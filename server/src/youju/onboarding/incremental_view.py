"""Read-only, run-scoped notice/position presentation; never a source-wide audit view."""

import re
from collections import Counter, defaultdict

from sqlalchemy import func, select

from youju.models import AuditResult, AuditRun, ParseResult, Position, Resource, ResourceBundle
from youju.positions.facts import PositionFacts

from .auditing import audit_dimensions

PAGE_SIZE = 20
POSITIONS_PER_NOTICE = 10
FACT_FIELDS = ("title", "hiring_unit", "headcount", "deadline_on", "employment_type")


def _issue_codes(issues):
    return sorted(
        {
            issue["code"]
            for issue in issues
            if isinstance(issue, dict)
            and isinstance(issue.get("code"), str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", issue["code"])
        }
    )[:20]


def _unavailable(state, reason):
    return {
        "state": state,
        "reason": reason,
        "notice_count": None,
        "position_count": None,
        "new_notices": None,
        "changed_notices": None,
    }


def notice_rows(session, run):
    """Resolve exact audit members and compare bundle identity, including attachment changes."""
    if not run.audit_id:
        return _unavailable(
            "pending" if run.status == "running" else "unavailable", "AUDIT_NOT_AVAILABLE"
        ), []
    audit = session.get(AuditRun, run.audit_id)
    if not audit or audit.source_id != run.source_id or audit.kind != "incremental":
        return _unavailable("unavailable", "AUDIT_CONTEXT_INVALID"), []
    baseline = run.baseline
    if not isinstance(baseline, dict) or any(
        not isinstance(value, dict) or "bundle" not in value for value in baseline.values()
    ):
        return _unavailable("unavailable", "BASELINE_NOT_AVAILABLE"), []
    summary = audit.summary
    if not isinstance(summary.get("results"), list) or not summary.get("evaluated_on"):
        return _unavailable("unavailable", "AUDIT_RESULTS_NOT_AVAILABLE"), []
    ids = [
        item["id"]
        for item in summary["results"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if len(ids) > 10000:
        return _unavailable("unavailable", "AUDIT_RESULTS_LIMIT"), []
    statement = (
        select(
            AuditResult.id,
            AuditResult.bundle_id,
            AuditResult.parse_id.label("audit_parse_id"),
            ParseResult.id.label("parse_id"),
            AuditResult.status,
            AuditResult.errors,
            AuditResult.result["execution"]["path"].as_string().label("extraction_path"),
            AuditResult.result["routing"]["category"].as_string().label("routing_category"),
            Resource.id.label("resource_id"),
            Resource.normalized_url,
            ResourceBundle.page_version_id,
            ResourceBundle.status.label("material_status"),
            ParseResult.title,
        )
        .join(ResourceBundle, ResourceBundle.id == AuditResult.bundle_id)
        .join(Resource, Resource.id == ResourceBundle.resource_id)
        .outerjoin(
            ParseResult,
            (ParseResult.id == AuditResult.parse_id)
            & (ParseResult.version_id == ResourceBundle.page_version_id),
        )
        .where(
            Resource.source_id == run.source_id,
            AuditResult.id.in_(ids),
            AuditResult.recipe_id == audit.recipe_id,
            AuditResult.evaluated_on == summary["evaluated_on"],
            AuditResult.generation == summary.get("generation", 0),
        )
        .order_by(Resource.normalized_url, AuditResult.id)
    )
    rows = []
    seen = set()
    for record in session.execute(statement).mappings():
        before = baseline.get(record["resource_id"])
        if before is not None and before["bundle"] == record["bundle_id"]:
            continue
        if record["bundle_id"] in seen:
            continue
        seen.add(record["bundle_id"])
        row = {
            **record,
            "change": "new" if before is None else "changed",
        }
        from .semantic_labels import current, errors_for, receipt

        saved = session.get(AuditResult, row["id"])
        from .temporal_context import current as temporal_current

        if not current(session, saved) or not temporal_current(session, saved):
            row["status"] = "review"
            row["errors"] = [*row["errors"], *errors_for(receipt(session, saved), row["bundle_id"])]
            if not temporal_current(session, saved):
                row["errors"].append(
                    {
                        "code": "TEMPORAL_CONTEXT_CHANGED",
                        "message": "Temporal policy requires re-audit",
                    }
                )
        dimensions = audit_dimensions(saved, row["material_status"])
        row["field_issue_codes"] = _issue_codes(dimensions["field_issues"])
        row["coverage_issue_codes"] = _issue_codes(dimensions["coverage_issues"])
        row["review_required"] = dimensions["review_required"] or row["status"] == "review"
        rows.append(row)
    accepted = [
        row["parse_id"]
        for row in rows
        if row["parse_id"] and row["status"] in {"success", "partial"} and not row["errors"]
    ]
    counts = (
        dict(
            session.execute(
                select(Position.parse_id, func.count())
                .where(
                    Position.parse_id.in_(accepted),
                )
                .group_by(Position.parse_id)
            ).all()
        )
        if accepted
        else {}
    )
    for row in rows:
        row["position_count"] = counts.get(row["parse_id"], 0)
    kinds = Counter(row["change"] for row in rows)
    delta = {
        "state": "pending" if audit.status == "running" else "ready",
        "reason": None,
        "notice_count": len(rows),
        "position_count": sum(counts.values()),
        "new_notices": kinds["new"],
        "changed_notices": kinds["changed"],
    }
    return delta, rows


def run_projection(session, run):
    if run is None:
        return None
    delta, _ = notice_rows(session, run)
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "summary": {
            "delta": delta,
            **{
                key: run.summary[key]
                for key in ("error", "repair_request_ids")
                if key in run.summary
            },
        },
    }


def run_detail(session, run, *, page=1):
    delta, rows = notice_rows(session, run)
    selected = rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    parse_ids = [row["parse_id"] for row in selected if row["position_count"]]
    grouped = defaultdict(list)
    invalid = set()
    if parse_ids:
        ranked = select(
            Position.id,
            Position.parse_id,
            Position.facts,
            func.row_number()
            .over(partition_by=Position.parse_id, order_by=(Position.ordinal, Position.id))
            .label("row"),
        )
        ranked = ranked.where(Position.parse_id.in_(parse_ids)).subquery()
        for position in session.execute(
            select(ranked).where(ranked.c.row <= POSITIONS_PER_NOTICE)
        ).mappings():
            try:
                facts = PositionFacts.from_dict(position["facts"]).as_dict()
            except (ValueError, TypeError, KeyError):
                invalid.add(position["parse_id"])
                continue
            grouped[position["parse_id"]].append(
                {"id": position["id"], "facts": {key: facts[key] for key in FACT_FIELDS}}
            )
    items = []
    for row in selected:
        positions = grouped[row["parse_id"]]
        items.append(
            {
                key: row[key]
                for key in (
                    "bundle_id",
                    "resource_id",
                    "change",
                    "status",
                    "material_status",
                    "extraction_path",
                    "routing_category",
                    "field_issue_codes",
                    "coverage_issue_codes",
                    "review_required",
                    "position_count",
                )
            }
            | {
                "title": row["title"] or "公告标题未识别",
                "url": row["normalized_url"],
                "error_codes": _issue_codes(row["errors"]),
                "positions": positions,
                "positions_truncated": row["position_count"] > len(positions),
                "positions_unavailable": row["parse_id"] in invalid
                or (row["audit_parse_id"] is not None and row["parse_id"] is None),
            }
        )
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "summary": {
            "delta": delta,
            **{key: run.summary[key] for key in ("error",) if key in run.summary},
        },
        "notices": {
            "items": items,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": len(rows),
            "has_next": page * PAGE_SIZE < len(rows),
        },
    }
