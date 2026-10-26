"""Registry administration and read-only onboarding projections.

HTTP mutations use the same cross-process source lock as the foreground CLI.
Onboarding only persists acquisition work; no HTTP handler crawls or calls models.
"""

import fcntl
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from youju.api.common import ApiError
from youju.database import create_sqlite_engine
from youju.models import (
    AuditResult,
    AuditRun,
    OnboardingResult,
    Organization,
    OrganizationScope,
    OrganizationType,
    Region,
    RegistryStatus,
    Resource,
    ResourceBundle,
    ResourceKind,
    ResourceScan,
    ReviewStatus,
    Seed,
    SeedKind,
    Source,
    SourcePipeline,
    SourceRecipeBinding,
    normalize_url,
)
from youju.models.base import utc_now
from youju.onboarding.lifecycle import incremental_json
from youju.onboarding.store import PipelineStore
from youju.poc.scope import DiscoveryScope
from youju.recovery import recovery_options


@contextmanager
def database(config, *, write=False):
    path = Path(config["DATABASE"])
    if not path.is_file():
        raise ApiError(503, "DATABASE_NOT_READY", "数据库尚未初始化，请联系管理员。")
    lock = None
    engine = None
    try:
        if write:
            lock = path.resolve().with_suffix(path.suffix + ".source.lock").open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ApiError(409, "SOURCE_BUSY", "来源任务正在执行，请稍后重试。") from None
        engine = create_sqlite_engine(path)
        yield engine
    except IntegrityError:
        raise ApiError(409, "REGISTRY_CONFLICT", "记录冲突，请刷新后检查已有数据。") from None
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_UNAVAILABLE", "数据库暂不可用。") from None
    finally:
        if engine:
            engine.dispose()
        if lock:
            lock.close()


def organization_json(session, org):
    scope = session.get(OrganizationScope, org.id)
    return {
        "id": org.id,
        "name": org.name,
        "organization_type": org.organization_type.value,
        "location_region_code": org.location_region_code,
        "root_domain": scope.root_domain if scope else None,
    }


def source_json(session, source):
    publisher = session.get(Organization, source.publisher_organization_id)
    return {
        "id": source.id,
        "name": source.name,
        "organization_id": source.publisher_organization_id,
        "organization_name": publisher.name if publisher else None,
        "base_url": source.base_url,
        "description": source.description,
        "status": source.status.value,
        "region_codes": [r.code for r in source.region_tags],
        "authority_evidence": source.authority_evidence,
        "verified_at": source.verified_at.isoformat() if source.verified_at else None,
        "seeds": [{"name": s.name, "url": s.resource.normalized_url} for s in source.seeds],
        "onboarding": pipeline_json(session, source.id),
        "incremental": incremental_json(session, source.id),
    }


