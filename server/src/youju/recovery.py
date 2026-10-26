"""Host-owned blocked-source action policy; no source text can authorize retries."""

from sqlalchemy import func, select

from youju.models import (
    AuditRun,
    RegistryStatus,
    RepairRequest,
    Resource,
    ResourceScan,
    Source,
    SourceRecipe,
    SourceRecipeBinding,
)

ACTIVE_TASKS = {"preparing", "queued", "waiting_init", "running"}


def recovery_options(session, pipeline):
    depth, resources = pipeline.config["max_depth"], pipeline.config["max_resources"]
    scans = dict(
        session.execute(
            select(ResourceScan.last_error, func.count())
            .join(Resource)
            .where(
                Resource.source_id == pipeline.source_id,
                ResourceScan.state == "review",
            )
            .group_by(ResourceScan.last_error)
        ).all()
    )
    blockers = set(pipeline.summary.get("inventory_blockers", []))
    blockers.update(
        issue.get("code")
        for issue in pipeline.summary.get("inventory_issues", [])
        if issue.get("code") in {"depth_limit", "resource_limit", "discovery_link_limit"}
    )
    if scans.get("depth_limit"):
        blockers.add("depth_limit")
    limits = {"max_depth": depth, "max_resources": resources}
    suggested = {}
    if "depth_limit" in blockers and depth < 100:
        suggested["max_depth"] = 100
    if "resource_limit" in blockers and resources < 10000:
        suggested["max_resources"] = min(10000, max(resources + 1000, resources * 2))
    task = pipeline.summary.get("authoring_job", {})
    running = session.scalar(
        select(ResourceScan.resource_id)
        .join(Resource)
        .where(
            Resource.source_id == pipeline.source_id,
            ResourceScan.state == "running",
        )
        .limit(1)
    )
    source = session.get(Source, pipeline.source_id)
    idle = (
        source is not None
        and source.status == RegistryStatus.ACTIVE
        and pipeline.state == "blocked"
        and not pipeline.lease_token
        and not pipeline.lease_expires_at
        and not running
        and task.get("status") not in ACTIVE_TASKS
    )
    resolvable = (
        "discovery_link_limit" not in blockers
        and ("depth_limit" not in blockers or depth < 100)
        and ("resource_limit" not in blockers or resources < 10000)
    )
    can_resume = bool(
        idle and resolvable and pipeline.stage in {"inventory", "classification", "authoring"}
    )
    from youju.onboarding.auto_repair import ACTIVE

    binding = session.get(SourceRecipeBinding, pipeline.source_id)
    target = (binding.candidate_recipe_id or binding.active_recipe_id) if binding else None
    audit = (
        session.scalar(
            select(AuditRun)
            .where(
                AuditRun.source_id == pipeline.source_id,
                AuditRun.recipe_id == target,
                AuditRun.status.in_(["complete", "blocked"]),
                AuditRun.finished_at.is_not(None),
            )
            .order_by(AuditRun.finished_at.desc(), AuditRun.id.desc())
        )
        if target
        else None
    )
    recipe = session.get(SourceRecipe, target) if target else None
    key = pipeline.summary.get("classification", {}).get("classifier_key")
    pending = session.scalar(
        select(ResourceScan.resource_id)
        .join(Resource)
        .where(
            Resource.source_id == pipeline.source_id,
            ResourceScan.state.in_(["ready", "running"]),
        )
        .limit(1)
    )
    pending_repair = session.scalar(
        select(RepairRequest.id)
        .where(
            RepairRequest.source_id == pipeline.source_id,
            RepairRequest.status.in_(ACTIVE),
        )
        .limit(1)
    )
    has_failures = bool(
        audit
        and (
            audit.summary.get("statuses", {}).get("failed", 0)
            or (audit.summary.get("repair_verification") or {}).get("passed") is False
            or any(result.get("status") == "failed" for result in audit.summary.get("results", []))
        )
    )
    repair_idle = bool(
        source
        and source.status == RegistryStatus.ACTIVE
        and pipeline.state in {"ready", "blocked"}
        and not pipeline.lease_token
        and not pipeline.lease_expires_at
        and task.get("status") not in ACTIVE_TASKS
    )
    can_repair = bool(
        repair_idle
        and pipeline.stage in {"active", "audit"}
        and key
        and not pending
        and not pending_repair
        and has_failures
        and recipe
        and recipe.revision_id
        and not recipe.validation.get("errors")
    )
    return {
        "limits": limits,
        "suggested_limits": suggested,
        "blocking_limits": sorted(blockers),
        "depth_limited_resources": scans.get("depth_limit", 0),
        "can_resume": can_resume,
        "can_repair": can_repair,
        "requires_operator": pipeline.state == "blocked" and not (can_resume or can_repair),
    }
