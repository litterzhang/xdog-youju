"""Evidence-bound issue recording and explicit human review; no SDK/worker imports."""

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from youju.api.common import ApiError
from youju.jobs import public_jobs
from youju.models import ParsingFeedback, Source
from youju.models.base import utc_now
from youju.onboarding.store import digest

FEEDBACK_FIELDS = frozenset(
    {
        "title",
        "conditions",
        "hiring_unit",
        "employer",
        "employment_type",
        "work_location",
        "headcount",
        "requirements_text",
        "deadline_on",
        "validity",
        "other",
    }
)
FEEDBACK_STATUSES = frozenset({"open", "reviewed", "resolved", "dismissed"})
REVIEW_STATUSES = FEEDBACK_STATUSES - {"open"}


def feedback_json(row):
    # Actor identifiers/review history stay server-side, never disclosed to other reporters.
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "position_id",
            "source_id",
            "field",
            "message",
            "expected_text",
            "status",
            "resolution_note",
            "context",
        )
    } | {"created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


def submit_feedback(session, position_id, subject, field, message, expected_text, day):
    if field not in FEEDBACK_FIELDS:
        raise ApiError(400, "INVALID_FEEDBACK_FIELD", "请选择支持反馈的字段。")
    payload_digest = digest(
        {
            "position_id": position_id,
            "field": field,
            "message": message,
            "expected_text": expected_text,
        }
    )
    # A retry must not reopen/duplicate a report, including after the row ceases to be current.
    existing = session.scalar(
        select(ParsingFeedback).where(
            ParsingFeedback.reporter_sub == subject,
            ParsingFeedback.payload_digest == payload_digest,
        )
    )
    if existing is not None:
        return existing, False
    row = next(public_jobs(session, day=day, include_partial=True, job_id=position_id), None)
    if row is None:
        raise ApiError(404, "JOB_NOT_FOUND", "岗位已不再公开，请刷新后反馈。")
    context = {
        key: deepcopy(row[key])
        for key in (
            "official_url",
            "bundle_id",
            "recipe_id",
            "audit_id",
            "audit_evaluated_on",
        )
    }
    context["title"] = row["facts"]["title"]["value"]
    context["field_value"] = deepcopy(
        row["validity"] if field == "validity" else row["facts"].get(field)
    )
    context["condition_logic"] = deepcopy(row["facts"]["condition_logic"])
    context["facts"] = deepcopy(row["facts"])
    context["evidence"] = {
        key: deepcopy(row["evidence"].get(key, []))
        for key in dict.fromkeys([field, "requirements_text"])
    }
    # Preserve related source text without synthesizing an education/degree requirement.
    context["requirements_text"] = row["facts"]["requirements_text"]["value"]
    now = utc_now()
    statement = (
        insert(ParsingFeedback)
        .values(
            id=uuid4().hex,
            position_id=position_id,
            source_id=row["source_id"],
            reporter_sub=subject,
            payload_digest=payload_digest,
            field=field,
            message=message,
            expected_text=expected_text,
            context=context,
            status="open",
            review_history=[],
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["reporter_sub", "payload_digest"])
    )
    inserted = session.execute(statement).rowcount == 1
    result = session.scalar(
        select(ParsingFeedback).where(
            ParsingFeedback.reporter_sub == subject,
            ParsingFeedback.payload_digest == payload_digest,
        )
    )
    return result, inserted


def list_feedback(session, *, position_id=None, subject=None, source_id=None, status="all", page=1):
    statement = select(ParsingFeedback)
    if position_id is not None:
        statement = statement.where(ParsingFeedback.position_id == position_id)
    if subject is not None:
        statement = statement.where(ParsingFeedback.reporter_sub == subject)
    if source_id is not None:
        if session.get(Source, source_id) is None:
            raise ApiError(404, "NOT_FOUND", "未找到对应来源。")
        statement = statement.where(ParsingFeedback.source_id == source_id)
    if status != "all":
        if status not in FEEDBACK_STATUSES:
            raise ApiError(400, "INVALID_QUERY", "反馈状态无效。")
        statement = statement.where(ParsingFeedback.status == status)
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = session.scalars(
        statement.order_by(
            ParsingFeedback.created_at.desc(),
            ParsingFeedback.id.desc(),
        )
        .offset((page - 1) * 50)
        .limit(50)
    )
    return {"items": [feedback_json(row) for row in rows], "page": page, "total": total}


def review_feedback(session, source_id, feedback_id, subject, status, note):
    if status not in REVIEW_STATUSES:
        raise ApiError(400, "INVALID_FEEDBACK_STATUS", "请选择已复核、已处理或无需修改。")
    row = session.get(ParsingFeedback, feedback_id)
    if row is None or row.source_id != source_id:
        raise ApiError(404, "FEEDBACK_NOT_FOUND", "未找到该来源的反馈。")
    if row.status == status and row.resolution_note == note:
        return row
    if len(row.review_history) >= 100:
        raise ApiError(409, "REVIEW_HISTORY_LIMIT", "反馈处理次数已达上限，请人工核查。")
    now = utc_now()
    row.review_history = [
        *row.review_history,
        {
            "previous_status": row.status,
            "status": status,
            "note": note,
            "reviewer_sub": subject,
            "at": now.isoformat(),
        },
    ]
    row.status, row.resolution_note, row.reviewer_sub, row.updated_at = status, note, subject, now
    session.flush()
    return row