def pipeline_json(session, source_id):
    pipeline = session.get(SourcePipeline, source_id)
    if pipeline is None:
        return {"stage": "not_started", "state": "not_started", "summary": {}, "acquisition": {}}
    binding = session.get(SourceRecipeBinding, source_id)
    frozen = session.get(OnboardingResult, source_id)
    summary = frozen.summary if frozen else pipeline.summary
    # Exclude resource arrays, model text, workflow files and physical workspace paths.
    groups = {
        "classification": (
            "processed",
            "remaining",
            "total",
            "rules",
            "local",
            "llm",
            "coverage",
            "llm_calls",
            "local_coverage",
            "by_method",
            "methods",
            "requires_review",
            "total_current_pages",
            "unassessed",
            "cheap_covered",
            "cheap_coverage",
            "target_met",
            "llm_reserved_calls",
            "llm_call_limit",
        ),
        "audit": (
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
            "extraction_statuses",
            "material_statuses",
            "verification",
        ),
    }
    projected = {
        group: {
            key: summary.get(group, {}).get(key) for key in keys if key in summary.get(group, {})
        }
        for group, keys in groups.items()
    }
    audit_id = summary.get("audit_id")
    audit = session.get(AuditRun, audit_id) if isinstance(audit_id, str) else None
    if not frozen and audit and audit.source_id == source_id and audit.summary.get("evaluated_on"):
        period = func.coalesce(
            func.json_extract(AuditResult.result, "$.validity.application_period.kind"), "missing"
        )
        projected["audit"]["application_period"] = dict(
            session.execute(
                select(period, func.count())
                .select_from(AuditResult)
                .join(ResourceBundle)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    AuditResult.recipe_id == audit.recipe_id,
                    AuditResult.evaluated_on == audit.summary["evaluated_on"],
                    AuditResult.generation == audit.summary.get("generation", 0),
                )
                .group_by(period)
            ).all()
        )
    projected["inventory_incomplete"] = bool(summary.get("inventory_incomplete"))
    if isinstance(summary.get("error"), dict):
        projected["error"] = {"code": str(summary["error"].get("code", "UNKNOWN"))[:80]}
    if isinstance(summary.get("authoring_job"), dict):
        job = summary["authoring_job"]
        projected["authoring_job"] = {k: job[k] for k in ("id", "status") if k in job}
    counts = dict(
        session.execute(
            select(ResourceScan.state, func.count())
            .join(Resource)
            .where(Resource.source_id == source_id)
            .group_by(ResourceScan.state)
        ).all()
    )
    recovery = recovery_options(session, pipeline)
    if not frozen and pipeline.state == "blocked" and "error" not in projected:
        projected["error"] = {
            "code": "INVENTORY_DEPTH_LIMIT"
            if recovery["blocking_limits"] == ["depth_limit"]
            else "INVENTORY_INCOMPLETE"
            if projected["inventory_incomplete"]
            else "SOURCE_BLOCKED"
        }
    return {
        "recovery": recovery,
        "stage": "completed" if frozen else pipeline.stage,
        "state": "completed" if frozen else pipeline.state,
        "completed_at": frozen.completed_at.isoformat() if frozen else None,
        "baseline_recipe_id": frozen.recipe_id if frozen else None,
        "baseline_audit_id": frozen.audit_id if frozen else None,
        "provenance": frozen.provenance if frozen else None,
        "maintenance": {
            "stage": pipeline.stage,
            "state": pipeline.state,
            "error": {"code": pipeline.summary.get("error", {}).get("code")},
        }
        if frozen and (pipeline.stage != "active" or pipeline.state == "blocked")
        else None,
        "summary": projected,
        "acquisition": frozen.acquisition if frozen else counts,
        "updated_at": (frozen.completed_at if frozen else pipeline.updated_at).isoformat(),
        "activated_at": pipeline.activated_at.isoformat() if pipeline.activated_at else None,
        "active_recipe_id": binding.active_recipe_id if binding else None,
        "candidate_recipe_id": binding.candidate_recipe_id if binding else None,
        "workspace_id": binding.workspace_id if binding else None,
        "active_revision_id": binding.active_revision_id if binding else None,
        "candidate_revision_id": binding.candidate_revision_id if binding else None,
        "generation": binding.generation if binding else 0,
    }


def require_record(session, model, identity):
    record = session.get(model, identity)
    if record is None:
        raise ApiError(404, "NOT_FOUND", "未找到对应记录。")
    return record


def source_url(value, scope):
    try:
        url = normalize_url(value)
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.port not in (None, 443) or not scope.contains(url):
            raise ValueError("outside scope")
    except (ValueError, UnicodeError):
        raise ApiError(
            400, "INVALID_SOURCE_URL", "来源必须使用组织根域名范围内的 HTTPS 公共地址。"
        ) from None
    return url


def create_organization(session, data):
    try:
        kind = OrganizationType(data["organization_type"])
        scope = DiscoveryScope(data["root_domain"])
    except (ValueError, UnicodeError):
        raise ApiError(400, "INVALID_ORGANIZATION", "请检查组织类型和可注册根域名。") from None
    region = data.get("location_region_code") or None
    if region:
        require_record(session, Region, region)
    org = Organization(name=data["name"], organization_type=kind, location_region_code=region)
    session.add(org)
    session.flush()
    session.add(OrganizationScope(organization_id=org.id, root_domain=scope.root_domain))
    session.flush()
    return organization_json(session, org)


