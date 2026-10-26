"""Read-only source-wide totals over current saved bundles, not frozen admission or run deltas."""

from collections import Counter

from sqlalchemy import func, select

from youju.models import (
    AuditResult,
    PageAssessment,
    ParseResult,
    Position,
    Resource,
    ResourceBundle,
    ResourceScan,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now

from .auditing import audit_dimensions
from .classification_policy import notice_candidate


def parsing_summary(session, source_id):
    pipeline = session.get(SourcePipeline, source_id)
    summary = pipeline.summary if pipeline else {}
    effective_key = summary.get(
        "effective_classifier_key", summary.get("classification", {}).get("classifier_key")
    )
    latest = (
        select(
            PageAssessment.bundle_id,
            PageAssessment.result,
            func.row_number()
            .over(
                partition_by=PageAssessment.bundle_id,
                order_by=(PageAssessment.created_at.desc(), PageAssessment.id.desc()),
            )
            .label("rank"),
        )
        .where(PageAssessment.classifier_key == effective_key)
        .subquery()
    )
    records = (
        session.execute(
            select(
                ResourceBundle.id,
                ResourceBundle.page_version_id,
                ResourceBundle.status.label("material_status"),
                Resource.id.label("resource_id"),
                ResourceScan.state,
                ResourceScan.last_version_id,
                latest.c.result,
            )
            .select_from(Resource)
            .join(ResourceScan, ResourceScan.resource_id == Resource.id)
            .join(
                ResourceBundle,
                (ResourceBundle.id == ResourceScan.bundle_id)
                & (ResourceBundle.resource_id == Resource.id),
            )
            .outerjoin(latest, (latest.c.bundle_id == ResourceBundle.id) & (latest.c.rank == 1))
            .where(Resource.source_id == source_id)
        )
        .mappings()
        .all()
    )
    notices = [
        r for r in records if isinstance(r["result"], dict) and notice_candidate(r["result"])
    ]
    from .temporal_context import associated_event_reviews

    related = {
        r["id"] for r in records if associated_event_reviews(session, r["resource_id"], r["id"])
    }
    unclassified = sum(r["result"] is None for r in records)
    binding = session.get(SourceRecipeBinding, source_id)
    recipe = (
        session.get(SourceRecipe, binding.active_recipe_id)
        if binding and binding.active_recipe_id
        else None
    )
    active = recipe is not None and recipe.source_id == source_id and recipe.status == "active"
    results = {}
    if active and notices:
        audits = (
            select(
                AuditResult,
                func.row_number()
                .over(
                    partition_by=AuditResult.bundle_id,
                    order_by=(AuditResult.evaluated_on.desc(), AuditResult.created_at.desc()),
                )
                .label("rank"),
            )
            .where(
                AuditResult.bundle_id.in_([r["id"] for r in notices]),
                AuditResult.recipe_id == recipe.id,
                AuditResult.generation == binding.generation,
            )
            .subquery()
        )
        results = {
            r["bundle_id"]: r
            for r in session.execute(select(audits).where(audits.c.rank == 1)).mappings()
        }
    statuses = Counter()
    extraction_paths = Counter()
    routing_categories = Counter()
    field_issues = Counter()
    coverage_issues = Counter()
    review_required = 0
    accepted = set()
    days = []
    for notice in notices:
        if notice["id"] in related:
            statuses["associated"] += 1
            continue
        audit = results.get(notice["id"])
        if (
            audit is None
            or notice["state"] not in {"done", "review"}
            or notice["page_version_id"] != notice["last_version_id"]
        ):
            statuses["pending"] += 1
            continue
        from .semantic_labels import current as semantic_current

        stored_audit = session.get(AuditResult, audit["id"])
        from .temporal_context import current as temporal_current

        if not semantic_current(session, stored_audit) or not temporal_current(
            session, stored_audit
        ):
            statuses["review"] += 1
            review_required += 1
            continue
        dimensions = audit_dimensions(stored_audit, notice["material_status"])
        extraction_paths[dimensions["extraction_path"]] += 1
        routing_categories[dimensions["routing_category"]] += 1
        field_issues.update(issue["code"] for issue in dimensions["field_issues"])
        coverage_issues.update(issue["code"] for issue in dimensions["coverage_issues"])
        review_required += dimensions["review_required"]
        days.append(audit["evaluated_on"])
        status = audit["status"]
        if status == "cancelled" and audit["result"].get("disposition") == "cancelled":
            statuses["cancelled"] += 1
            continue
        # Quick is a cost path, not an expiry or quality verdict. Failed/review
        # zero-row work must not be hidden inside completed archives.
        if dimensions["extraction_status"] == "archived":
            statuses["archived"] += 1
            continue
        statuses[
            status if status in {"success", "partial", "empty", "review", "failed"} else "review"
        ] += 1
        if status in {"success", "partial"} and not audit["errors"] and audit["parse_id"]:
            accepted.add((audit["parse_id"], notice["page_version_id"]))
    # A parse must belong to this exact saved page version. Never sum all historical rows.
    versions = dict(accepted)
    valid_parses = (
        {
            parse.id
            for parse in session.scalars(select(ParseResult).where(ParseResult.id.in_(versions)))
            if parse.version_id == versions[parse.id]
        }
        if versions
        else set()
    )
    positions = (
        session.scalar(
            select(func.count()).select_from(Position).where(Position.parse_id.in_(valid_parses))
        )
        if valid_parses
        else 0
    )
    return {
        "source_id": source_id,
        "scope": "current_saved_notices",
        "state": "ready"
        if active and records and not unclassified and not statuses["pending"]
        else "pending",
        "notice_count": len(notices),
        "position_count": positions,
        "statuses": {
            key: statuses[key]
            for key in (
                "success",
                "partial",
                "empty",
                "review",
                "failed",
                "pending",
                "archived",
                "cancelled",
                "associated",
            )
        },
        "extraction_paths": dict(extraction_paths),
        "routing_categories": dict(routing_categories),
        "field_issues_by_code": dict(field_issues),
        "coverage_issues_by_code": dict(coverage_issues),
        "review_required": review_required,
        "unclassified_pages": unclassified,
        "recipe_id": recipe.id if active else None,
        "evaluated_from": min(days) if days else None,
        "evaluated_through": max(days) if days else None,
        "summarized_at": utc_now().isoformat(),
    }
