"""Saved canonical candidates are public with uncertainty tags, not audit-gated.

Keep one latest candidate-bearing audit per notice. Failed refreshes never erase
readable facts; stale material, rejected enrichment and cancellation stay explicit.
Source registration remains a trust boundary. No model calls or writes on reads.
"""

from copy import deepcopy
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.job_filters import EMPLOYMENT_FILTERS as EMPLOYMENT_FILTERS
from youju.job_filters import FACETS, additional_matches, condition_matches
from youju.models import (
    AuditResult,
    AuditRun,
    PageAssessment,
    ParseResult,
    Position,
    RegistryStatus,
    Resource,
    ResourceBundle,
    ResourceScan,
    Source,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.onboarding.auditing import (
    AUDIT_POLICY,
    _available_dependency_current,
    associated_change_issues,
    audit_dimensions,
    position_field_issues,
)
from youju.positions.facts import PositionFacts


def _education_matches(conditions, selected):
    return condition_matches(conditions, "education", selected)


def job_validity(facts: PositionFacts, day: date, *, now_iso=None, field_issues=()):
    """Compare only this job's explicit source deadline. Never inspect a notice.

    An inferred/missing year stays unknown. Only structured endpoints participate;
    their own tags matter even in mixed-certainty ranges.
    Ordinary minute precision retains the stated minute; 24:00 is exactly next
    midnight, with no extra minute. Date-only endpoints retain the whole date.
    This is saved-job application timing, not verified hiring or eligibility.
    """
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.fromisoformat(now_iso) if now_iso else datetime.combine(day, time(), tz)
    if now.utcoffset() is None or now.astimezone(tz).date() != day:
        raise ValueError("Public job validity needs a consistent aware clock")
    value = {
        "verification_scope": "position",
        "status": "unknown",
        "deadline": None,
        "reason": "该岗位截止日期缺失或年份待确认，不能据此判断报名已结束。",
        "evidence_ids": [],
        "evaluated_at": now.isoformat(),
        "current_hiring_status": "not_verified",
    }
    if any(
        issue.get("code")
        in {
            "APPLICATION_DATE_CONFLICT",
            "APPLICATION_RULE_CONFLICT",
            "AMBIGUOUS_APPLICATION_DATE",
            "INVALID_APPLICATION_DATE",
            "INVALID_APPLICATION_DATES",
            "UNSTRUCTURED_APPLICATION_DATE",
            "NEGATED_APPLICATION_DATE",
            "UNSUPPORTED_APPLICATION_TIME",
            "UNSUPPORTED_APPLICATION_SECONDS",
            "UNFINISHED_APPLICATION_EXCEPTION",
        }
        for issue in field_issues
    ):
        value["reason"] = "该岗位报名日期或规则仍有冲突、缺失或解析疑点，需核验原文。"
        return value
    dates = facts.fields["application_dates"].value
    end = dates.get("end") if dates else None
    if not end or end["tag"] != "source" or end["year_status"] != "explicit" or not end["date"]:
        return value
    deadline, clock = end["date"], end["time"]
    if facts.fields["application_rule"].value in {"open_ended", "until_filled"}:
        value["reason"] = "该岗位固定日期与长期或招满规则冲突，需核验原文。"
        return value
    end_day = date.fromisoformat(deadline)
    expired = end_day < day
    if clock:
        boundary = (
            datetime.combine(end_day + timedelta(days=1), time(), tz)
            if clock == "24:00"
            else datetime.combine(end_day, time.fromisoformat(clock), tz) + timedelta(minutes=1)
        )
        expired = now >= boundary
    value.update(
        status="expired" if expired else "not_expired",
        deadline=deadline,
        reason="该岗位明确截止时间已过；不代表已核验招聘状态。"
        if expired
        else "该岗位明确截止时间尚未经过；不保证当前仍在招聘或符合报名资格。",
    )
    return value


def _admission(session, binding, recipe, day):
    """Use the last *passing* completed run, not an in-progress summary pointer."""
    generation = binding.generation
    runs = session.scalars(
        select(AuditRun)
        .where(
            AuditRun.source_id == binding.source_id,
            AuditRun.recipe_id == recipe.id,
            AuditRun.status == "complete",
            AuditRun.finished_at.is_not(None),
        )
        .order_by(AuditRun.finished_at.desc(), AuditRun.id.desc())
    )
    for run in runs:
        summary = run.summary
        admission = summary.get("admission", {})
        if (
            summary.get("passed") is not True
            or summary.get("audit_policy") != AUDIT_POLICY
            or summary.get("generation", 0) != generation
            or admission.get("policy") != "quarantine-v1"
            or admission.get("blocking_reasons")
            or admission.get("source_errors_by_code")
            or summary.get("remaining", 0) != 0
        ):
            continue
        try:
            evaluated = date.fromisoformat(summary.get("evaluated_on"))
        except (TypeError, ValueError):
            continue
        if evaluated > day:
            continue
        admitted = {
            row["id"]: row
            for row in summary.get("results", [])
            if row.get("id")
            and row.get("status") in {"success", "partial"}
            and not row.get("errors")
        }
        return run, admitted
    return None, {}


def public_jobs(
    session: Session,
    *,
    day: date,
    include_partial=True,
    search="",
    education="all",
    employment="all",
    deadline="all",
    degree="all",
    staffing="all",
    experience="all",
    job_id=None,
    now_iso=None,
):
    """Display canonical candidates; explicit user facets may narrow the list.

    Audits still govern recipe activation/repair, never candidate visibility.
    Invalid facts are not jobs. Older versions remain stored, not duplicate rows.
    """
    if any(
        value not in FACETS[key]
        for key, value in {
            "education": education,
            "employment": employment,
            "deadline": deadline,
            "degree": degree,
            "staffing": staffing,
            "experience": experience,
        }.items()
    ):
        raise ValueError("Invalid public job filter")
    latest_candidate = (
        select(AuditResult.id)
        .join(ResourceBundle, ResourceBundle.id == AuditResult.bundle_id)
        .where(
            ResourceBundle.resource_id == Resource.id,
            AuditResult.result["schema_version"].as_string() == "position-facts-v3",
            AuditResult.parse_id.in_(select(Position.parse_id)),
        )
        .order_by(AuditResult.created_at.desc(), AuditResult.id.desc())
        .correlate(Resource)
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        select(
            Position,
            Resource,
            ResourceScan,
            ResourceBundle,
            AuditResult,
            Source,
            SourceRecipeBinding,
            SourceRecipe,
        )
        .join(ParseResult, ParseResult.id == Position.parse_id)
        .join(AuditResult, AuditResult.parse_id == ParseResult.id)
        .join(ResourceBundle, ResourceBundle.id == AuditResult.bundle_id)
        .join(Resource, Resource.id == ResourceBundle.resource_id)
        .outerjoin(ResourceScan, ResourceScan.resource_id == Resource.id)
        .join(Source, Source.id == Resource.source_id)
        .outerjoin(SourceRecipeBinding, SourceRecipeBinding.source_id == Source.id)
        .join(SourceRecipe, SourceRecipe.id == AuditResult.recipe_id)
        .where(
            Source.status == RegistryStatus.ACTIVE,
            SourceRecipe.source_id == Source.id,
            AuditResult.id == latest_candidate,
        )
        .order_by(Source.id, Resource.id, Position.ordinal, Position.id)
    )
    if job_id is not None:
        statement = statement.where(Position.id == job_id)
    admissions, bundle_states = {}, {}
    for position, resource, scan, bundle, audit, source, binding, recipe in session.execute(
        statement
    ):
        if audit.result.get("schema_version") != "position-facts-v3":
            continue
        try:
            facts = PositionFacts.from_dict(position.facts)
        except (TypeError, ValueError, KeyError):
            continue
        if not include_partial and bundle.status != "complete":
            continue
        dimensions = audit_dimensions(audit, bundle.status)
        dimensions["field_issues"] = position_field_issues(audit.result, position.ordinal)
        if bundle.id not in bundle_states:
            bundle_states[bundle.id] = _display_state(session, resource, scan, bundle, audit)
        state = bundle_states[bundle.id]
        dimensions["review_required"] = bool(
            audit.status == "review"
            or dimensions["field_issues"]
            or state["review"]
            or audit.errors
        )
        failure = audit.result.get("execution", {}).get("failure")
        if failure or state["failed"]:
            dimensions["extraction_status"] = "failed"
            dimensions["repair_required"] = True
        if not _education_matches(facts.conditions, education):
            continue
        arrangement = facts.fields["employment_type"].value
        staffing_value = facts.fields["staffing_status"].value
        if employment == "public_institution_staff":
            if staffing_value != "established":
                continue
        elif employment == "unknown":
            if arrangement is not None or staffing_value is not None:
                continue
        elif employment != "all" and arrangement != employment:
            continue
        if search:
            haystack = [
                source.name,
                *[
                    str(facts.fields[name].value or "")
                    for name in (
                        "title",
                        "position_code",
                        "hiring_unit",
                        "employer",
                        "requirements_text",
                        "work_location",
                    )
                ],
                *[str(item.fact.value or "") for item in facts.conditions],
            ]
            if not any(search.casefold() in text.casefold() for text in haystack):
                continue
        admission_key = (source.id, recipe.id)
        if admission_key not in admissions:
            admissions[admission_key] = (
                _admission(session, binding, recipe, day)
                if binding and binding.active_recipe_id == recipe.id
                else (None, {})
            )
        run, admitted = admissions[admission_key]
        if (
            run is None
            or audit.id not in admitted
            or audit.errors
            or (binding and audit.generation != binding.generation)
            or recipe.validation.get("errors")
        ):
            dimensions["review_required"] = True
        validity = job_validity(
            facts,
            day,
            now_iso=now_iso,
            field_issues=dimensions["field_issues"],
        )
        if not additional_matches(
            facts,
            validity,
            deadline=deadline,
            degree=degree,
            staffing=staffing,
            experience=experience,
        ):
            continue
        tags = [
            "expired"
            if validity["status"] == "expired"
            else "deadline_not_passed"
            if validity["status"] == "not_expired"
            else "deadline_unknown"
        ]
        if dimensions["extraction_path"] == "migration":
            tags.append("migrated")
        if state["cancelled"]:
            tags.append("cancelled")
        if dimensions["repair_required"]:
            tags.append("parse_failed")
        elif dimensions["extraction_status"] != "parsed" or dimensions["coverage_issues"]:
            tags.append("partial_parse")
        if dimensions["review_required"]:
            tags.append("needs_review")
        if bundle.status != "complete":
            tags.append("material_incomplete")
        if state["stale"]:
            tags.append("material_stale")
        if state["refresh_pending"]:
            tags.append("refresh_pending")
        if any(f.tag == "ai_inferred" for f in facts.fields.values()) or any(
            condition.fact.tag == "ai_inferred" for condition in facts.conditions
        ):
            tags.append("ai_inferred")
        if any(f.tag == "unknown" for f in facts.fields.values()) or any(
            condition.fact.tag == "unknown" for condition in facts.conditions
        ):
            tags.append("fields_unknown")
        # Cost routing belongs in source diagnostics, never public business facts.
        dimensions.pop("routing_category", None)
        # Quick/detailed is execution provenance, never a quality or visibility gate.
        yield {
            "id": position.id,
            "source_id": source.id,
            "source_name": source.name,
            "official_url": resource.normalized_url,
            "bundle_id": bundle.id,
            "recipe_id": recipe.id,
            "audit_id": audit.id,
            "admission_audit_id": run.id if run and audit.id in admitted else None,
            "display_tags": tags,
            "audit_evaluated_on": audit.evaluated_on,
            "audit_evaluated_at": audit.created_at.isoformat(),
            "expiry_evaluated_on": day.isoformat(),
            "expiry_precision": "minute"
            if validity["deadline"] and facts.fields["application_dates"].value["end"]["time"]
            else "day",
            "last_successful_check": scan.last_success_at.isoformat()
            if scan and scan.last_success_at
            else None,
            "facts": facts.as_dict(),
            "evidence": deepcopy(position.evidence),
            "validity": validity,
            "review_status": position.review_status,
            **dimensions,
        }


def _display_state(session, resource, scan, bundle, audit):
    """Diagnostics label saved facts; they never claim the facts are confirmed."""
    from youju.onboarding.semantic_labels import current as semantic_current
    from youju.onboarding.temporal_context import change_current, change_review
    from youju.onboarding.temporal_context import current as temporal_current

    latest = session.scalar(
        select(AuditResult)
        .join(ResourceBundle, ResourceBundle.id == AuditResult.bundle_id)
        .where(ResourceBundle.resource_id == resource.id)
        .order_by(AuditResult.created_at.desc(), AuditResult.id.desc())
        .limit(1)
    )
    assessment = session.scalar(
        select(PageAssessment)
        .where(PageAssessment.bundle_id == bundle.id)
        .order_by(PageAssessment.created_at.desc(), PageAssessment.id.desc())
        .limit(1)
    )
    parse = session.get(ParseResult, audit.parse_id)
    change = change_review(session, resource.id)
    cancelled = any(
        row and (row.status == "cancelled" or row.result.get("disposition") == "cancelled")
        for row in (audit, latest)
    ) or (parse is not None and parse.status == "cancelled")
    classification = assessment.result if assessment else {}
    cancelled = (
        cancelled
        or classification.get("business_type") == "cancellation"
        or (
            change is not None
            and change.effect == "cancellation"
            and change_current(session, change, bundle.id)
        )
    )
    stale = (
        not scan
        or scan.bundle_id != bundle.id
        or scan.last_version_id != bundle.page_version_id
        or not all(_available_dependency_current(session, dep) for dep in bundle.dependencies)
    )
    failed = any(row and row.status == "failed" for row in (audit, latest))
    review = (
        bool(latest and latest.id != audit.id)
        or not semantic_current(session, audit)
        or not temporal_current(session, audit)
        or not assessment
        or classification.get("requires_review", False)
        or classification.get("business_type") != "recruitment"
        or bool(
            associated_change_issues(
                session,
                bundle.id,
                dependencies=audit.result.get("fact_dependencies"),
                disposition=audit.result.get("disposition"),
            )
        )
    )
    return {
        "review": review or cancelled or stale or failed,
        "cancelled": bool(cancelled),
        "stale": bool(stale),
        "failed": failed,
        "refresh_pending": not scan or scan.state != "done",
    }
