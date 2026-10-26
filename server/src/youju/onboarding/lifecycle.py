"""Separate, persistent baseline admission and opt-in incremental monitoring."""

from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.models import (
    AuditResult,
    AuditRun,
    IncrementalControl,
    IncrementalRun,
    OnboardingResult,
    Resource,
    ResourceKind,
    ResourceScan,
    ScanPolicy,
    SourcePipeline,
    SourceRecipeBinding,
)
from youju.models.base import utc_now

AUDIT_FIELDS = (
    "total_announcements",
    "processed",
    "remaining",
    "statuses",
    "validity",
    "application_period",
    "position_count",
    "partial_position_count",
    "candidate_position_count",
    "errors_by_code",
    "warnings_by_code",
    "passed",
    "evaluated_on",
    "verification",
    "extraction_statuses",
    "material_statuses",
    "quality_tiers",
    "coarse_announcements",
    "withdrawn_announcements",
)


def audit_projection(summary):
    return {key: deepcopy(summary[key]) for key in AUDIT_FIELDS if key in summary}


def freeze_onboarding(session, pipeline, audit, *, provenance="activation"):
    """Called in the activation transaction; historical baseline is write-once."""
    if session.get(OnboardingResult, pipeline.source_id) is None:
        counts = dict(
            session.execute(
                select(ResourceScan.state, func.count())
                .join(Resource)
                .where(
                    Resource.source_id == pipeline.source_id,
                    Resource.kind != ResourceKind.UNKNOWN,
                )
                .group_by(ResourceScan.state)
            ).all()
        )
        summary = {"audit": deepcopy(audit.summary), "audit_id": audit.id}
        result_ids = [item["id"] for item in audit.summary.get("results", []) if "id" in item]
        period = func.coalesce(
            func.json_extract(AuditResult.result, "$.validity.application_period.kind"), "missing"
        )
        summary["audit"]["application_period"] = dict(
            session.execute(
                select(period, func.count()).where(AuditResult.id.in_(result_ids)).group_by(period)
            ).all()
        )
        if provenance == "activation":
            summary = deepcopy(pipeline.summary) | summary
        session.add(
            OnboardingResult(
                source_id=pipeline.source_id,
                recipe_id=audit.recipe_id,
                audit_id=audit.id,
                completed_at=audit.finished_at,
                summary=summary,
                acquisition=counts if provenance == "activation" else {},
                provenance=provenance,
            )
        )
    if session.get(IncrementalControl, pipeline.source_id) is None:
        session.add(IncrementalControl(source_id=pipeline.source_id, enabled=False))


def backfill_lifecycle(engine):
    """Explicit migration only; do not freeze the latest incremental as a baseline."""
    count = 0
    with Session(engine) as session, session.begin():
        for pipeline in session.scalars(select(SourcePipeline)):
            binding = session.get(SourceRecipeBinding, pipeline.source_id)
            if (
                not binding
                or not binding.active_recipe_id
                or session.get(OnboardingResult, pipeline.source_id)
            ):
                continue
            audits = session.scalars(
                select(AuditRun)
                .where(
                    AuditRun.source_id == pipeline.source_id,
                    AuditRun.kind == "onboarding",
                    AuditRun.status == "complete",
                )
                .order_by(AuditRun.finished_at, AuditRun.id)
            )
            audit = next(
                (
                    a
                    for a in audits
                    if a.finished_at
                    and a.summary.get("passed")
                    and a.summary.get("admission", {}).get("policy") == "quarantine-v1"
                ),
                None,
            )
            if audit is not None:
                freeze_onboarding(session, pipeline, audit, provenance="legacy-onboarding-audit")
                count += 1
    return count


def incremental_json(session, source_id, *, history=False):
    from .incremental_view import run_projection

    control = session.get(IncrementalControl, source_id)
    runs = list(
        session.scalars(
            select(IncrementalRun)
            .where(
                IncrementalRun.source_id == source_id,
            )
            .order_by(IncrementalRun.started_at.desc(), IncrementalRun.id.desc())
            .limit(20)
        )
    )
    latest = runs[0] if runs else None
    enabled = bool(control and control.enabled)
    state = (
        "blocked"
        if control and control.state == "blocked"
        else "running"
        if latest and latest.status == "running" and enabled
        else "idle"
        if enabled
        else "disabled"
    )
    result = {"enabled": enabled, "state": state, "latest_run": run_projection(session, latest)}
    from youju.models import RepairRequest

    from .auto_repair import request_json

    result["repairs"] = [
        request_json(row)
        for row in session.scalars(
            select(RepairRequest)
            .where(RepairRequest.source_id == source_id)
            .order_by(RepairRequest.created_at.desc(), RepairRequest.id.desc())
            .limit(20)
        )
    ]

    if control and control.error:
        result["error"] = {"code": control.error.get("code", "UNKNOWN")}
    if history:
        result["runs"] = [run_projection(session, row) for row in runs]
    return result


