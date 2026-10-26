"""Public saved-job reads. Parent must register jobs_api with url_prefix='/api'."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, request
from sqlalchemy.orm import Session

from youju.admin import database
from youju.job_filters import FACETS
from youju.jobs import public_jobs
from youju.models.base import utc_now

from .common import ApiError

jobs_api = Blueprint("jobs", __name__)


def current_day(stamp: datetime | None = None):
    return (stamp or utc_now()).astimezone(ZoneInfo("Asia/Shanghai")).date()


def _arguments(*, detail=False):
    allowed = (
        {"include_partial"} if detail else {"page", "page_size", "q", "include_partial", *FACETS}
    )
    if set(request.args) - allowed or any(len(request.args.getlist(k)) != 1 for k in request.args):
        raise ApiError(400, "INVALID_QUERY", "查询参数无效。")
    partial = request.args.get("include_partial", "true")
    if partial not in {"true", "false"}:
        raise ApiError(400, "INVALID_QUERY", "部分材料选项必须为 true 或 false。")
    values = {"include_partial": partial == "true"}
    if detail:
        return values
    for name, allowed_values in FACETS.items():
        selected = request.args.get(name, "all")
        if selected not in allowed_values:
            raise ApiError(400, "INVALID_QUERY", "岗位筛选选项无效。")
        values[name] = selected
    for name, default, maximum in (("page", "1", 1000), ("page_size", "20", 50)):
        raw = request.args.get(name, default)
        if not re.fullmatch(r"[0-9]{1,4}", raw) or not 1 <= int(raw) <= maximum:
            raise ApiError(400, "INVALID_QUERY", "分页参数超出允许范围。")
        values[name] = int(raw)
    raw = request.args.get("q", "")
    if len(raw) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ApiError(400, "INVALID_QUERY", "搜索词最多 100 字，不能包含控制字符。")
    values["search"] = raw.strip()
    return values


@jobs_api.get("/jobs")
def list_jobs():
    args = _arguments()
    stamp = utc_now()
    day = current_day(stamp)
    page, size = args.pop("page"), args.pop("page_size")
    offset, items, total = (page - 1) * size, [], 0
    with database(current_app.config) as engine, Session(engine) as session:
        for row in public_jobs(session, day=day, now_iso=stamp.isoformat(), **args):
            if offset <= total < offset + size:
                items.append(row)
            total += 1
    return {
        "items": items,
        "page": page,
        "page_size": size,
        "total": total,
        "has_next": page < 1000 and offset + size < total,
        "include_partial": args["include_partial"],
        "filters": {key: args[key] for key in FACETS},
        "evaluated_on": day.isoformat(),
        "timezone": "Asia/Shanghai",
    }


@jobs_api.get("/jobs/<job_id>")
def job_detail(job_id):
    args = _arguments(detail=True)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise ApiError(404, "JOB_NOT_FOUND", "岗位不存在或当前未公开。")
    stamp = utc_now()
    with database(current_app.config) as engine, Session(engine) as session:
        row = next(
            public_jobs(
                session, day=current_day(stamp), now_iso=stamp.isoformat(), job_id=job_id, **args
            ),
            None,
        )
    if row is None:
        raise ApiError(404, "JOB_NOT_FOUND", "岗位不存在或当前未公开。")
    return row