def create_source(session, data):
    org = require_record(session, Organization, data["organization_id"])
    scope_record = require_record(session, OrganizationScope, org.id)
    scope = DiscoveryScope(scope_record.root_domain)
    url = source_url(data["base_url"], scope)
    if session.scalar(select(Source.id).where(Source.base_url == url)):
        raise ApiError(409, "REGISTRY_CONFLICT", "该来源地址已登记。")
    codes, urls = data["region_codes"], data["seed_urls"]
    for values in (codes, urls):
        if (
            not isinstance(values, list)
            or len(values) > 20
            or any(not isinstance(v, str) or not v.strip() or len(v) > 2048 for v in values)
            or len(set(values)) != len(values)
        ):
            raise ApiError(
                400, "INVALID_INPUT", "地区和种子地址必须为不重复的文本列表，最多 20 项。"
            )
    regions = [require_record(session, Region, code) for code in codes]
    seed_urls = [source_url(value, scope) for value in urls]
    if any(urlsplit(seed).netloc != urlsplit(url).netloc for seed in seed_urls):
        raise ApiError(400, "INVALID_SEED", "种子地址必须与来源同源。")
    if len(set(seed_urls)) != len(seed_urls):
        raise ApiError(400, "INVALID_SEED", "种子地址规范化后重复。")
    source = Source(
        name=data["name"],
        publisher_organization_id=org.id,
        base_url=url,
        description=data["description"],
        region_tags=regions,
    )
    session.add(source)
    session.flush()
    for ordinal, seed_url in enumerate(seed_urls, 1):
        if session.scalar(select(Resource.id).where(Resource.normalized_url == seed_url)):
            raise ApiError(409, "REGISTRY_CONFLICT", "种子地址已存在，需核对来源归属。")
        resource = Resource(original_url=seed_url, source_id=source.id, kind=ResourceKind.PAGE)
        session.add(resource)
        session.flush()
        session.add(
            Seed(
                name=f"{source.name} / {ordinal}",
                source_id=source.id,
                resource_id=resource.id,
                kind=SeedKind.LISTING,
            )
        )
    session.flush()
    return source_json(session, source)


def approve_source(session, source_id, evidence):
    source = require_record(session, Source, source_id)
    if source.status != RegistryStatus.CANDIDATE:
        raise ApiError(409, "INVALID_STATE", "仅可批准待审核来源。")
    scope_record = require_record(session, OrganizationScope, source.publisher_organization_id)
    source_url(source.base_url, DiscoveryScope(scope_record.root_domain))
    source.authority_evidence, source.verified_at = evidence, utc_now()
    source.status = RegistryStatus.ACTIVE
    for seed in source.seeds:
        seed.status = RegistryStatus.ACTIVE
        seed.resource.review_status = ReviewStatus.APPROVED
    session.flush()
    return source_json(session, source)


def start_onboarding(engine, config, source_id):
    with Session(engine) as session:
        source = require_record(session, Source, source_id)
        if source.status != RegistryStatus.ACTIVE:
            raise ApiError(409, "SOURCE_NOT_APPROVED", "请先核对并批准官方来源。")
    store = PipelineStore(engine, config["SOURCE_STORAGE"])
    try:
        store.start(source_id, config.get("ONBOARDING_LIMITS"))
    except ValueError:
        raise ApiError(
            409, "ONBOARDING_CONFLICT", "来源暂不能启动，请检查批准状态及现有任务。"
        ) from None
    with Session(engine) as session:
        return source_json(session, require_record(session, Source, source_id))


def recover_onboarding(engine, config, source_id, operation, limits, *, repair_context=None):
    from youju.onboarding.repair import request_repair

    with Session(engine) as session:
        source = require_record(session, Source, source_id)
        pipeline = require_record(session, SourcePipeline, source_id)
        if source.status != RegistryStatus.ACTIVE or (
            operation == "resume" and pipeline.state != "blocked"
        ):
            raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "仅可处理已批准来源的阻塞任务。")
        options = recovery_options(session, pipeline)
        if not options["can_resume" if operation == "resume" else "can_repair"]:
            raise ApiError(
                409,
                "RECOVERY_NOT_AVAILABLE",
                "当前任务不能安全恢复，请检查任务租约、限制或审计结果。",
            )
        current = options["limits"]
        for key, value in limits.items():
            if type(value) is not int or not current[key] <= value <= (
                100 if key == "max_depth" else 10000
            ):
                raise ApiError(
                    400, "INVALID_LIMIT", "深度只能增加至 100，资源上限只能增加至 10000。"
                )
        for key in options["suggested_limits"]:
            if key not in limits or limits[key] <= current[key]:
                raise ApiError(400, "LIMIT_INCREASE_REQUIRED", "需要提高触发阻塞的限制后才能继续。")
    store = PipelineStore(engine, config["SOURCE_STORAGE"])
    try:
        if operation == "resume":
            store.resume(source_id, **limits)
        else:
            request_repair(store, source_id, **(repair_context or {}))
    except ValueError:
        raise ApiError(
            409, "RECOVERY_CONFLICT", "任务状态已变化或缺少可用审计反馈，请刷新后检查。"
        ) from None
    with Session(engine) as session:
        return source_json(session, require_record(session, Source, source_id))
