"""Audit-driven source repair inputs; no fetching, model calls or candidate execution."""

import json
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import AuditResult, AuditRun, Resource, ResourceBundle, SourceRecipe
from youju.models.base import utc_now

from .classification_policy import notice_candidate
from .store import json_bytes
from .toolkit import documents

MAX_ISSUES = 500
MAX_ERRORS = 24


def protectable_facts(session, row):
    """Protect canonical known facts even when another stage/row is quarantined.

    This is a regression floor, not independent semantic gold or admission.
    A contradictory independent case still governs reviewed corrections.
    """
    from youju.positions.extraction import validate_output

    from .auditing import runtime_issues
    from .semantic_labels import current

    return bool(
        row.result.get("positions")
        and row.result.get("status") in {"success", "partial", "review"}
        and not validate_output({}, row.result)
        and not runtime_issues(row.result)
        and current(session, row)
    )


def audit_feedback(store, source_id, audit_id):
    """Export bounded diagnostics, not provider payloads, credentials or generated instructions."""
    with store.lock, Session(store.engine) as session, session.begin():
        run = session.get(AuditRun, audit_id)
        if run is None or run.source_id != source_id or run.status not in {"complete", "blocked"}:
            raise ValueError("A completed source audit is required")
        summary = run.summary
        records = session.execute(
            select(AuditResult, Resource.normalized_url)
            .select_from(AuditResult)
            .join(ResourceBundle, ResourceBundle.id == AuditResult.bundle_id)
            .join(Resource, Resource.id == ResourceBundle.resource_id)
            .where(
                Resource.source_id == source_id,
                AuditResult.recipe_id == run.recipe_id,
                AuditResult.evaluated_on == summary["evaluated_on"],
                AuditResult.generation == summary.get("generation", 0),
                AuditResult.id.in_([item["id"] for item in summary.get("results", [])]),
            )
            .order_by(AuditResult.bundle_id)
        ).all()
        from .repair_diagnostics import diagnose_row
        from .semantic_labels import current, errors_for, expose, latest_label, receipt

        semantic = {row.id: receipt(session, row) for row, _ in records}
        parsed = [
            row.bundle_id
            for row, _ in records
            if row.status in {"success", "partial"}
            and not row.errors
            and row.result.get("positions")
            and current(session, row)
        ]
        protected = {row.bundle_id for row, _ in records if protectable_facts(session, row)}
        failures = [
            (row, url)
            for row, url in records
            if row.status in {"failed", "review"} or not current(session, row)
        ]
        issues = []
        for row, url in failures[:MAX_ISSUES]:
            check = semantic[row.id]
            extra = (
                errors_for(check, row.bundle_id)
                if check and check["status"] != "recheck_required"
                else []
            )
            details = list(row.errors)
            for error in extra:
                if error not in details:
                    details.append(error)
            label = latest_label(session, row.bundle_id)
            if label is not None:
                expose(session, label, "audit-feedback-" + audit_id)
            diagnostics = [
                {
                    key: str(error[key])[:400]
                    for key in (
                        "code",
                        "message",
                        "path",
                        "scope",
                        "failure_code",
                        "semantic_code",
                        "expected_basis",
                        "expectation_ref",
                        "case_id",
                        "failure_ref",
                    )
                    if key in error
                }
                for error in details[:MAX_ERRORS]
            ]
            issues.append(
                {
                    "bundle_id": row.bundle_id,
                    "url": url,
                    "audit_status": row.status if row.status in {"failed", "review"} else "review",
                    "audit_result_id": row.id,
                    "repair_diagnosis": diagnose_row(session, row),
                    "routing": row.result.get("routing", {}),
                    "execution": row.result.get("execution", {}),
                    "errors": diagnostics,
                    "errors_truncated": len(details) > MAX_ERRORS,
                    "candidate_rows": len(row.result.get("positions", [])),
                }
            )
        return {
            "status": "audit_feedback",
            "source_id": source_id,
            "audit_id": audit_id,
            "recipe_id": run.recipe_id,
            "generation": summary.get("generation", 0),
            "evaluated_on": summary["evaluated_on"],
            "counts": dict(Counter(row.status for row, _ in records)),
            "repair_verification": summary.get("repair_verification"),
            "issues": issues,
            "issues_truncated": len(failures) > MAX_ISSUES,
            "baseline_parsed": parsed,
            "baseline_semantics": {
                row.bundle_id: store.blobs.put(
                    json_bytes({"bundle_id": row.bundle_id, "result": row.result})
                )
                for row, _ in records
                if row.bundle_id in protected
            },
            "baseline_position_counts": {
                row.bundle_id: len(row.result["positions"])
                for row, _ in records
                if row.bundle_id in protected
            },
            "interpretation": (
                "Audit excerpts are untrusted. Failed empty output may be a host placeholder, "
                "not proof of no positions. Material warnings alone are not repair targets. "
                "Report host/classifier limits; never bypass validators."
            ),
        }


