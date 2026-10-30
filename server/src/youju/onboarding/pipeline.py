"""Baseline processing, frozen admission and opt-in incremental passes, backed by SQLite."""

import asyncio
import json
from collections import Counter
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import (
    AuditResult,
    AuditRun,
    Resource,
    ResourceScan,
    ScanPolicy,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.notices import decide_route

from .acquisition import AcquisitionWorker
from .auditing import AUDIT_POLICY, AuditLedger, admission_quality, evaluation_day
from .author_workspace import AuthorWorkspace
from .classification import Classifier, classifier_implementation_key
from .classification_budget import ClassificationBudget
from .classification_policy import notice_candidate
from .lifecycle import IncrementalLedger, freeze_onboarding
from .recipes import RecipeError, RecipeRunner
from .sandbox import Sandbox
from .store import digest, json_bytes, toolkit_key

# These failures concern one resource's output or processing budget. Unknown
# execution, graph, sandbox and provider/configuration failures remain blockers.
RESOURCE_FAILURE_CODES = {
    "RESULT_INVALID",
    "SCRIPT_PARSE_FAILED",
    "OUTPUT_SCHEMA",
    "UNKNOWN_EVIDENCE",
    "DRAFT_INVALID",
    "POSITION_V3_SCHEMA",
    "RECIPE_SIZE",
    "RECIPE_TIMEOUT",
    "MODEL_INPUT_LIMIT",
    "MODEL_OUTPUT_LIMIT",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TOKEN_LIMIT",
    "MODEL_TIMEOUT",
    "EMPTY_CAPTURE_UNVERIFIED",
}


class Pipeline:
    def __init__(
        self,
        store,
        *,
        models=None,
        sandbox=None,
        worker=None,
        concurrency=2,
        local_classifier=None,
        classification_llm_fraction=0.05,
        recipe_rpc=None,
        recipe_accuracy=False,
        classification_reviewer=None,
    ):
        if type(recipe_accuracy) is not bool:
            raise ValueError("Recipe accuracy opt-in must be boolean")
        if not 1 <= concurrency <= 8:
            raise ValueError("Invalid pipeline concurrency")
        self.classification_reviewer = classification_reviewer
        self.store, self.models = store, models
        self.recipe_rpc = recipe_rpc
        self.sandbox = sandbox or Sandbox()
        self.worker = worker or AcquisitionWorker(
            store, sandbox=self.sandbox, concurrency=concurrency
        )
        self.runner = RecipeRunner(self.sandbox, models)
        from .recipe_bridge import SourceRecipeCoordinator

        self.recipe_coordinator = SourceRecipeCoordinator(
            store,
            recipe_rpc,
            self.runner,
            classifier_key="",
            toolkit_key=self.toolkit_key(),
            accuracy=recipe_accuracy,
        )

        self.audits = AuditLedger(store)
        self.incrementals = IncrementalLedger(store)
        self.concurrency = concurrency
        self.classifier_key = digest(
            {
                "implementation": classifier_implementation_key(),
                "local_model": local_classifier.key if local_classifier else None,
                "models": {
                    key: models.metadata.get(key)
                    for key in ("sdk_version", "provider", "small_model", "large_model")
                }
                if models is not None
                else "rules-only",
                "llm_fraction": classification_llm_fraction,
            }
        )
        self.classification_budget = ClassificationBudget(
            store, self.classifier_key, classification_llm_fraction
        )
        self.recipe_coordinator.classifier_key = self.classifier_key
        self.classifier = Classifier(
            models, local_model=local_classifier, llm_budget=self.classification_budget.reserve
        )

    @staticmethod
    def toolkit_key():
        # Source-specific artifacts must be revalidated when shared business semantics change.
        return toolkit_key()

    async def step(self, source_id, *, limit=50):
        if not 1 <= limit <= 500:
            raise ValueError("Pass limit must be between 1 and 500")
        if self.models is not None:
            self.models.calls.clear()
        self.recipe_coordinator.rpc = self.recipe_rpc
        await self.recipe_coordinator.flush_review(source_id)
        state = await asyncio.to_thread(self.store.status, source_id)
        from .auto_repair import advance, pending

        self.recipe_coordinator.rpc = self.recipe_rpc
        await self.recipe_coordinator.flush_review(source_id)
        if await asyncio.to_thread(pending, self.store, source_id):
            await advance(self, source_id, limit=limit)
            return await asyncio.to_thread(self.store.status, source_id)
        if state["state"] == "blocked":
            return state
        if state["stage"] == "active":
            await self.recipe_coordinator.review_active(source_id)
            return await self.incremental(source_id, limit=limit, scheduled=True)
        if state["stage"] == "inventory":
            await self.worker.run_once(source_id, limit)
            await asyncio.to_thread(self.store.build_bundles, source_id)
            await asyncio.to_thread(self.store.finish_inventory, source_id)
        elif state["stage"] == "classification":
            remaining = await self._classify(source_id, limit)
            if not remaining:
                await asyncio.to_thread(self._advance_classification, source_id)
        elif state["stage"] == "authoring":
            await self._author(source_id)
        elif state["stage"] == "audit":
            replay = state["summary"].get("runtime_reparse", {})
            saved_only = (
                replay.get("reuse_saved_materials") is True
                and replay.get("recipe_id") == state.get("candidate_recipe_id")
                and replay.get("generation") == state["summary"].get("candidate_generation")
            )
            await self._audit(
                source_id, limit=limit, activate=True, retry_acquisition=not saved_only
            )
        return await asyncio.to_thread(self.store.status, source_id)

    async def _classify(self, source_id, limit):
        bundles = await asyncio.to_thread(
            self.store.bundles,
            source_id,
            classifier_key=self.classifier_key,
            unassessed=True,
            summaries=True,
        )
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(bundle):
            async with semaphore:
                bundle = await asyncio.to_thread(
                    self.store.load_bundle, bundle["id"], classifier_key=self.classifier_key
                )
                result = await self.classifier.classify(bundle)
                await asyncio.to_thread(self.store.assess, bundle, result, self.classifier_key)

        await asyncio.gather(*(one(bundle) for bundle in bundles[:limit]))
        remaining = max(0, len(bundles) - limit)
        if not remaining and self.classification_reviewer is not None:
            review = await self.classification_reviewer.run_once(source_id)
            # Budget exhaustion/abstention never creates a human gate or retries
            # a dispatched batch. Another pass handles remaining budgeted pages.
            remaining = int(review["can_continue"])
        await asyncio.to_thread(self.classification_budget.summarize, source_id)
        return remaining

    def _advance_classification(self, source_id):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            binding = session.get(SourceRecipeBinding, source_id)
            pipeline.stage = "audit" if binding.candidate_recipe_id else "authoring"
            pipeline.updated_at = utc_now()

    @staticmethod
    def _is_notice(bundle):
        assessment = bundle.get("assessment", {})
        return notice_candidate(assessment) and not bundle.get("associated_event_reviews")

    async def _author(self, source_id):
        from .recipe_client import RecipeRPCError

        try:
            await self.recipe_coordinator.advance_build(source_id)
        except RecipeRPCError as error:
            if not error.transient:
                with self.store.lock, Session(self.store.engine) as session, session.begin():
                    row = session.get(SourcePipeline, source_id)
                    row.lease_token = row.lease_expires_at = None
                self.block(source_id, error.code, "Recipe service needs review")

    def block(self, source_id, code, message, errors=None):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            pipeline.state = "blocked"
            pipeline.summary = dict(
                pipeline.summary, error={"code": code, "message": message, "details": errors or []}
            )
            pipeline.updated_at = utc_now()

    def _recipe(self, source_id, *, active=False):
        with self.store.lock, Session(self.store.engine) as session:
            binding = session.get(SourceRecipeBinding, source_id)
            row = session.get(
                SourceRecipe, binding.active_recipe_id if active else binding.candidate_recipe_id
            )
            if row is None:
                raise ValueError("No recipe is available")
            if (
                row.source_id != source_id
                or row.status not in ({"active"} if active else {"validated", "active"})
                or row.validation.get("errors")
            ):
                raise ValueError("The source recipe has not passed validation")
            if row.validation.get("toolkit_key") != self.toolkit_key():
                raise ValueError("Shared toolkit changed; regenerate and audit the source recipe")
            return row.id, json.loads(self.store.blobs.read(row.body_ref))

    async def _audit(self, source_id, *, limit, activate, retry_acquisition=True):
        if type(retry_acquisition) is not bool:
            raise ValueError("Acquisition retry policy requires an explicit boolean")
        recipe_id, candidate = await asyncio.to_thread(self._recipe, source_id, active=not activate)
        bundles = await asyncio.to_thread(
            self.store.bundles, source_id, classifier_key=self.classifier_key, summaries=True
        )
        notices = [bundle for bundle in bundles if self._is_notice(bundle)]
        stamp = utc_now()
        status = await asyncio.to_thread(self.store.status, source_id)
        generation = (
            status["summary"].get("candidate_generation", status["binding"]["generation"] + 1)
            if activate
            else status["summary"].get("incremental_generation", status["binding"]["generation"])
        )
        day, audit_id = (
            evaluation_day(stamp),
            await asyncio.to_thread(
                self.audits.begin, source_id, recipe_id, "onboarding" if activate else "incremental"
            ),
        )
        results, pending = [], []
        for bundle in notices:
            cached = await asyncio.to_thread(
                self.audits.cached, bundle["id"], recipe_id, day, generation
            )
            if cached:
                results.append(cached)
            else:
                pending.append(bundle)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(bundle):
            async with semaphore:
                bundle = await asyncio.to_thread(
                    self.store.load_bundle, bundle["id"], classifier_key=self.classifier_key
                )
                errors, failure_evidence = [], None
                routing = decide_route(bundle, bundle.get("assessment"), now=stamp).as_dict()
                try:
                    previous = await asyncio.to_thread(
                        self.audits.previous_result, bundle["id"], recipe_id
                    )
                    result = await self.runner.run(
                        candidate,
                        bundle,
                        now_iso=stamp.isoformat(),
                        routing=routing,
                        previous_result=previous,
                    )
                except RecipeError as error:
                    from .diagnostics import failure_record
                    from .recipes import candidate_digest

                    failure_evidence = failure_record(
                        bundle,
                        candidate_digest(candidate),
                        stamp.isoformat(),
                        error,
                        bindings={"recipe_id": recipe_id, "generation": generation},
                    )
                    reference = self.store.blobs.put(json_bytes(failure_evidence))
                    errors = [
                        {
                            **detail,
                            "failure_code": error.code,
                            "failure_ref": reference,
                            "rejected_output_available": error.rejected_result is not None,
                            "host_failure_envelope": True,
                            "scope": "resource"
                            if error.code in RESOURCE_FAILURE_CODES
                            else "source",
                        }
                        for detail in error.errors
                    ]
                    if error.partial_result is not None:
                        from .diagnostics import failed_result

                        result, errors = failed_result(error, failure_ref=reference)
                    else:
                        result = {
                            "schema_version": "position-facts-v3",
                            "status": "failed",
                            "positions": [],
                            "errors": errors,
                            "coverage": {
                                "supported": False,
                                "complete": False,
                                "needs_detailed": True,
                            },
                            "routing": routing,
                            "execution": {
                                "path": routing["mode"],
                                "model_used": None,
                                "runtime_contract": "recipe-runtime-v3",
                                "parser": "undated_parser.py"
                                if routing["category"]
                                in {"undated_low_priority", "undated_high_priority"}
                                else "parser.py",
                                "script_issues": [],
                            },
                        }
                return await asyncio.to_thread(
                    self.audits.record,
                    bundle,
                    recipe_id,
                    day,
                    result,
                    errors,
                    generation,
                    failure_evidence=failure_evidence,
                )

        results.extend(await asyncio.gather(*(one(bundle) for bundle in pending[:limit])))
        complete = len(results) == len(notices)
        counts = Counter(item["status"] for item in results)
        errors = Counter(error["code"] for item in results for error in item["errors"])
        warnings = Counter(w["code"] for item in results for w in item.get("warnings", []))
        partial = [
            item
            for item in results
            if item["status"] == "partial" and not item["errors"] and item["position_count"] > 0
        ]
        unresolved = [
            bundle["id"]
            for bundle in bundles
            if (
                not bundle.get("assessment")
                or bundle["assessment"].get("requires_review")
                or "unknown" in bundle["assessment"].get("page_roles", [])
            )
        ]
        unassessed = [bundle["id"] for bundle in bundles if not bundle.get("assessment")]
        scan_counts = await asyncio.to_thread(self.store.counts, source_id)
        failed_fetches = scan_counts.get("review", 0)
        waiting_fetches = scan_counts.get("ready", 0) + scan_counts.get("running", 0)
        coverage_incomplete = (await asyncio.to_thread(self.store.status, source_id))[
            "summary"
        ].get("inventory_incomplete", False)
        successful, archived, archive_only = admission_quality(results, len(notices))
        quarantined = [
            item["bundle_id"]
            for item in results
            if item["status"] not in {"success", "partial", "empty", "cancelled"} or item["errors"]
        ]
        source_errors = Counter(
            code
            for item in results
            for code in {
                error.get("failure_code", error["code"])
                for error in item["errors"]
                if error.get("scope") == "source"
            }
        )
        blockers = [
            code
            for code, blocked in (
                ("INVENTORY_INCOMPLETE", coverage_incomplete),
                ("ACQUISITIONS_PENDING", bool(waiting_fetches)),
                ("CLASSIFICATION_PENDING", bool(unassessed)),
                ("AUDIT_PENDING", not complete),
                ("NO_ANNOUNCEMENTS", not notices),
                ("NO_SUCCESSFUL_EXTRACTION", not successful and not archive_only),
                ("SOURCE_EXECUTION_FAILURE", bool(source_errors)),
            )
            if blocked
        ]
        repair_check = None
        passed = not blockers
        summary = {
            "audit_policy": AUDIT_POLICY,
            "acquisition_retries_enabled": retry_acquisition,
            "classifier_key": self.classifier_key,
            "repair_verification": repair_check,
            "admission": {
                "policy": "quarantine-v1",
                "mode": (
                    "associated_withdrawals_only"
                    if archive_only
                    and any(row.get("disposition") == "cancelled" for row in results)
                    else "quick_archives_only"
                    if archive_only
                    else "facts_with_quarantine"
                ),
                "blocking_reasons": blockers,
                "successful_announcements": len(successful),
                "quarantined_announcements": quarantined,
                "incomplete_material_announcements": [item["bundle_id"] for item in partial],
                "review_required": counts["review"],
                "repair_required": counts["failed"],
                "source_errors_by_code": dict(source_errors),
            },
            "total_announcements": len(notices),
            "associated_event_count": sum(bool(b.get("associated_event_reviews")) for b in bundles),
            "processed": len(results),
            "remaining": len(notices) - len(results),
            "statuses": dict(counts),
            "routing_categories": dict(Counter(item["routing_category"] for item in results)),
            "extraction_paths": dict(Counter(item["extraction_path"] for item in results)),
            "archived_announcements": len(archived),
            "field_issues_by_code": dict(
                Counter(issue["code"] for item in results for issue in item.get("field_issues", []))
            ),
            "coverage_issues_by_code": dict(
                Counter(
                    issue["code"] for item in results for issue in item.get("coverage_issues", [])
                )
            ),
            "errors_by_code": dict(errors),
            "warnings_by_code": dict(warnings),
            "extraction_statuses": dict(Counter(item["extraction_status"] for item in results)),
            "material_statuses": dict(Counter(item["material_status"] for item in results)),
            "position_count": sum(item["position_count"] for item in successful),
            "candidate_position_count": sum(item["position_count"] for item in results),
            "partial_position_count": sum(item["position_count"] for item in partial),
            "classification_review": unresolved,
            "classification_pending": unassessed,
            "classification_by_business": dict(
                Counter(
                    bundle.get("assessment", {}).get("business_type", "unassessed")
                    for bundle in bundles
                )
            ),
            "failed_acquisitions": failed_fetches,
            "waiting_acquisitions": waiting_fetches,
            "coverage_incomplete": coverage_incomplete,
            "formats": dict(
                Counter(
                    doc.get("format", "unknown")
                    for bundle in notices
                    for doc in [bundle["page"], *bundle["attachments"]]
                )
            ),
            "templates": dict(
                Counter(
                    bundle.get("assessment", {}).get("template_key") or "unknown"
                    for bundle in notices
                )
            ),
            "evaluated_on": day,
            "generation": generation,
            "results": results,
            "passed": passed,
            "model_calls": self.models.calls if self.models is not None else [],
            "verification": "stubbed-model"
            if self.models is not None and self.models.stub is not None
            else "live-model"
            if self.models is not None and self.models.config.live_enabled
            else "scripts-only",
        }
        await asyncio.to_thread(self.audits.finish, audit_id, summary, complete=complete)

        if not activate and complete and passed and generation != status["binding"]["generation"]:
            with self.store.lock, Session(self.store.engine) as session, session.begin():
                binding = session.get(SourceRecipeBinding, source_id)
                row = session.get(SourcePipeline, source_id)
                if (
                    binding.active_recipe_id != recipe_id
                    or binding.generation != status["binding"]["generation"]
                ):
                    raise ValueError("Source binding changed during saved-output retry")
                binding.generation = generation
                row.summary = {
                    key: value
                    for key, value in row.summary.items()
                    if key != "incremental_generation"
                }
        if complete:
            from .repair import audit_feedback

            feedback = await asyncio.to_thread(audit_feedback, self.store, source_id, audit_id)
            workspace = AuthorWorkspace(
                self.store.blobs.root / "authoring" / digest({"source_id": source_id}),
                sandbox=self.sandbox,
            )
            await asyncio.to_thread(workspace.record_audit, feedback)
            if repair_check and not repair_check["passed"]:
                await asyncio.to_thread(
                    self.block,
                    source_id,
                    "REPAIR_AUDIT_REGRESSION",
                    "Repair regressed parsed material or did not fix selected failures",
                )
                return summary
            if source_errors:
                if activate:
                    await asyncio.to_thread(
                        self.block,
                        source_id,
                        "SOURCE_EXECUTION_FAILURE",
                        "Recipe, sandbox or model execution needs repair before further scans",
                        errors=[
                            {"code": code, "resources": count}
                            for code, count in source_errors.items()
                        ],
                    )
                else:
                    await asyncio.to_thread(
                        self.incrementals.block, source_id, "SOURCE_EXECUTION_FAILURE"
                    )
            elif activate and passed:
                await asyncio.to_thread(self._activate, source_id, recipe_id)
            if not source_errors:
                # Activate good results first; retries must not restart onboarding.
                retry_ids = set()
                for item in results:
                    if item["status"] not in {"review", "failed"} or any(
                        error["code"]
                        in {
                            "MODEL_DISABLED",
                            "ANNOUNCEMENT_ASSOCIATION_REVIEW",
                            "ASSOCIATED_CHANGE_UNRESOLVED",
                        }
                        for error in item["errors"]
                    ):
                        continue
                    retry_bundle = await asyncio.to_thread(
                        self.store.load_bundle,
                        item["bundle_id"],
                        classifier_key=self.classifier_key,
                    )
                    if self._needs_parent_render(retry_bundle, item):
                        retry_ids.add(item["bundle_id"])
                queued = (
                    await asyncio.to_thread(self._render_retries, source_id, retry_ids)
                    if retry_acquisition
                    else 0
                )
                if activate and not passed and not queued:
                    await asyncio.to_thread(
                        self.block,
                        source_id,
                        "AUDIT_NOT_READY",
                        "Source admission failed; inspect audit.admission.blocking_reasons",
                    )
        if not activate and coverage_incomplete:
            await asyncio.to_thread(
                self.incrementals.block,
                source_id,
                "INVENTORY_INCOMPLETE",
            )
        if (
            activate
            and complete
            and not passed
            and self.store.status(source_id)["state"] == "blocked"
        ):
            await self.recipe_coordinator.reject_candidate(source_id, audit_id)
        return summary

    @staticmethod
    def _needs_parent_render(bundle, audit):
        """Exclude only proved policy-only attachment gaps with validated candidate rows.

        Unknown fetch failures, empty extraction, bad evidence and incomplete parent
        pages keep the existing retry behavior. Rendering cannot authorize an attachment.
        """
        denied = {"attachment_outside_approved_scope", "policy_blocked"}
        from .auditing import split_audit_issues
        from .repair_diagnostics import ADAPTER_CODES, valid_failure_receipt

        failure = audit.get("failure")
        page = bundle.get("page", {})
        if (
            valid_failure_receipt(failure)
            and (
                failure["code"].startswith("MODEL_")
                or failure["code"]
                in ADAPTER_CODES | {"RECIPE_TIMEOUT", "SCRIPT_RUNTIME_UNAVAILABLE"}
            )
            and page.get("status") == "ok"
            and not page.get("errors")
            and not page.get("truncated")
        ):
            return (
                False  # Inference/code failure is not evidence that HTML must be downloaded again.
            )

        _, blocking = split_audit_issues(bundle, audit["errors"])
        if (
            audit["status"] not in {"partial", "review"}
            or not audit["position_count"]
            or blocking
            or bundle.get("status") != "partial"
        ):
            return True
        page = bundle.get("page", {})
        if page.get("status") != "ok" or page.get("errors") or page.get("truncated"):
            return True
        attachments = bundle.get("attachments", [])
        urls = {doc.get("url") for doc in attachments}
        if any(
            link.get("is_attachment") and link.get("url") not in urls
            for link in page.get("links", [])
        ):
            return True
        missing = [
            doc
            for doc in attachments
            if doc.get("status") != "ok" or doc.get("errors") or doc.get("truncated")
        ]
        if not missing or any(error["code"] not in denied for error in bundle.get("errors", [])):
            return True
        return any(
            doc.get("status") != "failed"
            or doc.get("blocks")
            or doc.get("tables")
            or doc.get("truncated")
            or not doc.get("errors")
            or any(error["code"] not in denied for error in doc["errors"])
            for doc in missing
        )

    def _render_retries(self, source_id, bundle_ids):
        count = 0
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            scans = session.scalars(
                select(ResourceScan)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    ResourceScan.bundle_id.in_(bundle_ids),
                    ResourceScan.mode == "static",
                    ResourceScan.state == "done",
                )
            )
            for scan in scans:
                scan.mode, scan.state = "dynamic", "ready"
                scan.available_at = utc_now() + timedelta(seconds=pipeline.config["retry_delay"])
                scan.last_error = "no_valuable_extraction"
                count += 1
            if count and pipeline.stage != "active":
                pipeline.stage, pipeline.state = "inventory", "ready"
        return count

    def _activate(self, source_id, recipe_id):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            self.store.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            binding = session.get(SourceRecipeBinding, source_id)
            if pipeline.stage != "audit" or binding.candidate_recipe_id != recipe_id:
                raise ValueError("Activation candidate changed")
            recipe = session.get(SourceRecipe, recipe_id)
            audit_id = pipeline.summary.get("audit_id")
            audit = session.get(AuditRun, audit_id) if audit_id else None
            if (
                recipe is None
                or recipe.source_id != source_id
                or recipe.status not in {"validated", "active"}
                or recipe.validation.get("errors")
                or recipe.validation.get("toolkit_key") != self.toolkit_key()
                or audit is None
                or audit.source_id != source_id
                or audit.recipe_id != recipe_id
                or audit.kind != "onboarding"
                or audit.status != "complete"
                or audit.finished_at is None
                or not audit.summary.get("passed")
                or audit.summary.get("admission", {}).get("policy") != "quarantine-v1"
                or audit.summary.get("generation")
                != pipeline.summary.get("candidate_generation", binding.generation + 1)
            ):
                raise ValueError(
                    "Activation requires a validated recipe and completed admission audit"
                )
            from .semantic_labels import current as semantic_current
            from .temporal_context import current as temporal_current

            for entry in audit.summary.get("results", []):
                checked = session.get(AuditResult, entry["id"])
                if (
                    checked is None
                    or checked.recipe_id != recipe_id
                    or checked.generation != audit.summary["generation"]
                    or not semantic_current(session, checked)
                    or not temporal_current(session, checked)
                ):
                    raise ValueError("Independent semantic expectations changed before activation")
            reviewed = pipeline.summary.get("revision_review", {}).get("intent", {})
            if (
                binding.candidate_revision_id
                and reviewed.get("revision_id") == binding.candidate_revision_id
                and reviewed.get("decision") == "reject"
            ):
                raise ValueError(
                    "A rejected candidate requires a new recipe revision before adoption"
                )
            previous_recipe = (
                session.get(SourceRecipe, binding.active_recipe_id)
                if binding.active_recipe_id
                else None
            )
            # Shared-runtime re-audit is not a new source-authored revision.
            # Service revision reviews are terminal: preserve the earlier owner
            # decision instead of submitting a second, conflicting acceptance.
            from .recipe_versions import same_source_revision

            same_revision = same_source_revision(session, binding)
            recipe.status = "active"
            if binding.active_recipe_id and binding.active_recipe_id != recipe_id:
                previous_recipe.status = "retired"
            binding.active_recipe_id = recipe_id
            binding.active_revision_id = binding.candidate_revision_id
            binding.candidate_recipe_id = binding.candidate_revision_id = None
            pipeline.summary = dict(pipeline.summary, effective_classifier_key=self.classifier_key)
            binding.generation = audit.summary["generation"]
            binding.updated_at = utc_now()
            if binding.active_revision_id and not same_revision:
                from .recipe_versions import queue_review

                queue_review(
                    session,
                    source_id,
                    workspace_id=binding.workspace_id,
                    revision_id=binding.active_revision_id,
                    decision="accept",
                    feedback={"audit_id": audit.id, "passed": True, "policy": "quarantine-v1"},
                    request_id="adopt-" + binding.active_revision_id,
                )

            pipeline.stage, pipeline.state = "active", "ready"
            pipeline.summary = {
                key: value for key, value in pipeline.summary.items() if key != "error"
            }
            pipeline.activated_at, pipeline.updated_at = utc_now(), utc_now()
            replay = pipeline.summary.get("runtime_reparse", {})
            if (
                replay.get("recipe_id") == recipe_id
                and replay.get("generation") == binding.generation
            ):
                pipeline.summary = dict(
                    pipeline.summary,
                    runtime_reparse={
                        **replay,
                        "status": "complete",
                        "audit_id": audit.id,
                        "completed_at": pipeline.updated_at.isoformat(),
                    },
                )
            freeze_onboarding(session, pipeline, audit)
            # Policies are based on last successful check, not activation time.
            for policy in session.scalars(
                select(ScanPolicy).join(Resource).where(Resource.source_id == source_id)
            ):
                scan = session.get(ResourceScan, policy.resource_id)
                policy.next_scan_at = self.store.next_scan_at(scan.last_success_at, policy.cadence)

    async def incremental(self, source_id, *, limit=50, scheduled=False):
        if not 1 <= limit <= 500:
            raise ValueError("Pass limit must be between 1 and 500")
        if self.models is not None:
            self.models.calls.clear()
        from .auto_repair import advance, pending

        self.recipe_coordinator.rpc = self.recipe_rpc
        await self.recipe_coordinator.flush_review(source_id)
        if await asyncio.to_thread(pending, self.store, source_id):
            await advance(self, source_id, limit=limit)
            return await asyncio.to_thread(self.store.status, source_id)
        run_id = await asyncio.to_thread(self.incrementals.begin, source_id, scheduled=scheduled)
        if run_id is None:
            return await asyncio.to_thread(self.store.status, source_id)
        try:
            queued = await asyncio.to_thread(self.store.queue_due, source_id)
            acquisition = await self.worker.run_once(source_id, limit)
            await asyncio.to_thread(self.store.build_bundles, source_id)
            remaining = await self._classify(source_id, limit)
            # Do not audit stale bundles while acquisitions or deferred rendering are pending.
            counts = await asyncio.to_thread(self.store.counts, source_id)
            complete = False
            if not remaining and not (counts.get("ready", 0) + counts.get("running", 0)):
                summary = await self._audit(source_id, limit=limit, activate=False)
                counts = await asyncio.to_thread(self.store.counts, source_id)
                complete = not summary["remaining"] and not (
                    counts.get("ready", 0) + counts.get("running", 0)
                )
            if complete:
                from .auto_repair import enqueue

                await asyncio.to_thread(enqueue, self, run_id, summary)
            await asyncio.to_thread(
                self.incrementals.update, run_id, queued=queued, complete=complete
            )
        except Exception:
            await asyncio.to_thread(self.incrementals.block, source_id, "INCREMENTAL_WORKER_FAILED")
            raise
        status = await asyncio.to_thread(self.store.status, source_id)
        return status | {"queued_due": queued, "pass": acquisition}
