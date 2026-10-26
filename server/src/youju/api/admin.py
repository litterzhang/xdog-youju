"""Permission-gated registry APIs; mutation bodies never contain worker settings."""

import re

from flask import Blueprint, current_app, request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju import admin
from youju.models import IncrementalRun, Organization, Region, RegistryStatus, Source
from youju.onboarding.lifecycle import incremental_json, set_incremental

from .auth import admin_required
from .common import ApiError, body, text

admin_api = Blueprint("admin", __name__)


def page_args():
    try:
        page = int(request.args.get("page", "1"))
        if not 1 <= page <= 10000:
            raise ValueError
    except ValueError:
        raise ApiError(400, "INVALID_PAGE", "分页参数无效。") from None
    return page


@admin_api.get("/regions")
@admin_api.get("/organizations")
@admin_api.get("/sources")
@admin_required
def list_registry():
    kind = request.path.rsplit("/", 1)[-1]
    model = {"regions": Region, "organizations": Organization, "sources": Source}[kind]
    page = page_args()
    with admin.database(current_app.config) as engine, Session(engine) as session:
        rows = session.scalars(select(model).order_by(model.name).offset((page - 1) * 50).limit(50))
        values = [
            {"code": r.code, "name": r.name}
            if kind == "regions"
            else admin.organization_json(session, r)
            if kind == "organizations"
            else admin.source_json(session, r)
            for r in rows
        ]
        return {
            "items": values,
            "page": page,
            "total": session.scalar(select(func.count()).select_from(model)),
        }


@admin_api.post("/regions")
@admin_required
def create_region():
    data = body(("code", "name"))
    code, name = text(data, "code", 32), text(data, "name", 128)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code):
        raise ApiError(400, "INVALID_REGION", "地区代码格式无效。")
    with (
        admin.database(current_app.config, write=True) as engine,
        Session(engine) as session,
        session.begin(),
    ):
        session.add(Region(code=code, name=name))
        session.flush()
        return {"code": code, "name": name}, 201


@admin_api.post("/organizations")
@admin_required
def create_organization():
    data = body(("name", "organization_type", "root_domain"), ("location_region_code",))
    cleaned = {k: text(data, k, 256) for k in ("name", "organization_type", "root_domain")}
    cleaned["location_region_code"] = text(data, "location_region_code", 32, optional=True)
    with (
        admin.database(current_app.config, write=True) as engine,
        Session(engine) as session,
        session.begin(),
    ):
        return admin.create_organization(session, cleaned), 201


@admin_api.post("/sources")
@admin_required
def create_source():
    data = body(
        ("name", "organization_id", "base_url", "region_codes", "seed_urls"), ("description",)
    )
    cleaned = {
        k: text(data, k, 2048 if k == "base_url" else 256)
        for k in ("name", "organization_id", "base_url")
    }
    cleaned.update(
        description=text(data, "description", 2000, optional=True),
        region_codes=data["region_codes"],
        seed_urls=data["seed_urls"],
    )
    with (
        admin.database(current_app.config, write=True) as engine,
        Session(engine) as session,
        session.begin(),
    ):
        return admin.create_source(session, cleaned), 201


@admin_api.get("/sources/<source_id>")
@admin_required
def source_detail(source_id):
    with admin.database(current_app.config) as engine, Session(engine) as session:
        return admin.source_json(session, admin.require_record(session, Source, source_id))


@admin_api.get("/sources/<source_id>/parsing-summary")
@admin_required
def source_parsing_summary(source_id):
    from youju.onboarding.source_summary import parsing_summary

    with admin.database(current_app.config) as engine, Session(engine) as session:
        admin.require_record(session, Source, source_id)
        return parsing_summary(session, source_id)


@admin_api.post("/sources/<source_id>/approve")
@admin_required
def approve(source_id):
    data = body(("authority_evidence",))
    evidence = text(data, "authority_evidence", 2000)
    with (
        admin.database(current_app.config, write=True) as engine,
        Session(engine) as session,
        session.begin(),
    ):
        return admin.approve_source(session, source_id, evidence)


@admin_api.post("/sources/<source_id>/onboard")
@admin_required
def onboard(source_id):
    body()
    with admin.database(current_app.config, write=True) as engine:
        return admin.start_onboarding(engine, current_app.config, source_id), 202


@admin_api.post("/sources/<source_id>/resume")
@admin_required
def resume(source_id):
    limits = body((), ("max_depth", "max_resources"))
    with admin.database(current_app.config, write=True) as engine:
        return admin.recover_onboarding(
            engine, current_app.config, source_id, "resume", limits
        ), 202


@admin_api.post("/sources/<source_id>/repair")
@admin_required
def repair(source_id):
    context = body(optional=("audit_id", "previous_request_id"))
    for value in context.values():
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ApiError(400, "INVALID_REPAIR_CONTEXT", "修复必须绑定有效的审查或请求 ID。")
    with admin.database(current_app.config, write=True) as engine:
        return admin.recover_onboarding(
            engine, current_app.config, source_id, "repair", {}, repair_context=context
        ), 202


@admin_api.get("/sources/<source_id>/incremental")
@admin_required
def incremental_detail(source_id):
    with admin.database(current_app.config) as engine, Session(engine) as session:
        admin.require_record(session, Source, source_id)
        return incremental_json(session, source_id, history=True)


@admin_api.get("/sources/<source_id>/incremental/runs/<run_id>")
@admin_required
def incremental_run(source_id, run_id):
    from youju.onboarding.incremental_view import run_detail

    page = page_args()
    with admin.database(current_app.config) as engine, Session(engine) as session:
        admin.require_record(session, Source, source_id)
        run = admin.require_record(session, IncrementalRun, run_id)
        if run.source_id != source_id:
            raise ApiError(404, "NOT_FOUND", "未找到对应批次。")
        return run_detail(session, run, page=page)


@admin_api.post("/sources/<source_id>/incremental")
@admin_required
def incremental_toggle(source_id):
    data = body(("enabled",))
    if type(data["enabled"]) is not bool:
        raise ApiError(400, "INVALID_INPUT", "enabled 必须为布尔值。")
    # This independent switch can be written while a bounded acquisition pass runs.
    # It does not mutate the registry, recipes, leases or existing acquisition slots.
    with admin.database(current_app.config) as engine, Session(engine) as session, session.begin():
        source = admin.require_record(session, Source, source_id)
        if data["enabled"] and source.status != RegistryStatus.ACTIVE:
            raise ApiError(409, "SOURCE_NOT_APPROVED", "只有已批准来源可以开启增量抓取。")
        try:
            return set_incremental(session, source_id, data["enabled"])
        except ValueError:
            raise ApiError(409, "ONBOARDING_NOT_COMPLETE", "请先完成来源接入或维护任务。") from None