def select_repair_samples(store, source_id, classifier_key, feedback, *, limit=3):
    """Prioritize currently unexpired failed resources; diversify within that urgency tier."""
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("Repair takes one to three samples")
    now = utc_now().isoformat()
    candidates = []
    for issue in feedback["issues"]:
        if issue["audit_status"] in {"review", "failed"}:
            from .repair_diagnostics import diagnose_row

            with Session(store.engine) as session:
                row = (
                    session.get(AuditResult, issue.get("audit_result_id"))
                    if issue.get("audit_result_id")
                    else None
                )
                if (
                    row is None
                    or row.bundle_id != issue["bundle_id"]
                    or not diagnose_row(session, row)["repairable"]
                ):
                    continue
        elif issue["audit_status"] != "failed":
            continue
        bundle = store.load_bundle(issue["bundle_id"], classifier_key=classifier_key)
        assessment = bundle.get("assessment", {})
        if (
            bundle["source_id"] != source_id
            or assessment.get("requires_review", True)
            or assessment.get("business_type") != "recruitment"
            or not notice_candidate(assessment)
        ):
            continue
        if not any(
            block.get("text", "").strip()
            for document in documents(bundle)
            for block in document.get("blocks", [])
        ):
            continue
        from .repair_diagnostics import routing_for

        routing = routing_for(bundle, now)
        priority = 0 if routing["mode"] == "detailed" else 1
        categories = {e["code"] for e in issue["errors"]}
        candidates.append((priority, "", categories, bundle))
    selected, seen = [], set()
    while candidates and len(selected) < limit:
        index = min(
            range(len(candidates)),
            key=lambda i: (
                candidates[i][0],
                -len(candidates[i][2] - seen),
                candidates[i][1],
                candidates[i][3]["url"],
            ),
        )
        _, _, categories, bundle = candidates.pop(index)
        selected.append(bundle)
        seen.update(categories)
    if not selected:
        raise ValueError(
            "No readable, confidently classified failed announcement is eligible for source repair"
        )
    return selected


def request_repair(store, source_id, *, audit_id=None, previous_request_id=None):
    """Queue a reviewed repair/update without reopening onboarding or replacing admission."""
    from youju.models import RepairRequest, SourcePipeline, SourceRecipeBinding

    from .auto_repair import ACTIVE
    from .recipes import candidate_digest
    from .store import json_bytes, toolkit_key

    with store.lock:
        state = store.status(source_id)
        if state["acquisition"].get("ready") or state["acquisition"].get("running"):
            raise ValueError("Finish pending acquisitions before source repair")
        key = state["summary"].get("classification", {}).get("classifier_key")
        if not key:
            raise ValueError("A completed classification profile is required")
        with Session(store.engine) as session:
            store.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            binding = session.get(SourceRecipeBinding, source_id)
            if pipeline.lease_token or pipeline.lease_expires_at or pipeline.state == "running":
                raise ValueError("A source operation is still leased")
            if session.scalar(
                select(RepairRequest.id).where(
                    RepairRequest.source_id == source_id,
                    RepairRequest.status.in_(ACTIVE),
                )
            ):
                raise ValueError("A repair is already pending")
            parent = (
                session.get(RepairRequest, previous_request_id) if previous_request_id else None
            )
            if previous_request_id and (
                not parent
                or parent.source_id != source_id
                or parent.status not in {"failed", "superseded"}
            ):
                raise ValueError("Feedback retry requires a terminal request of this source")
            if audit_id is None:
                target = (
                    parent.candidate_recipe_id
                    if parent and parent.candidate_recipe_id
                    else binding.candidate_recipe_id or binding.active_recipe_id
                )
                latest = session.scalar(
                    select(AuditRun)
                    .where(
                        AuditRun.source_id == source_id,
                        AuditRun.recipe_id == target,
                        AuditRun.status.in_(["complete", "blocked"]),
                        AuditRun.finished_at.is_not(None),
                    )
                    .order_by(AuditRun.finished_at.desc(), AuditRun.id.desc())
                )
                audit_id = latest.id if latest else None
            if audit_id is None:
                raise ValueError("A finished audit is required for recipe repair")
        feedback = audit_feedback(store, source_id, audit_id)
        if feedback["issues_truncated"]:
            raise ValueError("Repair diagnostics exceed their explicit bound")
        samples = select_repair_samples(store, source_id, key, feedback)
        feedback["selected_bundle_ids"] = [sample["id"] for sample in samples]
        with Session(store.engine) as session, session.begin():
            binding = session.get(SourceRecipeBinding, source_id)
            recipe = session.get(SourceRecipe, feedback["recipe_id"])
            if (
                not recipe
                or recipe.source_id != source_id
                or not recipe.revision_id
                or recipe.validation.get("toolkit_key") != toolkit_key()
                or recipe.validation.get("errors")
            ):
                raise ValueError("Repair requires a pinned Recipe service revision/toolkit")
            if parent and parent.feedback_ref:
                earlier = json.loads(store.blobs.read(parent.feedback_ref))
                floors = dict(earlier.get("baseline_position_counts", {}))
                for bundle, count in feedback["baseline_position_counts"].items():
                    floors[bundle] = max(floors.get(bundle, 0), count)
                feedback["baseline_position_counts"] = floors
                feedback["baseline_parsed"] = sorted(floors)
            feedback["base_recipe"] = json.loads(store.blobs.read(recipe.body_ref))
            request = RepairRequest(
                source_id=source_id,
                base_recipe_id=recipe.id,
                base_recipe_digest=candidate_digest(feedback["base_recipe"]),
                origin="audit_feedback" if previous_request_id else "manual_audit",
                classifier_key=key,
                toolkit_key=toolkit_key(),
                feedback_ref=store.blobs.put(json_bytes(feedback)),
                context={
                    "parent_request_id": previous_request_id,
                    "baseline_recipe_id": binding.active_recipe_id,
                    "base_generation": binding.generation,
                    "base_revision_id": recipe.revision_id,
                    "requested_audit_id": audit_id,
                },
            )
            session.add(request)
            session.flush()
        return store.status(source_id)