def set_incremental(session, source_id, enabled):
    pipeline = session.get(SourcePipeline, source_id)
    if (
        session.get(OnboardingResult, source_id) is None
        or pipeline is None
        or enabled
        and (pipeline.stage != "active" or pipeline.state != "ready")
    ):
        raise ValueError("Complete source admission/maintenance before monitoring")
    control = session.get(IncrementalControl, source_id)
    if control is None:
        control = IncrementalControl(source_id=source_id)
        session.add(control)
    control.enabled, control.updated_at = enabled, utc_now()
    if enabled:
        control.state, control.error = "idle", None
    session.flush()
    return incremental_json(session, source_id, history=True)


class IncrementalLedger:
    def __init__(self, store):
        self.store = store

    def allowed(self, source_id):
        with self.store.lock, Session(self.store.engine) as session:
            control = session.get(IncrementalControl, source_id)
            return bool(control and control.enabled and control.state == "idle")

    @staticmethod
    def _scans(session, source_id):
        return session.execute(
            select(Resource, ResourceScan)
            .join(ResourceScan)
            .where(
                Resource.source_id == source_id,
                Resource.kind != ResourceKind.UNKNOWN,
            )
        ).all()

    def begin(self, source_id, *, scheduled):
        """No empty runs. Reuse a running cycle across bounded passes and restarts."""
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            self.store.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            if not pipeline or pipeline.stage != "active" or pipeline.state != "ready":
                raise ValueError("Only admitted sources can run incremental scans")
            control = session.get(IncrementalControl, source_id)
            if control and control.state == "blocked":
                return None
            if scheduled and not (control and control.enabled):
                return None
            if control is None:
                control = IncrementalControl(source_id=source_id, enabled=False, state="idle")
                session.add(control)
            running = session.scalar(
                select(IncrementalRun).where(
                    IncrementalRun.source_id == source_id,
                    IncrementalRun.status == "running",
                )
            )
            if running:
                return running.id
            now = utc_now()
            scans = self._scans(session, source_id)
            pending = any(
                s.state == "running" or s.state == "ready" and s.available_at <= now
                for _, s in scans
            )
            due = session.scalar(
                select(ScanPolicy.resource_id)
                .join(Resource)
                .join(ResourceScan)
                .where(
                    Resource.source_id == source_id,
                    ScanPolicy.next_scan_at <= now,
                    ResourceScan.state == "done",
                )
                .limit(1)
            )
            if not pending and due is None and not pipeline.summary.get("incremental_generation"):
                return None
            row = IncrementalRun(
                source_id=source_id,
                baseline={
                    r.id: {
                        "version": s.last_version_id,
                        "attempt": s.last_attempt_id,
                        "bundle": s.bundle_id,
                    }
                    for r, s in scans
                },
            )
            session.add(row)
            session.flush()
            return row.id

    def update(self, run_id, *, queued=0, complete=False):
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            run = session.get(IncrementalRun, run_id)
            scans = self._scans(session, run.source_id)
            changes, fetched, failed = [], 0, 0
            for resource, scan in scans:
                before = run.baseline.get(resource.id)
                if before is None or before["attempt"] != scan.last_attempt_id:
                    if scan.last_success_at and scan.last_success_at >= run.started_at:
                        fetched += 1
                    if scan.state == "review" and scan.last_error:
                        failed += 1
                if before is None or before["version"] != scan.last_version_id:
                    changes.append(
                        {
                            "resource_id": resource.id,
                            "url": resource.normalized_url,
                            "kind": "new" if before is None else "changed",
                        }
                    )
            run.summary = dict(
                run.summary,
                queued_due=run.summary.get("queued_due", 0) + queued,
                new_resources=sum(x["kind"] == "new" for x in changes),
                changed_resources=sum(x["kind"] == "changed" for x in changes),
                fetched_resources=fetched,
                failed_resources=failed,
                resource_changes=changes[:100],
                changes_truncated=len(changes) > 100,
            )
            if complete and run.status == "running":
                run.finished_at = utc_now()
                if run.summary.get("audit", {}).get("passed") is False:
                    run.status = "blocked"
                    error = {"code": "INCREMENTAL_AUDIT_NOT_READY"}
                    run.summary = dict(run.summary, error=error)
                    control = session.get(IncrementalControl, run.source_id)
                    control.state, control.error, control.updated_at = "blocked", error, utc_now()
                else:
                    run.status = "complete"

    def block(self, source_id, code):
        """An incremental failure does not reopen or invalidate the baseline admission."""
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            control = session.get(IncrementalControl, source_id)
            if control is None:
                control = IncrementalControl(source_id=source_id)
                session.add(control)
            control.state, control.error, control.updated_at = "blocked", {"code": code}, utc_now()
            run = session.scalar(
                select(IncrementalRun).where(
                    IncrementalRun.source_id == source_id,
                    IncrementalRun.status == "running",
                )
            )
            if run:
                run.status, run.finished_at = "blocked", utc_now()
                run.summary = dict(run.summary, error={"code": code})
                if run.audit_id:
                    audit = session.get(AuditRun, run.audit_id)
                    if audit.status == "running":
                        audit.status, audit.finished_at = "blocked", utc_now()
