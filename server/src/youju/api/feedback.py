"""Authenticated reporting and centrally authorized review; never auto-start an Agent."""

import re

from flask import Blueprint, current_app, g, request
from sqlalchemy.orm import Session

from youju.admin import database
from youju.feedback import feedback_json, list_feedback, review_feedback, submit_feedback

from .auth import admin_required, auth_required
from .common import ApiError, body, text
from .jobs import current_day

feedback_api = Blueprint("feedback", __name__)


def require_position_id(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ApiError(404, "JOB_NOT_FOUND", "未找到对应岗位。")


def page_args(*, admin=False):
    if set(request.args) - ({"page", "status"} if admin else {"page"}) or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise ApiError(400, "INVALID_QUERY", "反馈查询参数无效。")
    raw = request.args.get("page", "1")
    if not re.fullmatch(r"[0-9]{1,4}", raw) or not 1 <= int(raw) <= 1000:
        raise ApiError(400, "INVALID_PAGE", "反馈分页参数无效。")
    return int(raw)


@feedback_api.post("/jobs/<position_id>/feedback")
@auth_required
def submit(position_id):
    require_position_id(position_id)
    data = body(("field", "message"), ("expected_text",))
    field, message = text(data, "field", 32), text(data, "message", 1000)
    expected = text(data, "expected_text", 500, optional=True)
    # Separate issue writes can persist during a bounded source pass; no source mutation occurs.
    with database(current_app.config) as engine, Session(engine) as session, session.begin():
        row, created = submit_feedback(
            session, position_id, g.subject, field, message, expected, current_day()
        )
        return feedback_json(row), 201 if created else 200


@feedback_api.get("/jobs/<position_id>/feedback")
@auth_required
def own_feedback(position_id):
    require_position_id(position_id)
    page = page_args()
    with database(current_app.config) as engine, Session(engine) as session:
        return list_feedback(session, position_id=position_id, subject=g.subject, page=page)


@feedback_api.get("/admin/sources/<source_id>/feedback")
@admin_required
def source_feedback(source_id):
    page = page_args(admin=True)
    with database(current_app.config) as engine, Session(engine) as session:
        return list_feedback(
            session,
            source_id=source_id,
            status=request.args.get("status", "all"),
            page=page,
        )


@feedback_api.post("/admin/sources/<source_id>/feedback/<feedback_id>/review")
@admin_required
def review(source_id, feedback_id):
    data = body(("status", "resolution_note"))
    status, note = text(data, "status", 32), text(data, "resolution_note", 1000)
    with database(current_app.config) as engine, Session(engine) as session, session.begin():
        row = review_feedback(session, source_id, feedback_id, g.subject, status, note)
        return feedback_json(row)