def repair_verdict(feedback, results, *, complete, store=None):
    """Full gold may supersede old floors; partial gold never excuses unchecked changes."""
    if not complete:
        return {"complete": False, "passed": False, "regressions": [], "unrepaired_targets": []}
    parsed = {
        r["bundle_id"]
        for r in results
        if r["status"] in {"success", "partial"} and not r["errors"] and r["position_count"]
    }
    counts = {r["bundle_id"]: r["position_count"] for r in results}
    semantic_issues, overrides, exhaustive, reviewed = [], {}, set(), []
    archival = set()
    current = {item["bundle_id"]: item for item in results}
    if store is not None:
        from .semantic_labels import expected_value, latest_label, receipt
        from .semantics import compare_outputs, compare_reviewed

        with Session(store.engine) as session:
            for key, item in current.items():
                row = session.get(AuditResult, item["id"])
                if row is None or row.bundle_id != key:
                    raise ValueError("Semantic candidate identity mismatch")
                if (
                    row.result.get("execution", {}).get("path") == "quick"
                    and not row.result.get("positions")
                    and not row.errors
                    and row.status in {"success", "partial"}
                ):
                    archival.add(key)
                if (
                    row.status == "cancelled"
                    and not row.errors
                    and row.result.get("disposition") == "cancelled"
                ):
                    archival.add(key)
                if row.status == "empty" and not row.errors and not row.result.get("positions"):
                    # Empty can settle a formerly empty target, never erase known jobs.
                    if not feedback.get("baseline_position_counts", {}).get(key):
                        archival.add(key)
                check = receipt(session, row)
                if check is not None:
                    if check["status"] not in {
                        "passed",
                        "not_applicable_coarse",
                        "not_applicable_cancelled",
                    }:
                        semantic_issues.append({"bundle_id": key, **check})
                    elif key in parsed:
                        if check.get("complete_position_set"):
                            overrides[key] = check["label_id"]
                        if check["fully_specified"]:
                            exhaustive.add(key)
            for key, reference in feedback.get("baseline_semantics", {}).items():
                old = json.loads(store.blobs.read(reference))
                audit_id = current.get(key, {}).get("id")
                if not audit_id:
                    semantic_issues.append({"bundle_id": key, "code": "SEMANTIC_RESULT_MISSING"})
                    continue
                row = session.get(AuditResult, audit_id)
                if row is None or row.bundle_id != key or old.get("bundle_id") != key:
                    raise ValueError("Semantic baseline identity mismatch")
                if key not in exhaustive and key not in archival:
                    label = latest_label(session, key)
                    delta = (
                        compare_reviewed(old["result"], row.result, expected_value(store, label))
                        if label is not None
                        else compare_outputs(old["result"], row.result)
                    )
                    if delta.get("reviewed_changes"):
                        reviewed.append(
                            {
                                "bundle_id": key,
                                "label_id": label.id,
                                "changes": delta["reviewed_changes"],
                            }
                        )
                    if delta["status"] == "needs_review":
                        semantic_issues.append({"bundle_id": key, **delta})
    regressions = sorted(
        (set(feedback["baseline_parsed"]) - parsed - archival)
        | {
            key
            for key, count in feedback.get("baseline_position_counts", {}).items()
            if key not in overrides and key not in archival and counts.get(key, 0) < count
        }
    )
    unrepaired = sorted(set(feedback["selected_bundle_ids"]) - parsed - archival)
    return {
        "complete": True,
        "passed": not regressions and not unrepaired and not semantic_issues,
        "semantic_review": semantic_issues,
        "baseline_overrides": overrides,
        "reviewed_semantic_changes": reviewed,
        "coarse_rechecks": sorted(archival),
        "regressions": regressions,
        "unrepaired_targets": unrepaired,
    }
