"""Authenticated source-side saved queries; no source DB import in Recipe tools.

Source state is read locally; authoritative task metadata comes only through an
injected control-plane port. No sidecar DB access, locks, parsing or storage writes.
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import httpx
from flask import Blueprint, Response, request
from sqlalchemy import LargeBinary, create_engine, exists, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from youju.api.common import ApiError
from youju.models import (
    AuditResult,
    PageAssessment,
    RegistryStatus,
    RepairRequest,
    Resource,
    ResourceBundle,
    ResourceScan,
    ResourceVersion,
    Source,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.onboarding import SemanticCaseExposure, SemanticCaseLabel
from youju.onboarding.recipe_client import RecipeRPCClient, RecipeRPCError

MAX_RESPONSE = 60 * 1024
MAX_SAVED = 16 * 1024 * 1024
CHUNK_CHARS = 2000
ACTIVE_TASKS = {"queued", "waiting_init", "running"}


def _denied():
    return ApiError(403, "RUNTIME_QUERY_DENIED", "Runtime query is not authorized")


def _unavailable():
    return ApiError(503, "RUNTIME_QUERY_UNAVAILABLE", "Saved query data is unavailable")


def _invalid():
    return ApiError(400, "RUNTIME_QUERY_INVALID", "Invalid bounded query request")


@contextmanager
def _readonly(path):
    """mode=ro cannot create a missing DB; query_only also forbids DML/DDL."""
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise _unavailable()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, autocommit=False, timeout=2)
    try:
        # Explicit read transactions keep all authorization/data reads in a snapshot.
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


class TaskStatusAuthorizer(Protocol):
    """Trusted host port; return authoritative workspace/task metadata, never a grant.

    All adapters (including host/test injections) pass through the same validation
    below. Implementations must authenticate the delegated owner, bound their I/O,
    and avoid assignment locks: the caller may already hold the execution lease.
    """

    async def get_status(
        self, *, task_id: str, workspace_id: str, owner: str
    ) -> tuple[dict, dict]: ...


class RPCTaskStatusAuthorizer:
    """Use existing authenticated metadata RPC, not RecipeService/private storage."""

    def __init__(self, client: RecipeRPCClient):
        self.client = client

    async def get_status(self, *, task_id, workspace_id, owner):
        if self.client.caller != owner:
            raise _denied()
        try:
            workspace = await self.client.call("GetWorkspace", workspace_id=workspace_id)
            # Task last: observe finalization/cancellation after the workspace read.
            # No result/files RPC, execution lease or request-assignment lock.
            task = await self.client.call("GetTask", task_id=task_id, after=0, limit=1)
        except RecipeRPCError as error:
            if error.transient:
                raise _unavailable() from None
            raise _denied() from None
        return workspace, task


def _configured_authorizer(config):
    if "RECIPE_RUNTIME_QUERY_AUTHORIZER" in config:
        return config["RECIPE_RUNTIME_QUERY_AUTHORIZER"]
    # No token means unavailable, not unauthenticated RPC or local DB fallback.
    token = config.get("RECIPE_RPC_TOKEN")
    owner = config.get("RECIPE_RUNTIME_QUERY_OWNER", "source-server")
    if not isinstance(token, str) or not token or not isinstance(owner, str) or not owner:
        raise _unavailable()
    return RPCTaskStatusAuthorizer(
        RecipeRPCClient(config.get("RECIPE_RPC_URL", "http://127.0.0.1:8090"), token, caller=owner)
    )


def _task_authorized(authorizer, data, *, owner):
    if not isinstance(owner, str) or not owner or authorizer is None:
        raise _unavailable()
    try:
        workspace, task = asyncio.run(
            authorizer.get_status(
                task_id=data["task_id"], workspace_id=data["workspace_id"], owner=owner
            )
        )
    except ApiError:
        raise
    except Exception:
        # RPC/auth/transport failures and injected adapter errors never disclose
        # headers, paths, remote errors or raw provider exception chains.
        raise _unavailable() from None
    if not isinstance(workspace, dict) or not isinstance(task, dict):
        raise _denied()
    assignment = task.get("assignment")
    if not isinstance(assignment, dict):
        raise _denied()
    if (
        workspace.get("workspace_id") != data["workspace_id"]
        or workspace.get("source_id") != data["source_id"]
        or workspace.get("owner_id") != owner
        or workspace.get("lifecycle") != "managed"
        or task.get("id") != data["task_id"]
        or task.get("workspace_id") != data["workspace_id"]
        or task.get("input_revision_id") != data["input_revision"]
        or task.get("status") != "running"
        or task.get("cancel_requested") is not False
        or any(
            assignment.get(field) is not True
            for field in ("active", "assigned", "input_revision_valid", "session_current")
        )
        or assignment.get("source_id") != data["source_id"]
        or assignment.get("owner_id") != owner
        or assignment.get("lifecycle") != "managed"
        or type(task.get("base_session_revision")) is not int
        or type(workspace.get("session_revision")) is not int
        or task["base_session_revision"] != workspace["session_revision"]
        or task.get("revision_id") is not None
        or task.get("result_ref") is not None
    ):
        raise _denied()
    kind = task.get("kind")
    if kind not in {"init", "build", "repair"}:
        raise _denied()
    if kind == "init" and workspace.get("init_task_id") != data["task_id"]:
        raise _denied()
    return kind


def _authorized(session, data, kind):
    source = session.get(Source, data["source_id"])
    binding = session.get(SourceRecipeBinding, data["source_id"])
    canonical = hashlib.sha256(
        json.dumps(
            {"source_id": data["source_id"]},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    if (
        source is None
        or source.status != RegistryStatus.ACTIVE
        or not source.verified_at
        or not source.authority_evidence
        or binding is None
        or binding.workspace_id != data["workspace_id"]
        or data["workspace_id"] != canonical
    ):
        raise _denied()
    pipeline = session.get(SourcePipeline, source.id)
    pending = pipeline.summary.get("authoring_job", {}) if pipeline else {}
    if not isinstance(pending, dict):
        raise _denied()
    context = pending.get("context", {})
    if (
        kind in {"init", "build"}
        and pipeline is not None
        and pipeline.stage == "authoring"
        and pipeline.state == "running"
        and pending.get("status") in {"preparing", *ACTIVE_TASKS}
        and not pending.get("accepted")
        and not pending.get("revision_id")
        and pending.get("request_id") == pipeline.lease_token
        and bool(pipeline.lease_token)
        and pending.get("workspace_id") == data["workspace_id"]
        and pending.get("init_task_id" if kind == "init" else "id") == data["task_id"]
        and pending.get("input_revision_id") == data["input_revision"]
        and isinstance(context, dict)
        and context.get("source_id") == source.id
    ):
        return context.get("classifier_key")
    if kind != "repair" or pipeline is None or pipeline.stage not in {"active", "audit"}:
        raise _denied()
    # Only the actual preparing/submitted states admit repair queries. Preparing
    # without a registered task ID never grants wildcard access to init/other jobs.
    for repair in session.scalars(
        select(RepairRequest).where(
            RepairRequest.source_id == source.id,
            RepairRequest.status.in_(["preparing", "submitted"]),
            RepairRequest.context["workspace_id"].as_string() == data["workspace_id"],
            RepairRequest.context["task_id"].as_string() == data["task_id"],
            RepairRequest.context["input_revision_id"].as_string() == data["input_revision"],
        )
    ):
        context = repair.context
        if not isinstance(context, dict):
            continue
        base = session.get(SourceRecipe, repair.base_recipe_id)
        if (
            base is not None
            and base.source_id == source.id
            and binding.active_recipe_id == context.get("baseline_recipe_id", repair.base_recipe_id)
            and all(
                context.get(key) == data[field]
                for key, field in (
                    ("workspace_id", "workspace_id"),
                    ("task_id", "task_id"),
                    ("input_revision_id", "input_revision"),
                )
            )
        ):
            return repair.classifier_key
    raise _denied()


def _current_notices(source_id, classifier_key):
    latest = select(PageAssessment.id).where(PageAssessment.bundle_id == ResourceBundle.id)
    if classifier_key:
        latest = latest.where(PageAssessment.classifier_key == classifier_key)
    latest = latest.order_by(PageAssessment.created_at.desc(), PageAssessment.id.desc()).limit(1)
    label = aliased(SemanticCaseLabel)
    latest_label = (
        select(label.id)
        .where(label.bundle_id == ResourceBundle.id)
        .order_by(label.created_at.desc(), label.id.desc())
        .limit(1)
        .correlate(ResourceBundle)
    )
    hidden = exists(
        select(SemanticCaseLabel.id).where(
            SemanticCaseLabel.id == latest_label.scalar_subquery(),
            SemanticCaseLabel.split == "holdout",
        )
    )
    exposed = exists(
        select(SemanticCaseExposure.label_id)
        .join(SemanticCaseLabel, SemanticCaseLabel.id == SemanticCaseExposure.label_id)
        .where(SemanticCaseLabel.bundle_id == ResourceBundle.id)
    )
    roles = func.json_each(PageAssessment.result, "$.page_roles").table_valued("value")
    return (
        select(
            ResourceBundle.id,
            ResourceBundle.resource_id,
            ResourceBundle.page_version_id,
            ResourceBundle.body_ref,
            ResourceBundle.status,
            PageAssessment.result["business_type"].as_string().label("business_type"),
            PageAssessment.result["requires_review"].as_boolean().label("requires_review"),
        )
        .join(
            ResourceScan,
            (ResourceScan.bundle_id == ResourceBundle.id)
            & (ResourceScan.resource_id == ResourceBundle.resource_id),
        )
        .join(Resource, Resource.id == ResourceBundle.resource_id)
        .join(
            ResourceVersion,
            (ResourceVersion.id == ResourceBundle.page_version_id)
            & (ResourceVersion.resource_id == Resource.id),
        )
        .join(
            PageAssessment, PageAssessment.id == latest.correlate(ResourceBundle).scalar_subquery()
        )
        .where(
            Resource.source_id == source_id,
            ResourceScan.state.in_(["done", "review"]),
            PageAssessment.result["business_type"]
            .as_string()
            .in_(["recruitment", "correction", "cancellation"]),
            func.json_type(PageAssessment.result, "$.page_roles") == "array",
            exists(select(roles.c.value).where(roles.c.value.in_(["article", "attachment"]))),
            ~hidden | exposed,
        )
        .order_by(ResourceBundle.id)
    )


def _page(items, *, cursor, total):
    selected = []
    for item in items:
        following = cursor + len(selected) + 1
        trial = {
            "items": [*selected, item],
            "next_cursor": following if following < total else None,
        }
        if len(json.dumps(trial, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESPONSE:
            if not selected:
                raise ApiError(413, "RUNTIME_QUERY_LIMIT", "Saved item exceeds query limit")
            break
        selected.append(item)
    next_cursor = cursor + len(selected)
    return {"items": selected, "next_cursor": next_cursor if next_cursor < total else None}


def _chunks(value, section, data, *, version):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(text.encode()) > MAX_SAVED:
        raise ApiError(413, "RUNTIME_QUERY_LIMIT", "Saved result exceeds query limit")
    total = (len(text) + CHUNK_CHARS - 1) // CHUNK_CHARS
    cursor = data["cursor"]
    return _page(
        (
            {
                "section": section,
                "notice_id": data["notice_id"],
                "version": version,
                "chunk_index": index,
                "text": text[index * CHUNK_CHARS : (index + 1) * CHUNK_CHARS],
                "untrusted": True,
            }
            for index in range(cursor, min(total, cursor + data["limit"]))
        ),
        cursor=cursor,
        total=total,
    )


def _material(root, row, source_id):
    reference = row.body_ref
    if not re.fullmatch(r"[0-9a-f]{64}", reference):
        raise _unavailable()
    # No BlobStore constructor: it mkdirs even for read-only requests. Bounded
    # regular-file reads with no symlinks/FIFOs or pre-read unbounded allocation.
    fd = os.open(root / reference, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise _unavailable()
        if os.fstat(stream.fileno()).st_size > MAX_SAVED:
            raise ApiError(413, "RUNTIME_QUERY_LIMIT", "Saved material exceeds query limit")
        body = stream.read(MAX_SAVED + 1)
    if len(body) > MAX_SAVED or hashlib.sha256(body).hexdigest() != reference:
        raise _unavailable()
    material = json.loads(body)
    if (
        not isinstance(material, dict)
        or material.get("id") != row.id
        or material.get("source_id") != source_id
        or material.get("resource_id") != row.resource_id
        or not isinstance(material.get("page"), dict)
        or material["page"].get("version_id") != row.page_version_id
    ):
        raise _unavailable()
    return {
        key: material[key]
        for key in ("page", "attachments", "status", "errors", "captured_at")
        if key in material
    }


def _diagnostic(value):
    """Never expose reviewed semantic answers/refs embedded in saved diagnostics."""
    if isinstance(value, dict):
        if value.get("code") == "SEMANTIC_EXPECTATION" or value.get("stage") == "semantic":
            return {"code": "SEMANTIC_EXPECTATION", "message": "Independent check needs review"}
        return {
            key: _diagnostic(item)
            for key, item in value.items()
            if key
            not in {
                "gold",
                "expected",
                "expected_ref",
                "expectation_ref",
                "expected_basis",
                "semantic",
                "semantic_check",
                "semantic_checks",
                "reviewer",
                "labels",
            }
        }
    if isinstance(value, list):
        return [_diagnostic(item) for item in value]
    return value


def query_saved(session, root, data, classifier_key=None):
    """Authorization occurs in the same source read transaction before this call."""
    query = _current_notices(data["source_id"], classifier_key)
    if data["operation"] == "list_notices":
        # SQL pagination, not an unbounded Python load/filter/slice of all bundles.
        rows = session.execute(query.offset(data["cursor"]).limit(data["limit"] + 1)).all()
        items = [
            {
                "notice_id": row.id,
                "resource_id": row.resource_id,
                "bundle_version": row.body_ref,
                "page_version_id": row.page_version_id,
                "material_status": row.status,
                "business_type": row.business_type,
                "requires_review": row.requires_review,
                "untrusted": True,
            }
            for row in rows[: data["limit"]]
        ]
        return _page(items, cursor=data["cursor"], total=data["cursor"] + len(rows))
    row = session.execute(query.where(ResourceBundle.id == data["notice_id"])).first()
    if row is None:
        raise _denied()
    if data["operation"] == "read_notice":
        return _chunks(
            {
                "notice_id": row.id,
                "bundle_version": row.body_ref,
                "saved_material": _material(root, row, data["source_id"]),
            },
            "notice_material",
            data,
            version=row.body_ref,
        )
    audit_query = (
        select(
            AuditResult.id,
            func.length(AuditResult.result.cast(LargeBinary)),
            func.length(AuditResult.errors.cast(LargeBinary)),
        )
        .join(SourceRecipe, SourceRecipe.id == AuditResult.recipe_id)
        .where(AuditResult.bundle_id == row.id, SourceRecipe.source_id == data["source_id"])
        .order_by(AuditResult.created_at.desc(), AuditResult.id.desc())
        .limit(1)
    )
    saved = session.execute(audit_query).first()
    if saved is not None and sum(saved[1:]) > MAX_SAVED:
        raise ApiError(413, "RUNTIME_QUERY_LIMIT", "Saved result exceeds query limit")
    audit = session.get(AuditResult, saved.id) if saved else None
    result = (
        None
        if audit is None
        else {
            key: audit.result[key]
            for key in (
                "schema_version",
                "status",
                "positions",
                "validity",
                "application_period",
                "current_hiring_status",
                "quality_tier",
                "execution",
                "coverage",
                "routing",
                "errors",
                "diagnostics",
                "material_status",
                "material_warnings",
            )
            if key in audit.result
        }
    )
    return _chunks(
        {
            "notice_id": row.id,
            "bundle_version": row.body_ref,
            "audit_id": audit.id if audit else None,
            "audit_status": audit.status if audit else "not_available",
            "errors": _diagnostic(audit.errors) if audit else [],
            "recipe_id": audit.recipe_id if audit else None,
            "generation": audit.generation if audit else None,
            "evaluated_on": audit.evaluated_on if audit else None,
            "result": _diagnostic(result),
            "note": "Saved diagnostic result, not acceptance or semantic gold",
        },
        "parse_result",
        data,
        version=audit.id if audit else row.body_ref,
    )


def register_runtime_queries(app):
    bp = Blueprint("recipe_runtime_queries", __name__)

    @bp.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.post("/internal/runtime/recipe-queries")
    def query():
        token = app.config.get("RECIPE_RUNTIME_QUERY_TOKEN", "")
        caller = app.config.get("RECIPE_RUNTIME_QUERY_CALLER", "recipe-service")
        if app.config.get("RECIPE_RUNTIME_QUERIES_ENABLED") is not True or not token:
            raise ApiError(503, "RUNTIME_QUERY_DISABLED", "Runtime queries are disabled")
        if (
            not isinstance(token, str)
            or not isinstance(caller, str)
            or not caller
            or not token.isascii()
            or any(ord(c) <= 32 or ord(c) > 126 for c in token)
        ):
            raise _unavailable()
        if (
            not hmac.compare_digest(
                request.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()
            )
            or request.headers.get("X-Recipe-Caller") != caller
        ):
            raise _denied()
        if not request.is_json or request.content_length is None or request.content_length > 8192:
            raise _invalid()
        request.max_content_length = 8192
        data = request.get_json(silent=True)
        keys = {
            "operation",
            "source_id",
            "workspace_id",
            "task_id",
            "input_revision",
            "cursor",
            "limit",
        }
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("operation"), str)
            or data["operation"] not in {"list_notices", "read_notice", "read_parse_result"}
        ):
            raise _invalid()
        if data["operation"] != "list_notices":
            keys.add("notice_id")
        if set(data) != keys or any(
            not isinstance(data[k], str)
            or not 1 <= len(data[k]) <= 128
            or data[k] != data[k].strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in data[k])
            for k in keys - {"cursor", "limit"}
        ):
            raise _invalid()
        if (
            type(data["cursor"]) is not int
            or not 0 <= data["cursor"] <= 2**31 - 1
            or type(data["limit"]) is not int
            or not 1 <= data["limit"] <= 50
        ):
            raise _invalid()
        engine = None
        try:
            root = Path(app.config["SOURCE_STORAGE"]).resolve(strict=True)
            if not root.is_dir():
                raise _unavailable()
            kind = _task_authorized(
                _configured_authorizer(app.config),
                data,
                owner=app.config.get("RECIPE_RUNTIME_QUERY_OWNER", "source-server"),
            )
            with _readonly(app.config["DATABASE"]) as connection:
                engine = create_engine("sqlite+pysqlite://", creator=lambda: connection)
                with Session(engine, autoflush=False) as session:
                    classifier_key = _authorized(session, data, kind)
                    result = query_saved(session, root, data, classifier_key)
                # Flask's default ASCII escaping can double Chinese payload
                # bytes after pagination has already enforced the wire budget.
                response = Response(
                    json.dumps(result, ensure_ascii=False, allow_nan=False),
                    mimetype="application/json",
                )
                response.headers["Cache-Control"] = "no-store"
                return response
        except ApiError:
            raise
        except (
            OSError,
            httpx.InvalidURL,
            sqlite3.Error,
            SQLAlchemyError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
        ):
            raise _unavailable() from None
        finally:
            if engine is not None:
                engine.dispose()

    app.register_blueprint(bp)
