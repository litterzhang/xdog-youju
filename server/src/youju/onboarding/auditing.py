"""Independent evidence validation, immutable audit outcomes and publication queries."""

from collections import Counter
from copy import deepcopy
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.material_contracts import capture_diagnostics
from youju.models import (
    AuditResult,
    AuditRun,
    FetchAttempt,
    FetchOutcome,
    PageAssessment,
    ParseResult,
    Position,
    RegistryStatus,
    Resource,
    ResourceBundle,
    ResourceScan,
    Source,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.notices import RoutingDecision
from youju.positions.extraction import validate_output
from youju.positions.facts import PositionFacts
from youju.positions.issues import COVERAGE_CODES, FIELD_ISSUE_CODES

from .classification_policy import needs_extraction, notice_candidate
from .store import digest, json_bytes, toolkit_key
from .toolkit import documents, material_issues

AUDIT_POLICY = "position-facts-admission-v1"
INCOMPLETE_BUNDLE = {
    "code": "INCOMPLETE_RESOURCE_BUNDLE",
    "message": "One or more materials are missing or partial",
}


def material_warnings(bundle):
    """Keep acquisition failures, their locations and completeness warnings together."""
    warnings = [*bundle.get("errors", []), *capture_diagnostics(bundle)]
    for doc in documents(bundle):
        warnings.extend(
            {**error, "url": doc["url"]} if doc.get("url") else error
            for error in doc.get("errors", [])
        )
    warnings.extend(
        {"code": "INCOMPLETE_MATERIAL", "message": issue} for issue in material_issues(bundle)
    )
    unique = []
    for warning in warnings:
        if warning not in unique:
            unique.append(deepcopy(warning))
    return unique


def split_audit_issues(bundle, issues):
    """Keep trusted acquisition diagnostics separate from extraction failures.

    Exact pinned input warnings never invalidate facts extracted from readable
    material. Candidate semantic/row/schema errors remain errors: they are not
    reclassified just because they have a familiar-looking code.
    """
    allowed = [
        *material_warnings(bundle),
        *([INCOMPLETE_BUNDLE] if bundle.get("status") != "complete" else []),
    ]
    expected = {json_bytes(issue) for issue in allowed}
    return (
        [issue for issue in issues if json_bytes(issue) in expected],
        [issue for issue in issues if json_bytes(issue) not in expected],
    )


def associated_change_issues(
    session, bundle_id, change_context=None, *, dependencies=None, disposition=None
):
    """Validate source-owned association identities, not extractor answer equality."""
    from .temporal_context import change_current, change_review

    saved = session.get(ResourceBundle, bundle_id)
    review = change_review(session, saved.resource_id) if saved else None
    receipt = dependencies.get("change") if isinstance(dependencies, dict) else None
    if review is None and not change_context and receipt is None:
        return []
    context = change_context if isinstance(change_context, dict) else {}
    event = session.get(ResourceBundle, review.event_bundle_id) if review else None
    valid = bool(
        review is not None
        and event is not None
        and change_current(session, review, bundle_id)
        and isinstance(dependencies, dict)
        and dependencies.get("contract") == "position-fact-dependencies-v1"
        and isinstance(receipt, dict)
        and all(
            receipt.get(key) == getattr(review, key)
            for key in ("review_id", "effect", "target_bundle_id", "event_bundle_id")
            if key != "review_id"
        )
        and receipt.get("review_id") == review.id
        and receipt.get("status") == "current"
        and receipt.get("event_version_id") == event.page_version_id
        and receipt.get("outcome")
        == ("cancelled" if review.effect == "cancellation" else "applied")
        and (disposition == "cancelled" if review.effect == "cancellation" else disposition is None)
    )
    if valid:
        return []
    return [
        {
            "code": "ASSOCIATED_CHANGE_UNRESOLVED",
            "message": (
                "Associated change is stale, unsupported or lacks matching fact dependencies"
            ),
            "scope": "domain",
            "owner": "position_domain",
            "repairable": False,
            "review_id": review.id if review else context.get("review_id"),
            "effect": review.effect if review else context.get("effect"),
            "association_status": (
                "current" if change_current(session, review, bundle_id) else "stale"
            )
            if review
            else context.get("status", "unverified"),
        }
    ]


def runtime_issues(result):
    """Check host execution receipts separately from position-domain invariants."""
    issues = []

    def invalid(message):
        issues.append({"code": "RUNTIME_RESULT_INVALID", "message": message})

    allowed = {
        "schema_version",
        "status",
        "positions",
        "errors",
        "coverage",
        "routing",
        "execution",
        "fact_dependencies",
        "disposition",
    }
    if set(result) - allowed:
        invalid("Unsupported execution result fields; old notice contracts are retired")
    routing = result.get("routing", {})
    try:
        if not isinstance(routing, dict) or set(routing) != {
            "category",
            "mode",
            "reason",
            "policy_version",
            "evaluated_at",
        }:
            raise ValueError("Expected routing metadata only")
        RoutingDecision.from_dict(routing)
    except (ValueError, TypeError, KeyError) as error:
        invalid(f"Invalid routing receipt: {error}")
    execution = result.get("execution", {})
    from youju.positions.corrections import valid_correction_receipts
    from youju.recipe_runtime.diagnostics import valid_stage_receipts

    from .repair_diagnostics import valid_failure_receipt

    required_execution = {"path", "model_used", "runtime_contract", "script_issues", "parser"}
    if (
        not isinstance(execution, dict)
        or not required_execution <= set(execution)
        or set(execution) - required_execution - {"failure", "stages", "corrections"}
        or ("failure" in execution and not valid_failure_receipt(execution["failure"]))
        or ("stages" in execution and not valid_stage_receipts(execution["stages"]))
        or (
            "corrections" in execution
            and (
                not valid_correction_receipts(execution["corrections"])
                or (execution["corrections"] and execution.get("model_used") is not True)
            )
        )
        or execution.get("path") not in ("quick", "detailed")
        or not (
            execution.get("parser") in {"parser.py", "undated_parser.py"}
            or (execution.get("parser") is None and result.get("status") == "cancelled")
        )
        or execution.get("runtime_contract") != "recipe-runtime-v3"
        or (
            type(execution.get("model_used")) is not bool
            and not (result.get("status") == "failed" and execution.get("model_used") is None)
        )
        or not isinstance(execution.get("script_issues"), list)
        or any(
            not isinstance(issue, dict)
            or not isinstance(issue.get("code"), str)
            or not isinstance(issue.get("message"), str)
            for issue in execution.get("script_issues", [])
        )
    ):
        invalid("Invalid execution receipt")
    elif (
        not isinstance(routing, dict)
        or execution["path"] != routing.get("mode")
        or (execution["path"] == "quick" and execution["model_used"])
    ):
        invalid("Execution does not match the cost route")
    return issues


def _metadata(result, name):
    value = result.get(name)
    return value if isinstance(value, dict) else {}


def position_field_issues(result, ordinal):
    """Select this normalized row's issues; unscoped/common uncertainty applies to all.

    Never apply an explicitly different row's dispute to this job. This only
    carries existing domain uncertainty, not new date extraction or routing facts.
    """
    prefix = f"positions[{ordinal - 1}]"
    return [
        issue
        for issue in result.get("errors", [])
        if issue.get("code") in FIELD_ISSUE_CODES
        and (
            not str(issue.get("path", "")).startswith("positions[")
            or str(issue["path"]) == prefix
            or str(issue["path"]).startswith(prefix + ".")
        )
    ]


def audit_dimensions(row, material_status):
    """Separate cost path, extraction coverage, acquisition and semantic failure."""
    result = row.result
    path = _metadata(result, "execution").get("path", "unknown")
    positions = bool(result.get("positions"))
    archived = (
        path == "quick"
        and not positions
        and not row.errors
        and row.status in {"success", "partial"}
    )
    coverage_issues = [
        e
        for e in result.get("errors", [])
        if e.get("code") in COVERAGE_CODES and e not in row.errors
    ]
    field_issues = [
        e
        for e in result.get("errors", [])
        if e.get("code") in FIELD_ISSUE_CODES and e not in row.errors
    ]
    return {
        "coverage": deepcopy(result.get("coverage", {})),
        "coverage_issues": coverage_issues,
        "field_issues": field_issues,
        "extraction_path": path,
        "routing_category": _metadata(result, "routing").get("category", "unknown"),
        "extraction_status": "empty_capture"
        if result.get("status") == "empty"
        else "parsed"
        if positions and not row.errors
        else "archived"
        if archived
        else "failed"
        if row.status == "failed"
        else "needs_review",
        "material_status": material_status,
        "review_required": bool(row.errors or field_issues) or row.status == "review",
        "repair_required": row.status == "failed",
        "warnings": [
            e
            for e in result.get("errors", [])
            if e not in row.errors and e not in coverage_issues and e not in field_issues
        ],
    }


def evaluation_day(stamp=None):
    return (stamp or utc_now()).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def admission_quality(results, announcement_count):
    """Valid rows count equally on either path; only zero-row quick work is archived.

    Intentionally incomplete/empty quick output can finish archive-only onboarding.
    A fatal/independent semantic error or missing receipt cannot authorize admission.
    Field uncertainty retains known rows without asserting full extraction coverage.
    """
    clean = [
        row
        for row in results
        if row["status"] in {"success", "partial", "cancelled"}
        and not row["errors"]
        and row.get("schema_version") == "position-facts-v3"
        and row.get("runtime_contract") == "recipe-runtime-v3"
    ]
    successful = [row for row in clean if row["position_count"] > 0]
    archived = [
        row
        for row in clean
        if row["position_count"] == 0
        and row["status"] in {"success", "partial"}
        and row.get("extraction_path") == "quick"
        and row.get("routing_category") in {"fixed_expired", "undated_low_priority"}
    ]
    withdrawn = [
        row
        for row in clean
        if row["status"] == "cancelled" and row.get("disposition") == "cancelled"
    ]
    archive_only = announcement_count > 0 and len(archived) + len(
        withdrawn
    ) == announcement_count == len(results)
    return successful, archived, archive_only


class AuditLedger:
    def __init__(self, store):
        self.store = store

    def cached(self, bundle_id, recipe_id, day, generation=0):
        with self.store.lock, Session(self.store.engine) as session:
            result = session.scalar(
                select(AuditResult).where(
                    AuditResult.bundle_id == bundle_id,
                    AuditResult.recipe_id == recipe_id,
                    AuditResult.evaluated_on == day,
                    AuditResult.generation == generation,
                )
            )
            from .semantic_labels import receipt
            from .temporal_context import current as temporal_current

            if result is not None and not temporal_current(session, result):
                raise ValueError("Temporal policy changed; use a new audit generation")
            check = receipt(session, result) if result is not None else None
            if check is not None and check["status"] == "recheck_required":
                # A new label is not permission to mutate an old audit in place.
                raise ValueError("Semantic expectations changed; use a new audit generation")
            return (
                self._summary(
                    result,
                    session.get(ResourceBundle, bundle_id).status,
                    semantic=receipt(session, result),
                )
                if result
                else None
            )

    def previous_result(self, bundle_id, recipe_id):
        with self.store.lock, Session(self.store.engine) as session:
            row = session.scalar(
                select(AuditResult)
                .where(
                    AuditResult.bundle_id == bundle_id,
                    AuditResult.recipe_id == recipe_id,
                    AuditResult.status == "success",
                )
                .order_by(AuditResult.evaluated_on.desc())
            )
            return row.result if row is not None else None

    @staticmethod
    def _summary(row, material_status=None, *, semantic=None):
        value = {
            "id": row.id,
            "bundle_id": row.bundle_id,
            "status": row.status,
            "errors": row.errors,
            "position_count": len(row.result.get("positions", [])),
            "schema_version": row.result.get("schema_version"),
            "runtime_contract": _metadata(row.result, "execution").get("runtime_contract"),
            "routing": deepcopy(row.result.get("routing", {})),
            **(
                {"failure": deepcopy(row.result["execution"]["failure"])}
                if _metadata(row.result, "execution").get("failure")
                else {}
            ),
            **(
                {"disposition": row.result["disposition"]}
                if row.result.get("disposition") is not None
                else {}
            ),
            **audit_dimensions(row, material_status),
            **({"semantic_validation": semantic} if semantic is not None else {}),
        }
        if semantic is not None and semantic["status"] in {"failed", "recheck_required"}:
            from .semantic_labels import errors_for

            value.update(status="review", extraction_status="needs_review", review_required=True)
            value["errors"] = list(row.errors)
            for issue in errors_for(semantic, row.bundle_id):
                if issue not in value["errors"]:
                    value["errors"].append(issue)
        return value

    def record(
        self,
        bundle,
        recipe_id,
        day,
        result,
        extra_errors=(),
        generation=0,
        *,
        failure_evidence=None,
    ):
        from .semantic_labels import assess, errors_for, latest_label, receipt, save_check

        semantic_input, not_evaluated = result, None
        if failure_evidence is not None:
            from .diagnostics import FAILURE_FORMAT

            binding = failure_evidence.get("bindings", {})
            if (
                failure_evidence.get("format") != FAILURE_FORMAT
                or binding.get("case_id") != bundle["id"]
                or binding.get("case_version") != digest(bundle)
                or binding.get("recipe_id") != recipe_id
            ):
                raise ValueError(
                    "Rejected output evidence must bind the audited material and recipe"
                )
            component = failure_evidence["rejected_output"]
            actual = component.get("value")
            try:
                if not component["available"] or component["truncated"]:
                    raise ValueError("No complete rejected output is available")
                if not isinstance(actual, dict) or not isinstance(actual.get("positions"), list):
                    raise ValueError("Rejected output has no position array")
                for item in actual["positions"]:
                    PositionFacts.from_dict(item)
                semantic_input = actual
            except (TypeError, ValueError):
                not_evaluated = "not_evaluated_execution_or_contract_failure"
        with self.store.lock, Session(self.store.engine) as session:
            label, semantic = assess(
                self.store,
                session,
                bundle["id"],
                semantic_input,
                not_evaluated_reason=not_evaluated,
            )
            label_id = label.id if label else None
            context = bundle.get("period_context") or {}
            empty_errors = []
            if result.get("status") == "empty":
                saved_bundle = session.get(ResourceBundle, bundle["id"])
                scan = session.get(ResourceScan, saved_bundle.resource_id) if saved_bundle else None
                attempt = (
                    session.get(FetchAttempt, scan.last_attempt_id)
                    if scan and scan.last_attempt_id
                    else None
                )
                if (
                    scan is None
                    or scan.bundle_id != bundle["id"]
                    or scan.mode != "dynamic"
                    or scan.state != "done"
                    or scan.last_version_id != saved_bundle.page_version_id
                    or attempt is None
                    or attempt.resource_id != saved_bundle.resource_id
                    or attempt.version_id != saved_bundle.page_version_id
                    or attempt.outcome not in {FetchOutcome.DOWNLOADED, FetchOutcome.NOT_MODIFIED}
                ):
                    empty_errors.append(
                        {
                            "code": "EMPTY_CAPTURE_NEEDS_RENDER",
                            "message": "An empty static capture needs an acquisition check",
                            "scope": "resource",
                            "owner": "acquisition",
                        }
                    )
            change_errors = associated_change_issues(
                session,
                bundle["id"],
                context.get("change") if isinstance(context, dict) else None,
                dependencies=result.get("fact_dependencies"),
                disposition=result.get("disposition"),
            )
        errors = [*errors_for(semantic, bundle["id"]), *change_errors, *empty_errors]
        if failure_evidence is None and semantic is not None and semantic["status"] == "failed":
            from .diagnostics import failure_record
            from .recipes import RecipeError

            with Session(self.store.engine) as session:
                artifact = session.get(SourceRecipe, recipe_id)
                artifact_ref = artifact.body_ref if artifact else None
            rejected = RecipeError(
                "SEMANTIC_EXPECTATION",
                "Independent semantic case rejected saved output",
                errors=errors,
                routing=result.get("routing"),
                rejected_result=result,
                rejected_origin="semantic_audit",
            )
            detail = failure_record(
                bundle,
                None,
                result.get("routing", {}).get("evaluated_at"),
                rejected,
                bindings={
                    "recipe_id": recipe_id,
                    "generation": generation,
                    "artifact_ref": artifact_ref,
                    "clock_basis": "saved_evaluation_or_unknown",
                },
            )
            reference = self.store.blobs.put(json_bytes(detail))
            errors = [
                {**issue, "failure_ref": reference, "rejected_output_available": True}
                for issue in errors
            ]
        domain_errors = validate_output(bundle, result)
        receipt_errors = runtime_issues(result)
        # Preserve reported semantic failures separately from acquisition warnings.
        for error in [
            *result.get("errors", []),
            *extra_errors,
            *material_warnings(bundle),
            *domain_errors,
            *receipt_errors,
        ]:
            if error not in errors:
                errors.append(error)
        if bundle["status"] != "complete":
            errors.append(dict(INCOMPLETE_BUNDLE))
        assessment = bundle.get("assessment", {})
        if assessment.get("requires_review"):
            errors.append(
                {
                    "code": "CLASSIFICATION_REVIEW",
                    "message": "Page classification is not sufficiently resolved for publication",
                }
            )
        if assessment.get("business_type") in {"correction", "cancellation"}:
            errors.append(
                {
                    "code": "ANNOUNCEMENT_ASSOCIATION_REVIEW",
                    "message": "Verify the affected original announcement before applying changes",
                }
            )
        warnings, errors = split_audit_issues(bundle, errors)
        field_issues, coverage_issues = [], []
        if (
            not domain_errors
            and not receipt_errors
            and result.get("status") in {"success", "partial", "empty", "review"}
        ):
            for issue in result.get("errors", []):
                # Explicit host/independent semantic failures cannot become field warnings.
                if (
                    issue in extra_errors
                    or issue.get("scope") == "source"
                    or issue.get("failure_code")
                    or issue.get("host_failure_envelope")
                ):
                    continue
                if issue.get("code") in FIELD_ISSUE_CODES:
                    field_issues.append(issue)
                elif issue.get("code") in COVERAGE_CODES:
                    coverage_issues.append(issue)
            errors = [e for e in errors if e not in field_issues and e not in coverage_issues]
        status = result.get("status", "failed")
        if errors:
            status = "failed" if status == "failed" else "review"
        elif result.get("positions") and status in {"success", "partial", "review"}:
            status = (
                "partial"
                if status != "success" or warnings or field_issues or coverage_issues
                else "success"
            )
        elif result.get("execution", {}).get("path") == "quick" and not field_issues:
            # Zero-row cost archives cover processing, not full extraction or expiry.
            if status in {"success", "partial", "review"}:
                status = "partial"
        if status not in {"success", "partial", "empty", "review", "cancelled", "failed"}:
            status = "failed"
        result = deepcopy(result)
        if failure_evidence is not None:
            # Bound failure provenance is application-owned, not a source/model
            # error-code assertion. Keep it distinct from partial fact validity.
            result["execution"] = {
                **result["execution"],
                "failure": {
                    "code": failure_evidence["error_code"],
                    "origin": failure_evidence.get("rejected_origin", "host_validation"),
                    "evidence_digest": digest(failure_evidence),
                },
            }
        if warnings and result.get("status") == "success":
            result["status"] = "partial"
        # The original extraction errors remain available. Host material warnings
        # are appended within the existing schema; AuditResult.errors is blocking only.
        for warning in warnings:
            if warning not in result["errors"]:
                result["errors"].append(deepcopy(warning))
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            old = session.scalar(
                select(AuditResult).where(
                    AuditResult.bundle_id == bundle["id"],
                    AuditResult.recipe_id == recipe_id,
                    AuditResult.evaluated_on == day,
                    AuditResult.generation == generation,
                )
            )
            current_label = latest_label(session, bundle["id"])
            if (current_label.id if current_label else None) != label_id:
                raise ValueError("Semantic expectations changed during audit")
            if old:
                from .temporal_context import current as temporal_current

                if not temporal_current(session, old):
                    raise ValueError("Temporal policy changed; use a new audit generation")
                if receipt(session, old) != semantic:
                    raise ValueError("Semantic expectations changed; use a new audit generation")
                return self._summary(old, bundle["status"], semantic=semantic)
            audit = AuditResult(
                bundle_id=bundle["id"],
                recipe_id=recipe_id,
                evaluated_on=day,
                generation=generation,
                status=status,
                result=result,
                errors=errors,
            )
            session.add(audit)
            if (
                not domain_errors
                and not receipt_errors
                and status in {"success", "partial", "review"}
                and result.get("positions")
            ):
                parse = ParseResult(
                    version_id=bundle["page"]["version_id"],
                    parser_key=digest(
                        {
                            "bundle": bundle["id"],
                            "recipe": recipe_id,
                            "day": day,
                            "generation": generation,
                            "validator": AUDIT_POLICY,
                        }
                    ),
                    status="valuable" if result.get("positions") else "review",
                    role="notice",
                    title=bundle["page"].get("title"),
                    explanation="source_workflow_tagged_facts_validated_not_eligibility_confirmed",
                )
                session.add(parse)
                session.flush()
                audit.parse_id = parse.id
                for ordinal, position in enumerate(result.get("positions", []), 1):
                    facts = PositionFacts.from_dict(position)
                    session.add(
                        Position(
                            parse_id=parse.id,
                            ordinal=ordinal,
                            facts=facts.as_dict(),
                            review_status="needs_review"
                            if errors
                            else "material_incomplete"
                            if warnings
                            else "needs_review",
                        )
                    )
            session.flush()
            # Material warnings are added to stored result after semantic matching;
            # the receipt always binds the final immutable audit bytes.
            save_check(session, audit, current_label, semantic)
            return self._summary(audit, bundle["status"], semantic=semantic)

    def reassess(self, source_id):
        """Revalidate saved current-day outputs in a new generation, without model/fetch work.

        The CLI holds the existing database process lock. Old audits and rows remain
        immutable; readers see the old generation until the final atomic pointer
        switch. An interrupted run can leave noncurrent records, never fake success.
        """
        day = evaluation_day()
        with self.store.lock:
            with Session(self.store.engine) as session:
                self.store.approved(session, source_id)
                pipeline = session.get(SourcePipeline, source_id)
                if pipeline is None or pipeline.stage != "active" or pipeline.state != "ready":
                    raise ValueError("Reassessment requires an active, ready source")
                if pipeline.lease_token or pipeline.lease_expires_at:
                    raise ValueError("A source operation is still leased")
                binding = session.get(SourceRecipeBinding, source_id)
                recipe = (
                    session.get(SourceRecipe, binding.active_recipe_id)
                    if binding and binding.active_recipe_id
                    else None
                )
                if (
                    recipe is None
                    or recipe.source_id != source_id
                    or recipe.status != "active"
                    or recipe.validation.get("errors")
                    or recipe.validation.get("toolkit_key") != toolkit_key()
                ):
                    raise ValueError("Toolkit/recipe changed; use source-reanalyze instead")
                recipe_id = recipe.id
                previous_generation = binding.generation
                prior_summary = deepcopy(pipeline.summary)
                key = prior_summary.get("classification", {}).get("classifier_key")
                if not key:
                    raise ValueError("A completed classifier profile is required")
                maximum = (
                    session.scalar(
                        select(func.max(AuditResult.generation))
                        .join(ResourceBundle)
                        .join(Resource)
                        .where(Resource.source_id == source_id)
                    )
                    or 0
                )
                generation = max(previous_generation, maximum) + 1
            queue = self.store.counts(source_id)
            if queue.get("ready", 0) or queue.get("running", 0):
                raise ValueError("Finish pending acquisitions before reassessment")
            bundles = self.store.bundles(source_id, classifier_key=key, summaries=True)
            if any(not bundle.get("assessment") for bundle in bundles):
                raise ValueError("Unassessed current pages remain")
            notices = [b for b in bundles if needs_extraction(b)]
            saved = []
            with Session(self.store.engine) as session:
                for bundle in notices:
                    row = session.scalar(
                        select(AuditResult).where(
                            AuditResult.bundle_id == bundle["id"],
                            AuditResult.recipe_id == recipe_id,
                            AuditResult.evaluated_on == day,
                            AuditResult.generation == previous_generation,
                        )
                    )
                    if row is None:
                        raise ValueError("Every current notice needs a current-day saved audit")
                    saved.append((bundle["id"], deepcopy(row.result), deepcopy(row.errors)))
            if not saved:
                raise ValueError("No current announcement audits to reassess")
            outcomes = []
            for bundle_id, result, old_errors in saved:
                bundle = self.store.load_bundle(bundle_id, classifier_key=key)
                # Re-run domain/schema and receipt validation in record(). No period
                # extractor, expiry oracle or acquisition-time substitution is involved.
                outcomes.append(self.record(bundle, recipe_id, day, result, old_errors, generation))
            complete, archived, archive_only = admission_quality(outcomes, len(notices))
            partial = [
                x
                for x in outcomes
                if x["status"] == "partial" and not x["errors"] and x["position_count"] > 0
            ]
            if (not complete and not archive_only) or any(
                e.get("scope") == "source" for x in outcomes for e in x["errors"]
            ):
                raise ValueError("Reassessment needs complete extraction and no source failures")
            counts = Counter(x["status"] for x in outcomes)
            summary = {
                key: deepcopy(value)
                for key, value in prior_summary.get("audit", {}).items()
                if key
                not in {
                    "quality_tiers",
                    "coarse_announcements",
                    "withdrawn_announcements",
                    "validity",
                }
            }
            summary.update(
                total_announcements=len(notices),
                processed=len(outcomes),
                remaining=0,
                statuses=dict(counts),
                routing_categories=dict(Counter(x["routing_category"] for x in outcomes)),
                extraction_paths=dict(Counter(x["extraction_path"] for x in outcomes)),
                field_issues_by_code=dict(
                    Counter(e["code"] for x in outcomes for e in x["field_issues"])
                ),
                coverage_issues_by_code=dict(
                    Counter(e["code"] for x in outcomes for e in x["coverage_issues"])
                ),
                errors_by_code=dict(Counter(e["code"] for x in outcomes for e in x["errors"])),
                warnings_by_code=dict(Counter(e["code"] for x in outcomes for e in x["warnings"])),
                extraction_statuses=dict(Counter(x["extraction_status"] for x in outcomes)),
                material_statuses=dict(Counter(x["material_status"] for x in outcomes)),
                position_count=sum(x["position_count"] for x in complete),
                partial_position_count=sum(x["position_count"] for x in partial),
                candidate_position_count=sum(x["position_count"] for x in outcomes),
                results=outcomes,
                evaluated_on=day,
                generation=generation,
                passed=True,
                model_calls=[],
                verification="saved-output-reassessment",
                audit_policy=AUDIT_POLICY,
                archived_announcements=len(archived),
                admission={
                    "policy": "quarantine-v1",
                    "mode": (
                        "associated_withdrawals_only"
                        if archive_only
                        and any(row.get("disposition") == "cancelled" for row in outcomes)
                        else "quick_archives_only"
                        if archive_only
                        else "facts_with_quarantine"
                    ),
                    "blocking_reasons": [],
                    "successful_announcements": len(complete),
                    "incomplete_material_announcements": [x["bundle_id"] for x in partial],
                    "quarantined_announcements": [
                        x["bundle_id"] for x in outcomes if x["status"] in {"review", "failed"}
                    ],
                    "review_required": counts["review"],
                    "repair_required": counts["failed"],
                    "source_errors_by_code": {},
                },
            )
            previous_ref = self.store.blobs.put(json_bytes(prior_summary))
            with Session(self.store.engine) as session, session.begin():
                pipeline = session.get(SourcePipeline, source_id)
                binding = session.get(SourceRecipeBinding, source_id)
                if (
                    evaluation_day() != day
                    or pipeline is None
                    or pipeline.stage != "active"
                    or pipeline.state != "ready"
                    or binding is None
                    or binding.active_recipe_id != recipe_id
                    or binding.generation != previous_generation
                ):
                    raise ValueError("Source/evaluation day changed during reassessment")
                audit = AuditRun(
                    source_id=source_id,
                    recipe_id=recipe_id,
                    kind="reassessment",
                    status="complete",
                    summary=summary,
                    finished_at=utc_now(),
                )
                session.add(audit)
                session.flush()
                binding.generation = generation
                binding.updated_at = utc_now()
                pipeline.summary = dict(
                    prior_summary,
                    audit=summary,
                    audit_id=audit.id,
                    reassessment={
                        "policy": AUDIT_POLICY,
                        "previous_generation": previous_generation,
                        "generation": generation,
                        "previous_summary_ref": previous_ref,
                        "network_calls": 0,
                        "model_calls": 0,
                    },
                )
                pipeline.updated_at = utc_now()
            return self.store.status(source_id)

    def begin(self, source_id, recipe_id, kind):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            old = session.scalar(
                select(AuditRun).where(
                    AuditRun.source_id == source_id,
                    AuditRun.recipe_id == recipe_id,
                    AuditRun.kind == kind,
                    AuditRun.status == "running",
                )
            )
            if old:
                return old.id
            run = AuditRun(source_id=source_id, recipe_id=recipe_id, kind=kind)
            session.add(run)
            session.flush()
            return run.id

    def finish(self, audit_id, summary, *, complete):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            row = session.get(AuditRun, audit_id)
            row.summary = summary
            if complete:
                row.status = "complete" if summary.get("passed") else "blocked"
                row.finished_at = utc_now()
            if row.kind == "incremental":
                from youju.models import IncrementalRun

                from .lifecycle import audit_projection

                run = session.scalar(
                    select(IncrementalRun).where(
                        IncrementalRun.source_id == row.source_id,
                        IncrementalRun.status == "running",
                    )
                )
                if run is not None:
                    run.audit_id = audit_id
                    run.summary = dict(run.summary, audit=audit_projection(summary))
            else:
                pipeline = session.get(SourcePipeline, row.source_id)
                pipeline.summary = dict(pipeline.summary, audit=summary, audit_id=audit_id)
                pipeline.updated_at = utc_now()


def current_source_positions(store, source_id, *, include_expired=False, include_partial=False):
    """No stale bundle, failed dependency, pending acquisition or unactivated recipe is current."""
    stamp = utc_now()
    day = evaluation_day(stamp)
    output = []
    with store.lock, Session(store.engine) as session:
        pipeline = session.get(SourcePipeline, source_id)
        binding = session.get(SourceRecipeBinding, source_id)
        if (
            pipeline is None
            or pipeline.stage != "active"
            or binding is None
            or binding.active_recipe_id is None
        ):
            return output
        recipe = session.get(SourceRecipe, binding.active_recipe_id)
        source = session.get(Source, source_id)
        if (
            source is None
            or source.status != RegistryStatus.ACTIVE
            or recipe is None
            or recipe.source_id != source_id
            or recipe.status != "active"
            or recipe.validation.get("errors")
            or recipe.validation.get("toolkit_key") != toolkit_key()
        ):
            return output
        statement = (
            select(Resource, ResourceScan, ResourceBundle, AuditResult)
            .join(ResourceScan, ResourceScan.resource_id == Resource.id)
            .join(ResourceBundle, ResourceBundle.id == ResourceScan.bundle_id)
            .join(AuditResult, AuditResult.bundle_id == ResourceBundle.id)
            .where(
                Resource.source_id == source_id,
                ResourceScan.state == "done",
                ResourceBundle.page_version_id == ResourceScan.last_version_id,
                AuditResult.recipe_id == recipe.id,
                AuditResult.status.in_(["success", "partial"]),
                AuditResult.evaluated_on == day,
                AuditResult.generation == binding.generation,
            )
        )
        from .semantic_labels import current as semantic_current

        for resource, scan, bundle, audit in session.execute(statement):
            from .temporal_context import current as temporal_current

            if associated_change_issues(
                session,
                bundle.id,
                dependencies=audit.result.get("fact_dependencies"),
                disposition=audit.result.get("disposition"),
            ):
                continue
            if not semantic_current(session, audit) or not temporal_current(session, audit):
                continue
            if audit.errors or audit.result.get("schema_version") != "position-facts-v3":
                continue
            if not include_partial and audit_dimensions(audit, bundle.status)["warnings"]:
                continue
            dependency_check = (
                _available_dependency_current if audit.status == "partial" else _dependency_current
            )
            if any(not dependency_check(session, dep) for dep in bundle.dependencies):
                continue
            assessment = session.scalar(
                select(PageAssessment)
                .where(PageAssessment.bundle_id == bundle.id)
                .where(
                    PageAssessment.classifier_key
                    == pipeline.summary.get(
                        "effective_classifier_key",
                        pipeline.summary.get("classification", {}).get("classifier_key"),
                    )
                )
                .order_by(PageAssessment.created_at.desc())
            )
            if (
                assessment is None
                or assessment.result.get("business_type") != "recruitment"
                or assessment.result.get("requires_review")
                or not notice_candidate(assessment.result)
            ):
                continue
            for position in session.scalars(
                select(Position).where(Position.parse_id == audit.parse_id)
            ):
                from youju.jobs import job_validity

                facts = PositionFacts.from_dict(position.facts)
                validity = job_validity(
                    facts,
                    stamp.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    now_iso=stamp.isoformat(),
                    field_issues=position_field_issues(audit.result, position.ordinal),
                )
                if validity["status"] == "expired" and not include_expired:
                    continue
                output.append(
                    {
                        "id": position.id,
                        "source_id": source_id,
                        "url": resource.normalized_url,
                        "bundle_id": bundle.id,
                        "recipe_id": recipe.id,
                        "facts": facts.as_dict(),
                        "evidence": position.evidence,
                        "validity": validity,
                        "review_status": position.review_status,
                        **audit_dimensions(audit, bundle.status),
                        "field_issues": position_field_issues(audit.result, position.ordinal),
                        "review_required": bool(
                            position_field_issues(audit.result, position.ordinal)
                        ),
                        "last_successful_check": scan.last_success_at.isoformat(),
                    }
                )
    return output


def _dependency_current(session, dependency):
    target = session.scalar(select(Resource).where(Resource.normalized_url == dependency["url"]))
    if target is None:
        return False
    scan = session.get(ResourceScan, target.id)
    return (
        scan is not None
        and scan.state == "done"
        and scan.last_document_id == dependency["document_id"]
    )


def _available_dependency_current(session, dependency):
    """Opt-in partial views still reject changed or currently pending dependencies."""
    if dependency.get("document_id") and not dependency.get("error"):
        return _dependency_current(session, dependency)
    target = session.scalar(select(Resource).where(Resource.normalized_url == dependency["url"]))
    scan = session.get(ResourceScan, target.id) if target is not None else None
    return scan is None or (scan.state == "review" and dependency.get("document_id") is None)
