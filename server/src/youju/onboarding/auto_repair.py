"""Durable incremental repair dispatch and isolated candidate regression auditing.

Only the Recipe Agent edits source files. Acquisition/policy/provider failures are
not invitations to change permissions. Active admission stays untouched until a
saved-material audit passes and the source/material/profile fences still match.
"""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.models import (
    AuditResult,
    AuditRun,
    IncrementalControl,
    IncrementalRun,
    RepairIncident,
    RepairRequest,
    Resource,
    ResourceBundle,
    ResourceScan,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now

from .auditing import AUDIT_POLICY, evaluation_day
from .classification_policy import needs_extraction, notice_candidate
from .recipe_bridge import prepare_input
from .recipe_client import RecipeRPCError
from .recipes import RecipeError, candidate_digest
from .repair import protectable_facts, repair_verdict, select_repair_samples

# Deliberately excludes budgets, timeouts, provider/sandbox failures, acquisition
# gaps, and classification/association review. Extend only with explicit tests.
from .repair_diagnostics import ADAPTER_CODES, diagnose_row, routing_for
from .store import digest, json_bytes

REPAIRABLE = ADAPTER_CODES | {"SEMANTIC_EXPECTATION"}

ACTIVE = ("queued", "preparing", "submitted", "auditing", "accepting", "rejecting")


def pending(store, source_id):
    with store.lock, Session(store.engine) as session:
        return (
            session.scalar(
                select(RepairRequest.id)
                .where(
                    RepairRequest.source_id == source_id,
                    RepairRequest.status.in_(ACTIVE),
                )
                .limit(1)
            )
            is not None
        )


def enqueue(pipeline, run_id, summary):
    """Coalesce new/changed failed notices; one incident per immutable input/base."""
    store = pipeline.store
    with store.lock, Session(store.engine) as session, session.begin():
        run = session.get(IncrementalRun, run_id)
        binding = session.get(SourceRecipeBinding, run.source_id)
        recipe = session.get(SourceRecipe, binding.active_recipe_id)
        if recipe is None:
            return []
        base = candidate_digest(json.loads(store.blobs.read(recipe.body_ref)))
        request = session.scalar(
            select(RepairRequest)
            .where(
                RepairRequest.source_id == run.source_id,
                RepairRequest.base_recipe_id == recipe.id,
                RepairRequest.classifier_key == pipeline.classifier_key,
                RepairRequest.toolkit_key == pipeline.toolkit_key(),
                RepairRequest.status == "queued",
            )
            .order_by(RepairRequest.created_at)
        )
        touched = set()
        for item in summary.get("results", []):
            if item["status"] not in {"failed", "review"}:
                continue
            row = session.get(AuditResult, item["id"])
            if row is None or not diagnose_row(session, row)["repairable"]:
                continue
            bundle_row = session.get(ResourceBundle, row.bundle_id)
            resource = session.get(Resource, bundle_row.resource_id)
            scan = session.get(ResourceScan, resource.id)
            if (
                resource.source_id != run.source_id
                or row.recipe_id != recipe.id
                or row.generation != binding.generation
                or scan is None
                or scan.bundle_id != row.bundle_id
            ):
                continue
            before = run.baseline.get(resource.id)
            if before is not None and before.get("bundle") == bundle_row.id:
                continue  # Today's re-audit of an unchanged historical failure is not incremental.
            bundle = store.load_bundle(bundle_row.id, classifier_key=pipeline.classifier_key)
            assessment = bundle.get("assessment", {})
            if (
                bundle["source_id"] != run.source_id
                or not notice_candidate(assessment)
                or assessment.get("business_type") != "recruitment"
                or assessment.get("requires_review", True)
                or routing_for(bundle, utc_now().isoformat())["mode"] == "quick"
            ):
                continue
            issue = {
                "bundle_id": bundle_row.id,
                "url": bundle["url"],
                "audit_status": row.status,
                "audit_result_id": row.id,
                "routing": deepcopy(row.result.get("routing", {})),
                "errors": deepcopy(row.errors),
            }
            try:
                select_repair_samples(
                    store, run.source_id, pipeline.classifier_key, {"issues": [issue]}
                )
            except ValueError:
                continue  # Unreadable material belongs to acquisition, not recipe repair.
            key = digest(
                {
                    "source": run.source_id,
                    "bundle": bundle_row.digest,
                    "recipe": base,
                    "classifier": pipeline.classifier_key,
                    "toolkit": pipeline.toolkit_key(),
                }
            )
            if session.scalar(select(RepairIncident.id).where(RepairIncident.fingerprint == key)):
                continue  # Terminal failures do not spin up another paid Agent automatically.
            if request is None:
                request = RepairRequest(
                    source_id=run.source_id,
                    base_recipe_id=recipe.id,
                    base_recipe_digest=base,
                    classifier_key=pipeline.classifier_key,
                    toolkit_key=pipeline.toolkit_key(),
                )
                session.add(request)
                session.flush()
            session.add(
                RepairIncident(
                    fingerprint=key,
                    request_id=request.id,
                    incremental_run_id=run_id,
                    bundle_id=bundle_row.id,
                    audit_result_id=row.id,
                )
            )
            touched.add(request.id)
        run.summary = dict(
            run.summary,
            repair_request_ids=sorted(
                set(run.summary.get("repair_request_ids", [])) | touched,
            ),
        )
        return sorted(touched)


def request_json(row):
    return {
        "id": row.id,
        "origin": row.origin,
        "status": row.status,
        "base_recipe_id": row.base_recipe_id,
        "candidate_recipe_id": row.candidate_recipe_id,
        "task_id": row.context.get("task_id"),
        "error_code": row.error_code,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _save(store, request_id, *, status=None, error=None, context=None, **fields):
    with store.lock, Session(store.engine) as session, session.begin():
        row = session.get(RepairRequest, request_id)
        if status:
            row.status = status
        row.error_code = error
        if context is not None:
            row.context = context
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = utc_now()


def _freeze(pipeline, row):
    """Pin the entire current notice set and regression floors before RPC dispatch."""
    store = pipeline.store
    bundles = store.bundles(row.source_id, classifier_key=row.classifier_key, summaries=True)
    ids = sorted(b["id"] for b in bundles if needs_extraction(b))
    with store.lock, Session(store.engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, row.source_id)
        generation = binding.generation
        recipe = session.get(SourceRecipe, row.base_recipe_id)
        prior = json.loads(store.blobs.read(row.feedback_ref)) if row.feedback_ref else {}
        issues = list(prior.get("issues", []))
        for incident in session.scalars(
            select(RepairIncident).where(
                RepairIncident.request_id == row.id,
            )
        ):
            result = session.get(AuditResult, incident.audit_result_id)
            from .semantic_labels import errors_for, expose, latest_label, receipt

            semantic = receipt(session, result)
            label = latest_label(session, result.bundle_id)
            if label is not None:
                expose(session, label, "repair-" + row.id)
            details = [*result.errors]
            for error in errors_for(semantic, result.bundle_id) if semantic else []:
                if error not in details:
                    details.append(error)
            issues.append(
                {
                    "bundle_id": result.bundle_id,
                    "audit_status": result.status
                    if result.status in {"failed", "review"}
                    else "review",
                    "audit_result_id": result.id,
                    "repair_diagnosis": diagnose_row(session, result),
                    "errors": details,
                    "routing": result.result.get("routing", {}),
                    "execution": result.result.get("execution", {}),
                }
            )
        base_revision_id, baseline_recipe_id = recipe.revision_id, binding.active_recipe_id
        base_recipe = json.loads(store.blobs.read(recipe.body_ref))
        floors = dict(prior.get("baseline_position_counts", {}))
        semantic_baselines = dict(prior.get("baseline_semantics", {}))
        for result in session.scalars(
            select(AuditResult).where(
                AuditResult.bundle_id.in_(ids),
                AuditResult.recipe_id == row.base_recipe_id,
                AuditResult.generation == generation,
                AuditResult.status.in_(["success", "partial", "review"]),
            )
        ):
            if protectable_facts(session, result):
                semantic_baselines[result.bundle_id] = store.blobs.put(
                    json_bytes({"bundle_id": result.bundle_id, "result": result.result})
                )
                floors[result.bundle_id] = max(
                    floors.get(result.bundle_id, 0),
                    len(result.result["positions"]),
                )
        maximum = (
            session.scalar(
                select(func.max(AuditResult.generation))
                .join(
                    ResourceBundle,
                    ResourceBundle.id == AuditResult.bundle_id,
                )
                .join(Resource)
                .where(Resource.source_id == row.source_id)
            )
            or 0
        )
    feedback = {
        **prior,
        "base_recipe": base_recipe,
        "origin": row.origin,
        "source_id": row.source_id,
        "repair_request_id": row.id,
        "recipe_digest": row.base_recipe_digest,
        "recipe_id": row.base_recipe_id,
        "issues": issues,
        "baseline_parsed": sorted(floors),
        "baseline_position_counts": floors,
        "baseline_semantics": semantic_baselines,
    }
    if not {issue["bundle_id"] for issue in issues} <= set(ids):
        raise ValueError("Failed materials are no longer current")
    samples = select_repair_samples(store, row.source_id, row.classifier_key, feedback)
    # Every reported target must pass, not just the three supplied authoring samples.
    feedback["selected_bundle_ids"] = prior.get("selected_bundle_ids") or sorted(
        {i["bundle_id"] for i in issues}
    )
    context = {
        **row.context,
        "workspace_id": digest({"source_id": row.source_id}),
        "base_revision_id": base_revision_id,
        "baseline_recipe_id": baseline_recipe_id,
        "now_iso": utc_now().isoformat(),
        "sample_ids": [s["id"] for s in samples],
        "accuracy": base_recipe["config"].get("accuracy_contract") is not None,
        "bundle_ids": ids,
        "base_generation": generation,
        "generation": maximum + 1,
    }
    _save(
        store,
        row.id,
        status="preparing",
        context=context,
        feedback_ref=store.blobs.put(json_bytes(feedback)),
    )
    return context


async def advance(pipeline, source_id, *, limit):
    """One resumable pass; transient RPC failures leave persisted intent for retries."""
    if pipeline.recipe_rpc is None:
        return  # Record requests offline; never silently launch an in-process Agent.
    store = pipeline.store
    with store.lock, Session(store.engine) as session:
        row = session.scalar(
            select(RepairRequest)
            .where(
                RepairRequest.source_id == source_id,
                RepairRequest.status.in_(ACTIVE),
            )
            .order_by(RepairRequest.created_at, RepairRequest.id)
        )
        if row is None:
            return
        session.expunge(row)
    state = await asyncio.to_thread(store.status, source_id)
    baseline = row.context.get("baseline_recipe_id", row.base_recipe_id)
    expected = row.candidate_recipe_id if row.status == "accepting" else baseline
    if (
        state["stage"] not in {"active", "audit"}
        or state["active_recipe_id"] != expected
        or row.classifier_key != pipeline.classifier_key
        or row.toolkit_key != pipeline.toolkit_key()
    ):
        _save(store, row.id, status="superseded", error="REPAIR_CONTEXT_CHANGED")
        return
    try:
        with store.lock, Session(store.engine) as session:
            store.approved(session, source_id)
        context = row.context
        if row.status == "queued":
            if state["acquisition"].get("ready") or state["acquisition"].get("running"):
                return
            context = await asyncio.to_thread(_freeze, pipeline, row)
            row.status = "preparing"
        feedback_ref = row.feedback_ref
        if not feedback_ref:
            with Session(store.engine) as session:
                feedback_ref = session.get(RepairRequest, row.id).feedback_ref
        feedback = json.loads(store.blobs.read(feedback_ref))
        rpc = pipeline.recipe_rpc
        if row.status == "preparing":
            from .semantic_labels import author_visible, export_regressions

            location = await rpc.call("GetWorkspace", workspace_id=context["workspace_id"])
            expected_path = store.blobs.root.absolute() / "authoring" / context["workspace_id"]
            if (
                location.get("workspace_id") != context["workspace_id"]
                or Path(location.get("physical_path", "")) != expected_path
            ):
                raise RecipeRPCError("REPAIR_WORKSPACE_CHANGED")
            if not context.get("manifest_digest"):
                samples = [
                    store.load_bundle(key, classifier_key=row.classifier_key)
                    for key in context["sample_ids"]
                ]
                context["manifest_digest"] = await asyncio.to_thread(
                    prepare_input,
                    expected_path,
                    source_id,
                    samples,
                    context,
                    feedback,
                    semantic_labels=await asyncio.to_thread(export_regressions, store, source_id),
                    cases=[
                        store.load_bundle(b["id"], classifier_key=row.classifier_key)
                        for b in store.bundles(
                            source_id, classifier_key=row.classifier_key, summaries=True
                        )
                        if author_visible(store, b["id"])
                    ],
                )
                _save(store, row.id, context=context)
            receipt = await rpc.call(
                "InputCompleted",
                workspace_id=context["workspace_id"],
                revision="repair-" + row.id,
                request_id="repair-input-" + row.id,
                manifest_digest=context["manifest_digest"],
            )
            context["input_revision_id"] = receipt["input_revision_id"]
            _save(store, row.id, context=context)
            task = await rpc.call(
                "StartRepair",
                workspace_id=context["workspace_id"],
                input_revision="repair-" + row.id,
                base_recipe_digest=row.base_recipe_digest,
                base_revision_id=context["base_revision_id"],
                feedback={"repair_request_id": row.id, "origin": row.origin},
                request_id=row.id,
            )
            context["task_id"] = task["id"]
            _save(store, row.id, status="submitted", context=context)
            return
        if row.status == "submitted":
            task = await rpc.call("GetTask", task_id=context["task_id"])
            if (
                task.get("workspace_id") != context["workspace_id"]
                or task.get("request_id") != row.id
                or task.get("input_revision_id") != context["input_revision_id"]
                or task.get("kind") != "repair"
            ):
                raise RecipeRPCError("REPAIR_TASK_CHANGED")
            if task["status"] in {"queued", "waiting_init", "running"}:
                return
            if task["status"] != "candidate_ready":
                _save(
                    store, row.id, status="failed", error=task.get("error_code") or "REPAIR_FAILED"
                )
                return
            from .recipe_versions import import_revision

            revision = await rpc.call("GetRecipeRevision", revision_id=task["revision_id"])
            if revision.get("artifact_digest") == row.base_recipe_digest:
                raise RecipeRPCError("REPAIR_UNCHANGED")
            candidate_id = import_revision(
                store,
                source_id,
                revision,
                task,
                runner=pipeline.runner,
                toolkit=row.toolkit_key,
                sample_ids=context["sample_ids"],
            )
            context["revision_id"] = revision["id"]
            context["recipe_digest"] = revision["artifact_digest"]
            with store.lock, Session(store.engine) as session, session.begin():
                binding = session.get(SourceRecipeBinding, source_id)
                binding.candidate_recipe_id, binding.candidate_revision_id = (
                    candidate_id,
                    revision["id"],
                )
            _save(
                store, row.id, status="auditing", context=context, candidate_recipe_id=candidate_id
            )
            return
        if row.status == "auditing":
            await _audit_candidate(pipeline, row, context, feedback, limit=limit)
            return
        if row.status == "rejecting":
            await rpc.call(
                "ReviewRevision",
                workspace_id=context["workspace_id"],
                revision_id=context["revision_id"],
                decision="reject",
                feedback=context["review_feedback"],
                request_id="reject-repair-" + row.id,
            )
            _save(store, row.id, status="failed", error="REPAIR_AUDIT_REGRESSION")
            return
        if row.status == "accepting":
            await rpc.call(
                "ReviewRevision",
                workspace_id=context["workspace_id"],
                revision_id=context["revision_id"],
                decision="accept",
                feedback={"repair_request_id": row.id, "passed": True},
                request_id="accept-repair-" + row.id,
            )
            _save(store, row.id, status="succeeded")
    except RecipeRPCError as error:
        if error.transient:
            _save(store, row.id, error=error.code)
        else:
            _save(store, row.id, status="failed", error=error.code)
    except RecipeError:
        _save(store, row.id, status="failed", error="REPAIR_INPUT_INVALID")
    except ValueError:
        _save(store, row.id, status="failed", error="REPAIR_INPUT_NOT_READY")


async def _audit_candidate(pipeline, row, context, feedback, *, limit):
    store = pipeline.store
    counts = store.counts(row.source_id)
    if counts.get("ready") or counts.get("running"):
        return
    bundles = store.bundles(row.source_id, classifier_key=row.classifier_key, summaries=True)
    ids = sorted(b["id"] for b in bundles if needs_extraction(b))
    if ids != context["bundle_ids"]:
        _save(store, row.id, status="superseded", error="REPAIR_MATERIAL_CHANGED")
        return
    # Authoring can span days; re-evaluate saved material with today's explicit
    # clock instead of imposing an implicit one-day author run deadline.
    stamp = context.get("audit_now_iso")
    if stamp is None or evaluation_day(context_stamp({"now_iso": stamp})) != evaluation_day():
        stamp = utc_now().isoformat()
        context = dict(context, audit_now_iso=stamp)
        _save(store, row.id, context=context)
    day = evaluation_day(context_stamp({"now_iso": stamp}))
    with Session(store.engine) as session:
        candidate = json.loads(
            store.blobs.read(session.get(SourceRecipe, row.candidate_recipe_id).body_ref)
        )
    results, pending_ids = [], []
    for key in ids:
        cached = pipeline.audits.cached(key, row.candidate_recipe_id, day, context["generation"])
        if cached:
            results.append(cached)
        else:
            pending_ids.append(key)
    for key in pending_ids[:limit]:
        bundle = store.load_bundle(key, classifier_key=row.classifier_key)
        errors, failure_evidence = [], None
        try:
            result = await pipeline.runner.run(candidate, bundle, now_iso=stamp)
        except RecipeError as error:
            from .diagnostics import failed_result, failure_record

            failure_evidence = failure_record(
                bundle,
                candidate_digest(candidate),
                stamp,
                error,
                bindings={
                    "recipe_id": row.candidate_recipe_id,
                    "generation": context["generation"],
                },
            )
            reference = store.blobs.put(json_bytes(failure_evidence))
            result, errors = failed_result(error, failure_ref=reference)
        results.append(
            pipeline.audits.record(
                bundle,
                row.candidate_recipe_id,
                day,
                result,
                errors,
                context["generation"],
                failure_evidence=failure_evidence,
            )
        )
    if len(results) != len(ids) or evaluation_day() != day:
        return
    verdict = repair_verdict(feedback, results, complete=True, store=store)
    if any(
        e.get("failure_code", e.get("code")) not in REPAIRABLE
        for r in results
        for e in r["errors"]
        if e.get("failure_code")
    ):
        verdict["passed"] = False
    summary = {
        "audit_policy": AUDIT_POLICY,
        "classifier_key": row.classifier_key,
        "passed": verdict["passed"],
        "admission": {
            "policy": "quarantine-v1",
            "blocking_reasons": [] if verdict["passed"] else ["REPAIR_AUDIT_REGRESSION"],
            "source_errors_by_code": {},
        },
        "repair_verification": verdict,
        "results": results,
        "evaluated_on": day,
        "generation": context["generation"],
        "remaining": 0,
        "repair_request_id": row.id,
    }
    with store.lock, Session(store.engine) as session, session.begin():
        store.approved(session, row.source_id)
        state = session.get(SourcePipeline, row.source_id)
        binding = session.get(SourceRecipeBinding, row.source_id)
        request = session.get(RepairRequest, row.id)
        from .semantic_labels import current as semantic_current
        from .temporal_context import current as temporal_current

        run = AuditRun(
            source_id=row.source_id,
            recipe_id=row.candidate_recipe_id,
            kind="repair" if context["baseline_recipe_id"] else "onboarding",
            status="complete" if verdict["passed"] else "blocked",
            summary=summary,
            finished_at=utc_now(),
        )
        session.add(run)
        session.flush()
        if not verdict["passed"]:
            request.status, request.error_code = "rejecting", "REPAIR_AUDIT_REGRESSION"
            request.context = dict(
                context,
                review_feedback={
                    "audit_id": run.id,
                    "passed": False,
                    "repair_verification": verdict,
                },
            )
        elif any(
            (
                not semantic_current(session, session.get(AuditResult, item["id"]))
                or not temporal_current(session, session.get(AuditResult, item["id"]))
            )
            for item in results
        ):
            request.status, request.error_code = "superseded", "REPAIR_SEMANTIC_CASE_CHANGED"
        elif (
            state.stage not in {"active", "audit"}
            or binding.active_recipe_id != context["baseline_recipe_id"]
            or binding.generation != context["base_generation"]
            or binding.candidate_revision_id != context["revision_id"]
        ):
            request.status, request.error_code = "superseded", "REPAIR_CONTEXT_CHANGED"
        else:
            if binding.active_recipe_id:
                session.get(SourceRecipe, binding.active_recipe_id).status = "retired"
            session.get(SourceRecipe, row.candidate_recipe_id).status = "active"
            binding.active_recipe_id, binding.active_revision_id = (
                row.candidate_recipe_id,
                context["revision_id"],
            )
            binding.candidate_recipe_id = binding.candidate_revision_id = None
            binding.generation, binding.updated_at = context["generation"], utc_now()
            state.stage, state.state = "active", "ready"
            if context["baseline_recipe_id"]:
                state.summary = dict(state.summary, maintenance_audit_id=run.id)
            else:
                from .lifecycle import freeze_onboarding

                state.summary = dict(state.summary, audit=summary, audit_id=run.id)
                state.activated_at = utc_now()
                state.summary = {
                    key: value for key, value in state.summary.items() if key != "error"
                }
                freeze_onboarding(session, state, run)
            state.summary = {key: value for key, value in state.summary.items() if key != "error"}
            if state.summary.get("authoring_job"):
                state.summary = dict(
                    state.summary,
                    authoring_job=dict(
                        state.summary["authoring_job"],
                        superseded=True,
                    ),
                )
            state.updated_at = utc_now()
            request.status = "accepting"
            control = session.get(IncrementalControl, row.source_id)
            # Do not clear unrelated policy/provider blocks or alter the enable switch.
            if control and (control.error or {}).get("code") in {
                "SOURCE_EXECUTION_FAILURE",
                "INCREMENTAL_AUDIT_NOT_READY",
            }:
                control.state, control.error = "idle", None
        request.updated_at = utc_now()


def context_stamp(context):
    from datetime import datetime

    return datetime.fromisoformat(context["now_iso"])
