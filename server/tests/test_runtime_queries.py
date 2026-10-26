"""Actual SQLite scope/read-only checks plus Flask/httpx doubles; no network/model."""

import asyncio
import hashlib
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from flask import Flask
from sqlalchemy.orm import Session

from youju import create_app
from youju.database import create_sqlite_engine, initialize_database
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
from youju.models.base import utc_now
from youju.models.onboarding import SemanticCaseExposure, SemanticCaseLabel
from youju.onboarding.recipe_client import RecipeRPCClient
from youju.onboarding.store import digest, json_bytes
from youju.recipe_service import RecipeService
from youju.recipe_service.models import Job, Workspace
from youju.recipe_service.rpc import register_recipe_rpc
from youju.recipe_service.runtime_extension import HttpRecipeRuntimeExtension, RuntimeBinding
from youju.runtime_queries import MAX_RESPONSE, MAX_SAVED, RPCTaskStatusAuthorizer, _readonly

ROUTE = "/internal/runtime/recipe-queries"
TOKEN = "synthetic-query-token"
RPC_TOKEN = "synthetic-control-token"
GOLD = "DO-NOT-EXPOSE-REVIEWED-ANSWER"
HEADERS = {"Authorization": "Bearer " + TOKEN, "X-Recipe-Caller": "recipe-service"}


@pytest.fixture
def saved(tmp_path):
    storage = tmp_path / "blobs"
    storage.mkdir()
    service = RecipeService(tmp_path / "sidecar-owned-blobs")
    ws = service.create_workspace("source", owner_id="source-server")["workspace_id"]
    workspace = service.workspace(ws)
    payload = json_bytes({"source_id": "source", "objective": "Read-only offline fixture"})
    (workspace.input / "payload.json").write_bytes(payload)
    manifest = {"payload.json": hashlib.sha256(payload).hexdigest()}
    (workspace.input / "manifest.json").write_bytes(json_bytes(manifest))
    receipt = service.input_completed(
        ws, revision="r1", request_id="input", manifest_digest=digest(manifest)
    )
    job = service.start_build(ws, input_revision="r1", request_id="build", prompt="Offline fixture")
    with Session(service.engine) as session, session.begin():
        row = session.get(Job, job["id"])
        row.status, row.owner = "running", "executor"
    path = tmp_path / "source.sqlite3"
    engine = create_sqlite_engine(path)
    initialize_database(engine)
    pending = {
        "id": job["id"],
        "init_task_id": receipt["init_task_id"],
        "workspace_id": ws,
        "input_revision_id": receipt["input_revision_id"],
        "request_id": "build",
        "status": "running",
        "context": {"source_id": "source", "classifier_key": "clf"},
    }
    now = utc_now()
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                Source(
                    id=key,
                    name=key,
                    base_url=f"https://{key}.test",
                    status=RegistryStatus.ACTIVE,
                    verified_at=now,
                    authority_evidence="Public official source",
                )
                for key in ("source", "foreign")
            ]
        )
        session.flush()
        session.add(
            SourcePipeline(
                source_id="source",
                stage="authoring",
                state="running",
                lease_token="build",
                summary={"authoring_job": pending},
            )
        )
        session.add(
            SourceRecipe(
                id="recipe", source_id="source", digest="a" * 64, body_ref="a" * 64, status="active"
            )
        )
        session.add(
            SourceRecipe(
                id="foreign-recipe",
                source_id="foreign",
                digest="b" * 64,
                body_ref="b" * 64,
                status="active",
            )
        )
        session.flush()
        session.add(
            SourceRecipeBinding(source_id="source", workspace_id=ws, active_recipe_id="recipe")
        )
        for key, source_id, roles, business in [
            ("n1", "source", ["article"], "recruitment"),
            ("n2", "source", ["article"], "recruitment"),
            ("n3", "source", ["attachment"], "recruitment"),
            ("change", "source", ["article"], "correction"),
            ("cancel", "source", ["article"], "cancellation"),
            ("list-only", "source", ["notice_list"], "recruitment"),
            ("news", "source", ["article"], "news"),
            ("private", "foreign", ["article"], "recruitment"),
            ("holdout", "source", ["article"], "recruitment"),
        ]:
            resource = Resource(
                id="r-" + key, source_id=source_id, original_url=f"https://{source_id}.test/{key}"
            )
            session.add(resource)
            session.flush()
            text = "原始公告。" * (5000 if key == "n1" else 2)
            body = json_bytes(
                {
                    "id": key,
                    "source_id": source_id,
                    "resource_id": resource.id,
                    "page": {"version_id": "v-" + key, "text": text},
                    "attachments": [],
                    "status": "partial",
                    "errors": [{"code": "MISSING_ATTACHMENT"}],
                    "captured_at": now.isoformat(),
                    "gold": GOLD,
                }
            )
            ref = hashlib.sha256(body).hexdigest()
            (storage / ref).write_bytes(body)
            session.add(
                ResourceVersion(
                    id="v-" + key,
                    resource_id=resource.id,
                    content_sha256=ref,
                    body_ref=ref,
                    byte_length=len(body),
                )
            )
            session.flush()
            session.add(
                ResourceBundle(
                    id=key,
                    resource_id=resource.id,
                    page_version_id="v-" + key,
                    digest=ref,
                    body_ref=ref,
                    status="partial",
                    dependencies=[],
                )
            )
            session.flush()
            session.add(ResourceScan(resource_id=resource.id, state="done", bundle_id=key))
            session.add(
                PageAssessment(
                    id="a-" + key,
                    bundle_id=key,
                    classifier_key="clf",
                    result={"business_type": business, "page_roles": roles, "expected": GOLD},
                )
            )
        session.flush()
        session.add(
            SemanticCaseLabel(
                id="label",
                source_id="source",
                bundle_id="holdout",
                bundle_digest="0" * 64,
                expected_ref="0" * 64,
                split="holdout",
                reviewer=GOLD,
            )
        )
        old = ResourceBundle(
            id="old",
            resource_id="r-n1",
            page_version_id="v-n1",
            digest="1" * 64,
            body_ref="1" * 64,
            status="complete",
            dependencies=[],
        )
        session.add(old)
        session.flush()
        session.add(
            PageAssessment(
                bundle_id="old",
                classifier_key="clf",
                result={"business_type": "recruitment", "page_roles": ["article"]},
            )
        )
        session.add(
            AuditResult(
                id="audit-old",
                bundle_id="n1",
                recipe_id="recipe",
                evaluated_on="2026-10-23",
                status="success",
                result={"positions": [{"title": "old"}]},
                errors=[],
                created_at=now - timedelta(days=1),
            )
        )
        session.add(
            AuditResult(
                id="audit-current",
                bundle_id="n1",
                recipe_id="recipe",
                evaluated_on="2026-10-24",
                generation=1,
                status="failed",
                result={
                    "status": "success",
                    "positions": [{"title": "unaccepted saved candidate"}],
                    "diagnostics": [{"message": "saved failure", "expected": GOLD}],
                    "expected": GOLD,
                    "gold": GOLD,
                },
                errors=[
                    {"code": "SCRIPT_FAILED", "message": "Synthetic failure"},
                    {
                        "code": "SEMANTIC_EXPECTATION",
                        "expected": GOLD,
                        "actual": "x",
                        "expectation_ref": GOLD,
                    },
                ],
                created_at=now,
            )
        )
        session.add(
            AuditResult(
                id="audit-wrong-source",
                bundle_id="n1",
                recipe_id="foreign-recipe",
                evaluated_on="2026-10-24",
                generation=2,
                status="success",
                result={"positions": [{"title": "FOREIGN-RESULT"}]},
                errors=[],
                created_at=now + timedelta(seconds=1),
            )
        )
    # The service owns its separate storage; the source only sees authenticated
    # public control RPC. This is a real Flask RPC handler over an offline wire.
    rpc_app = Flask("offline-recipe-control")
    rpc_app.config.update(
        TESTING=True,
        RECIPE_RPC_ENABLED=True,
        RECIPE_SERVICE_STORAGE=str(tmp_path / "sidecar-owned-blobs"),
        RECIPE_RPC_CALLERS={
            "source-server": {
                "token": RPC_TOKEN,
                "source_ids": ["source", "foreign"],
                "agent_profile": {},
            }
        },
    )
    register_recipe_rpc(rpc_app)
    rpc_requests = []

    def rpc_http(request):
        rpc_requests.append(request)
        with rpc_app.test_client() as client:
            response = client.post(
                request.url.path, data=request.content, headers=dict(request.headers)
            )
        return httpx.Response(response.status_code, content=response.data)

    authorizer = RPCTaskStatusAuthorizer(
        RecipeRPCClient("http://127.0.0.1:8090", RPC_TOKEN, transport=httpx.MockTransport(rpc_http))
    )
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(path),
            "SOURCE_STORAGE": str(storage),
            "RECIPE_RUNTIME_QUERIES_ENABLED": True,
            "RECIPE_RUNTIME_QUERY_TOKEN": TOKEN,
            "RECIPE_RUNTIME_QUERY_AUTHORIZER": authorizer,
        }
    )
    data = {
        "operation": "list_notices",
        "source_id": "source",
        "workspace_id": ws,
        "task_id": job["id"],
        "input_revision": receipt["input_revision_id"],
        "cursor": 0,
        "limit": 20,
    }
    result = SimpleNamespace(
        app=app,
        client=app.test_client(),
        engine=engine,
        service=service,
        storage=storage,
        path=path,
        data=data,
        pending=pending,
        ws=ws,
        init_id=receipt["init_task_id"],
        authorizer=authorizer,
        rpc_app=rpc_app,
        rpc_http=rpc_http,
        rpc_requests=rpc_requests,
    )
    try:
        yield result
    finally:
        engine.dispose()
        service.close()


def post(saved, **changes):
    return saved.client.post(ROUTE, json={**saved.data, **changes}, headers=HEADERS)


def read_all(saved, operation, notice_id="n1", limit=2):
    cursor, chunks, versions = 0, [], set()
    while cursor is not None:
        response = post(saved, operation=operation, notice_id=notice_id, cursor=cursor, limit=limit)
        assert response.status_code == 200, response.json
        assert len(response.data) <= MAX_RESPONSE
        assert response.headers["Cache-Control"] == "no-store"
        page = response.json
        assert len(page["items"]) <= limit
        for item in page["items"]:
            assert item["chunk_index"] == len(chunks)
            assert item["untrusted"] is True and item["notice_id"] == notice_id
            versions.add(item["version"])
            chunks.append(item["text"])
        next_cursor = page["next_cursor"]
        assert next_cursor is None or next_cursor > cursor
        cursor = next_cursor
    assert len(versions) == 1
    return json.loads("".join(chunks))


def test_actual_sql_lists_only_current_visible_source_notices_and_pages(saved):
    ids, cursor = [], 0
    while cursor is not None:
        response = post(saved, cursor=cursor, limit=2)
        assert response.status_code == 200, response.json
        ids.extend(item["notice_id"] for item in response.json["items"])
        cursor = response.json["next_cursor"]
    assert ids == ["cancel", "change", "n1", "n2", "n3"]
    assert GOLD not in response.text
    assert post(saved, cursor=2**31 - 1).json == {"items": [], "next_cursor": None}


def test_material_is_bounded_chunked_original_with_versions_and_warnings(saved):
    value = read_all(saved, "read_notice", limit=50)
    assert value["notice_id"] == "n1"
    assert value["saved_material"]["page"]["text"] == "原始公告。" * 5000
    assert value["saved_material"]["status"] == "partial"
    assert value["saved_material"]["errors"] == [{"code": "MISSING_ATTACHMENT"}]
    assert GOLD not in json.dumps(value)


def test_saved_failed_audit_not_promoted_to_accepted_facts_or_gold(saved):
    value = read_all(saved, "read_parse_result")
    assert value["audit_id"] == "audit-current"
    assert value["audit_status"] == "failed"
    assert value["result"]["status"] == "success"  # Preserve disagreement, not authority.
    assert value["result"]["positions"] == [{"title": "unaccepted saved candidate"}]
    assert value["errors"][0]["code"] == "SCRIPT_FAILED"
    assert value["errors"][1] == {
        "code": "SEMANTIC_EXPECTATION",
        "message": "Independent check needs review",
    }
    assert value["generation"] == 1 and value["recipe_id"] == "recipe"
    assert GOLD not in json.dumps(value) and "FOREIGN-RESULT" not in json.dumps(value)
    absent = read_all(saved, "read_parse_result", "n2")
    assert absent["audit_status"] == "not_available" and absent["result"] is None


@pytest.mark.parametrize("notice_id", ["private", "old", "holdout", "news", "list-only", "unknown"])
@pytest.mark.parametrize("operation", ["read_notice", "read_parse_result"])
def test_foreign_stale_hidden_nonnotice_and_unknown_ids_are_indistinguishably_denied(
    saved, notice_id, operation
):
    response = post(saved, operation=operation, notice_id=notice_id)
    assert response.status_code == 403
    assert response.json["error"]["code"] == "RUNTIME_QUERY_DENIED"


@pytest.mark.parametrize("field", ["source_id", "workspace_id", "task_id", "input_revision"])
def test_each_full_tuple_dimension_is_authoritative(saved, field):
    assert post(saved, **{field: "stale-or-foreign"}).status_code == 403


@pytest.mark.parametrize(
    "status",
    [
        "candidate_ready",
        "succeeded",
        "blocked",
        "failed",
        "cancelled",
        "interrupted",
        "queued",
        "waiting_init",
    ],
)
def test_finalized_and_unassigned_sidecar_tasks_denied_even_with_stale_source_running(
    saved, status
):
    with Session(saved.service.engine) as session, session.begin():
        session.get(Job, saved.data["task_id"]).status = status
    assert post(saved).status_code == 403


@pytest.mark.parametrize(
    "case",
    [
        "cancel-requested",
        "no-owner",
        "wrong-owner",
        "workspace-source",
        "revision-workspace",
        "session-revision",
        "input-digest",
    ],
)
def test_current_assignment_owner_revision_and_cancellation_checked_through_control_rpc(
    saved, case
):
    from youju.recipe_service.models import InputRevision

    other = None
    if case == "revision-workspace":
        other = saved.service.create_workspace("foreign", owner_id="source-server")["workspace_id"]
    with Session(saved.service.engine) as session, session.begin():
        job = session.get(Job, saved.data["task_id"])
        workspace = session.get(Workspace, saved.ws)
        if case == "cancel-requested":
            job.cancel_requested = True
        elif case == "no-owner":
            job.owner = None
        elif case == "wrong-owner":
            workspace.owner_id = "unrelated-caller"
        elif case == "workspace-source":
            workspace.source_id = "foreign"
        elif case == "session-revision":
            workspace.session_revision += 1
        elif case == "input-digest":
            job.input_digest = "f" * 64
        else:
            session.get(InputRevision, saved.data["input_revision"]).workspace_id = other
    assert post(saved).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        "state",
        "stage",
        "pending-status",
        "accepted",
        "lease",
        "revision",
        "pending-task",
        "pending-source",
        "binding",
        "source-status",
    ],
)
def test_stale_source_association_or_finalized_authoring_intent_denied(saved, change):
    with Session(saved.engine) as session, session.begin():
        row = session.get(SourcePipeline, "source")
        pending = {**saved.pending, "context": dict(saved.pending["context"])}
        if change == "state":
            row.state = "ready"
        elif change == "stage":
            row.stage = "audit"
        elif change == "pending-status":
            pending["status"] = "candidate_ready"
        elif change == "accepted":
            pending["accepted"] = True
        elif change == "lease":
            row.lease_token = "new-request"
        elif change == "revision":
            pending["input_revision_id"] = "new-revision"
        elif change == "pending-task":
            pending["id"] = "new-task"
        elif change == "pending-source":
            pending["context"]["source_id"] = "foreign"
        elif change == "binding":
            session.get(SourceRecipeBinding, "source").workspace_id = "other"
        else:
            session.get(Source, "source").status = RegistryStatus.PAUSED
        row.summary = {"authoring_job": pending}
    assert post(saved).status_code == 403


@pytest.mark.parametrize(
    "status",
    [
        "preparing",
        "submitted",
        "queued",
        "auditing",
        "accepting",
        "rejecting",
        "succeeded",
        "failed",
        "superseded",
    ],
)
@pytest.mark.parametrize("bound_task", [True, False])
def test_actual_repair_statuses_require_explicit_task_and_full_binding(saved, status, bound_task):
    with Session(saved.service.engine) as session, session.begin():
        session.get(Job, saved.data["task_id"]).kind = "repair"
    with Session(saved.engine) as session, session.begin():
        pipeline = session.get(SourcePipeline, "source")
        pipeline.stage, pipeline.state, pipeline.summary = "active", "ready", {}
        context = {
            "workspace_id": saved.ws,
            "input_revision_id": saved.data["input_revision"],
            "baseline_recipe_id": "recipe",
        }
        if bound_task:
            context["task_id"] = saved.data["task_id"]
        session.add(
            RepairRequest(
                source_id="source",
                base_recipe_id="recipe",
                base_recipe_digest="a" * 64,
                status=status,
                classifier_key="clf",
                toolkit_key="toolkit",
                context=context,
            )
        )
    response = post(saved)
    assert response.status_code == (
        200 if status in {"preparing", "submitted"} and bound_task else 403
    )


def test_registered_running_init_task_can_read_but_old_init_cannot(saved):
    with Session(saved.service.engine) as session, session.begin():
        row = session.get(Job, saved.init_id)
        row.status, row.owner = "running", "executor"
    assert post(saved, task_id=saved.init_id).status_code == 200
    with Session(saved.service.engine) as session, session.begin():
        session.get(Workspace, saved.ws).init_task_id = "replacement-init"
    assert post(saved, task_id=saved.init_id).status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong", "X-Recipe-Caller": "recipe-service"},
        {"Authorization": "Bearer " + TOKEN, "X-Recipe-Caller": "foreign"},
        {"Authorization": "Bearer 非ascii", "X-Recipe-Caller": "recipe-service"},
    ],
)
def test_authentication_before_sql_and_no_credential_errors(saved, headers, monkeypatch):
    monkeypatch.setattr(
        "youju.runtime_queries._readonly", lambda _: pytest.fail("Unauthorized DB read")
    )
    response = saved.client.post(ROUTE, json=saved.data, headers=headers)
    assert response.status_code == 403
    assert TOKEN not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": []},
        {"operation": "write"},
        {"caller_id": "foreign"},
        {"url": "http://127.0.0.1"},
        {"cursor": True},
        {"cursor": -1},
        {"cursor": 2**31},
        {"limit": 0},
        {"limit": 51},
        {"limit": False},
        {"notice_id": "not-for-list"},
        {"task_id": "bad\n"},
        {"source_id": " "},
        {"input_revision": "x" * 129},
    ],
)
def test_strict_request_validation_before_storage_reads(saved, changes, monkeypatch):
    monkeypatch.setattr(
        "youju.runtime_queries._readonly", lambda _: pytest.fail("Invalid query DB read")
    )
    assert post(saved, **changes).status_code == 400


@pytest.mark.parametrize(
    "body", [b"null", b"[]", b"{bad", b" " * 8193], ids=["null", "array", "invalid", "oversized"]
)
def test_bad_json_and_request_size_safely_rejected(saved, body):
    response = saved.client.post(ROUTE, data=body, content_type="application/json", headers=HEADERS)
    assert response.status_code == 400


@pytest.mark.parametrize("missing", ["source-db", "authorizer", "root"])
def test_misconfigured_reads_do_not_create_files_or_directories(saved, tmp_path, missing):
    path = tmp_path / "does-not-exist"
    if missing == "source-db":
        saved.app.config["DATABASE"] = str(path)
    elif missing == "root":
        saved.app.config["SOURCE_STORAGE"] = str(path)
    else:
        saved.app.config["RECIPE_RUNTIME_QUERY_AUTHORIZER"] = None
    before = {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")}
    assert post(saved).status_code == 503
    assert {str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")} == before
    assert not path.exists()


def test_source_connections_are_read_only_and_never_open_sidecar_storage(saved, monkeypatch):
    def dump(path):
        with sqlite3.connect(path) as db:
            return "\n".join(db.iterdump())

    before = (dump(saved.path), set(saved.storage.rglob("*")))
    with _readonly(saved.path) as db:
        assert db.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("CREATE TABLE forbidden (id TEXT)")
    from youju import runtime_queries

    def source_only(path):
        assert str(path) == str(saved.path), "Source must not open a service DB"
        return _readonly(path)

    monkeypatch.setattr(runtime_queries, "_readonly", source_only)
    assert not (saved.storage / "authoring").exists()
    assert post(saved).status_code == 200
    read_all(saved, "read_notice")
    read_all(saved, "read_parse_result")
    assert (dump(saved.path), set(saved.storage.rglob("*"))) == before
    assert not (saved.storage / "authoring").exists()


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "symlink",
        "oversize",
        "corrupt",
        "foreign-body",
        "body-id",
        "body-resource",
        "body-version",
    ],
)
def test_blob_reads_bounded_verified_and_never_follow_paths(saved, tmp_path, case):
    with Session(saved.engine) as session, session.begin():
        row = session.get(ResourceBundle, "n1")
        file = saved.storage / row.body_ref
        if case == "missing":
            file.unlink()
        elif case == "symlink":
            target = tmp_path / "secret"
            target.write_text(TOKEN)
            file.unlink()
            file.symlink_to(target)
        elif case == "oversize":
            with file.open("wb") as stream:
                stream.truncate(MAX_SAVED + 1)
        elif case == "corrupt":
            file.write_text(TOKEN)
        else:
            value = json.loads(file.read_bytes())
            if case == "foreign-body":
                value["source_id"] = "foreign"
            elif case == "body-id":
                value["id"] = "n2"
            elif case == "body-resource":
                value["resource_id"] = "r-private"
            else:
                value["page"]["version_id"] = "v-private"
            raw = json_bytes(value)
            row.body_ref = hashlib.sha256(raw).hexdigest()
            (saved.storage / row.body_ref).write_bytes(raw)
    response = post(saved, operation="read_notice", notice_id="n1")
    assert response.status_code == (413 if case == "oversize" else 503)
    assert TOKEN not in response.text


def test_holdout_exposure_matches_existing_visible_case_policy(saved):
    with Session(saved.engine) as session, session.begin():
        session.add(SemanticCaseExposure(label_id="label", context_id="existing-reviewed-repair"))
    ids = [item["notice_id"] for item in post(saved).json["items"]]
    assert "holdout" in ids
    assert GOLD not in post(saved).text


def test_latest_classifier_and_scan_resource_consistency_enforced(saved):
    with Session(saved.engine) as session, session.begin():
        session.get(PageAssessment, "a-n2").result = {
            "business_type": "news",
            "page_roles": ["article"],
        }
        session.get(ResourceScan, "r-n3").bundle_id = "private"
        session.add(
            PageAssessment(
                bundle_id="n1",
                classifier_key="unrelated-classifier",
                result={"business_type": "news", "page_roles": ["article"]},
                created_at=utc_now() + timedelta(seconds=20),
            )
        )
    ids = [item["notice_id"] for item in post(saved).json["items"]]
    assert "n2" not in ids and "n3" not in ids and "private" not in ids
    assert "n1" in ids  # Bound classifier, not an unrelated latest assessment.


def test_httpx_client_to_real_flask_endpoint_wire_and_sanitized_denial(saved):
    requests = []

    async def transport(request):
        requests.append(request)
        response = await asyncio.to_thread(
            saved.client.open,
            request.url.path,
            method=request.method,
            data=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            response.status_code, content=response.data, headers=dict(response.headers)
        )

    binding = RuntimeBinding(
        saved.ws, "source", saved.data["task_id"], saved.data["input_revision"]
    )
    extension = HttpRecipeRuntimeExtension(
        binding,
        endpoint="https://runtime.test" + ROUTE,
        caller_id="recipe-service",
        token=TOKEN,
        transport=httpx.MockTransport(transport),
    )
    page = asyncio.run(extension.list_notices(limit=2))
    assert len(page["items"]) == 2 and page["next_cursor"] == 2
    assert asyncio.run(extension.read_notice("n1", limit=1))["next_cursor"] == 1
    assert asyncio.run(extension.read_parse_result("n1"))["items"]
    from youju.recipe_service.runtime_extension import RuntimeExtensionError

    with pytest.raises(RuntimeExtensionError) as caught:
        asyncio.run(extension.read_notice("private"))
    assert str(caught.value) == "RUNTIME_QUERY_UNAVAILABLE"
    assert len(requests) == 4


def test_production_optional_factory_binds_only_host_config():
    from youju.recipe_service.production import configured_runtime_extension_factory

    assert configured_runtime_extension_factory({}) is None
    assert configured_runtime_extension_factory({"RECIPE_RUNTIME_QUERY_TOKEN": TOKEN}) is None
    with pytest.raises(ValueError, match="requires a server token"):
        configured_runtime_extension_factory({"RECIPE_RUNTIME_QUERY_URL": "https://runtime.test"})
    factory = configured_runtime_extension_factory(
        {
            "RECIPE_RUNTIME_QUERY_URL": "https://runtime.test" + ROUTE,
            "RECIPE_RUNTIME_QUERY_TOKEN": TOKEN,
            "RECIPE_RUNTIME_QUERY_CALLER": "configured-worker",
        }
    )
    binding = RuntimeBinding("workspace", "source", "task", "revision")
    extension = factory(binding)
    assert extension.binding == binding
    assert extension.endpoint == "https://runtime.test" + ROUTE
    assert extension.caller_id == "configured-worker" and extension.token == TOKEN
    assert TOKEN not in repr(extension)


def test_production_worker_passes_extension_factory_into_native_runtime(tmp_path, monkeypatch):
    from youju.recipe_service import production

    service = RecipeService(tmp_path / "worker")
    calls, sentinel = [], object()

    class Worker:
        def __init__(self, _service, factory):
            calls.append(factory())

        async def run_once(self):
            raise asyncio.CancelledError

    class Dispatcher:
        def __init__(self, *args):
            pass

        async def run_once(self):
            raise asyncio.CancelledError

    monkeypatch.setattr(production, "TaskWorker", Worker)
    monkeypatch.setattr(production, "CallbackDispatcher", Dispatcher)
    monkeypatch.setattr(production, "XdogAgentRuntime", lambda models, **kw: (models, kw))
    try:
        asyncio.run(
            production.run_workers(service, "models", {}, runtime_extension_factory=sentinel)
        )
    finally:
        service.close()
    assert calls == [("models", {"runtime_extension_factory": sentinel})]


def test_production_main_uses_original_source_config_without_network_or_models(
    tmp_path, monkeypatch
):
    from youju.onboarding.sandbox import Sandbox
    from youju.recipe_service import production

    config = {
        "SOURCE_STORAGE": str(tmp_path / "production"),
        "RECIPE_RUNTIME_QUERY_URL": "https://runtime.test" + ROUTE,
        "RECIPE_RUNTIME_QUERY_TOKEN": TOKEN,
    }
    calls = []

    async def probe(self):
        return None

    async def workers(service, models, targets, *, runtime_extension_factory):
        binding = RuntimeBinding("workspace", "source", "task", "revision")
        extension = runtime_extension_factory(binding)
        calls.append((extension.binding, extension.token, targets))

    monkeypatch.setattr(production, "load_app", lambda: SimpleNamespace(config=config))
    monkeypatch.setattr(production, "configured_models", lambda _: "models")
    monkeypatch.setattr(Sandbox, "probe", probe)
    monkeypatch.setattr(
        production,
        "create_recipe_app",
        lambda _: SimpleNamespace(config={"RECIPE_CALLBACK_TARGETS": {"callback": "target"}}),
    )
    monkeypatch.setattr(
        production,
        "create_server",
        lambda *a, **kw: SimpleNamespace(run=lambda: None, close=lambda: None),
    )
    monkeypatch.setattr(production, "run_workers", workers)
    monkeypatch.setattr(production, "run_cancellable", asyncio.run)
    monkeypatch.setattr(production, "emit", lambda **kw: None)
    production.main()
    assert calls == [
        (RuntimeBinding("workspace", "source", "task", "revision"), TOKEN, {"callback": "target"})
    ]


@pytest.mark.parametrize(
    "field", ["source", "workspace", "task", "revision", "baseline", "base-source"]
)
def test_repair_context_cannot_authorize_a_different_tuple_or_replaced_baseline(saved, field):
    with Session(saved.service.engine) as session, session.begin():
        session.get(Job, saved.data["task_id"]).kind = "repair"
    with Session(saved.engine) as session, session.begin():
        pipeline = session.get(SourcePipeline, "source")
        pipeline.stage, pipeline.state, pipeline.summary = "active", "ready", {}
        context = {
            "workspace_id": saved.ws,
            "task_id": saved.data["task_id"],
            "input_revision_id": saved.data["input_revision"],
            "baseline_recipe_id": "recipe",
        }
        source_id, base_id = "source", "recipe"
        if field == "source":
            source_id = "foreign"
        elif field == "base-source":
            base_id = "foreign-recipe"
        elif field == "baseline":
            context["baseline_recipe_id"] = "replaced-baseline"
        else:
            context[
                {"workspace": "workspace_id", "task": "task_id", "revision": "input_revision_id"}[
                    field
                ]
            ] = "stale"
        session.add(
            RepairRequest(
                source_id=source_id,
                base_recipe_id=base_id,
                base_recipe_digest="a" * 64,
                status="submitted",
                classifier_key="clf",
                toolkit_key="toolkit",
                context=context,
            )
        )
    assert post(saved).status_code == 403


@pytest.mark.parametrize("which", ["result", "errors"])
def test_oversized_saved_diagnostics_rejected_before_loading_json(saved, monkeypatch, which):
    monkeypatch.setattr("youju.runtime_queries.MAX_SAVED", 1024)
    with Session(saved.engine) as session, session.begin():
        audit = session.get(AuditResult, "audit-current")
        if which == "result":
            audit.result = {"diagnostics": [{"message": "大" * 1024}]}
        else:
            audit.errors = [{"code": "FAILED", "message": "大" * 1024}]
    response = post(saved, operation="read_parse_result", notice_id="n1")
    assert response.status_code == 413
    assert response.json["error"]["code"] == "RUNTIME_QUERY_LIMIT"


def test_sql_listing_does_not_load_all_bundles_or_classification_bodies(saved, monkeypatch):
    from sqlalchemy import event

    from youju import runtime_queries

    statements = []
    original = runtime_queries.create_engine

    def inspected(*args, **kwargs):
        engine = original(*args, **kwargs)

        @event.listens_for(engine, "before_cursor_execute")
        def record(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        return engine

    monkeypatch.setattr(runtime_queries, "create_engine", inspected)
    assert post(saved, limit=1).status_code == 200
    queries = [sql for sql in statements if "resource_bundles.id" in sql]
    assert len(queries) == 1
    assert "LIMIT ? OFFSET ?" in queries[0]
    assert "resource_bundles.dependencies" not in queries[0]
    assert "SELECT page_assessments.result," not in queries[0]


@pytest.mark.parametrize("enabled", [False, None, "true"])
def test_endpoint_disabled_unless_explicitly_enabled(saved, enabled, monkeypatch):
    saved.app.config["RECIPE_RUNTIME_QUERIES_ENABLED"] = enabled
    monkeypatch.setattr(
        "youju.runtime_queries._readonly", lambda _: pytest.fail("Disabled DB read")
    )
    response = post(saved)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "RUNTIME_QUERY_DISABLED"
    assert response.headers["Cache-Control"] == "no-store"


def test_misconfigured_source_sql_schema_error_is_sanitized_without_creation(saved, tmp_path):
    missing_schema = tmp_path / "wrong-schema.sqlite3"
    with sqlite3.connect(missing_schema) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    saved.app.config["DATABASE"] = str(missing_schema)
    response = post(saved)
    assert response.status_code == 503
    assert "SELECT" not in response.text and str(tmp_path) not in response.text
    assert TOKEN not in response.text


@pytest.mark.parametrize("state", ["ready", "running"])
def test_noncurrent_scan_states_do_not_expose_last_saved_bundle(saved, state):
    with Session(saved.engine) as session, session.begin():
        session.get(ResourceScan, "r-n1").state = state
    assert post(saved, operation="read_notice", notice_id="n1").status_code == 403
    assert "n1" not in [item["notice_id"] for item in post(saved).json["items"]]


@pytest.mark.parametrize("roles", ["article", {"role": "article"}, None])
def test_malformed_saved_classification_does_not_promote_nonnotices(saved, roles):
    with Session(saved.engine) as session, session.begin():
        session.get(PageAssessment, "a-n1").result = {
            "business_type": "recruitment",
            "page_roles": roles,
        }
    assert post(saved, operation="read_parse_result", notice_id="n1").status_code == 403
    assert "n1" not in [item["notice_id"] for item in post(saved).json["items"]]


def test_listing_preserves_classification_review_uncertainty(saved):
    with Session(saved.engine) as session, session.begin():
        assessment = session.get(PageAssessment, "a-n1")
        assessment.result = {**assessment.result, "requires_review": True}
    item = next(item for item in post(saved).json["items"] if item["notice_id"] == "n1")
    assert item["requires_review"] is True
    absent = next(item for item in post(saved).json["items"] if item["notice_id"] == "n2")
    assert absent["requires_review"] is None


def test_control_rpc_authentication_scope_and_metadata_only_no_assignment_lock(saved):
    # Reverse calls happen while the worker holds this lease. Metadata queries
    # must not reacquire it (or construct a source-side RecipeService).
    with saved.service.assignment(saved.ws):
        assert post(saved).status_code == 200
    assert [request.url.path for request in saved.rpc_requests] == [
        "/internal/recipe/GetWorkspace",
        "/internal/recipe/GetTask",
    ]
    for request in saved.rpc_requests:
        assert request.headers["Authorization"] == "Bearer " + RPC_TOKEN
        assert request.headers["X-Recipe-Caller"] == "source-server"
        assert request.url.host == "127.0.0.1"
    assert json.loads(saved.rpc_requests[-1].content) == {
        "task_id": saved.data["task_id"],
        "after": 0,
        "limit": 1,
    }


@pytest.mark.parametrize("url", [None, "http://localhost:18090"])
def test_actual_default_adapter_uses_existing_control_rpc_configuration(saved, monkeypatch, url):
    from youju import runtime_queries

    constructed = []

    def rpc_client(endpoint, token, *, caller):
        constructed.append((endpoint, token, caller))
        return RecipeRPCClient(
            endpoint, token, caller=caller, transport=httpx.MockTransport(saved.rpc_http)
        )

    monkeypatch.setattr(runtime_queries, "RecipeRPCClient", rpc_client)
    saved.app.config.pop("RECIPE_RUNTIME_QUERY_AUTHORIZER")
    saved.app.config["RECIPE_RPC_TOKEN"] = RPC_TOKEN
    if url is not None:
        saved.app.config["RECIPE_RPC_URL"] = url
    assert post(saved).status_code == 200
    assert constructed == [(url or "http://127.0.0.1:8090", RPC_TOKEN, "source-server")]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"RECIPE_RPC_TOKEN": ""},
        {"RECIPE_RPC_TOKEN": "bad token"},
        {"RECIPE_RPC_TOKEN": RPC_TOKEN, "RECIPE_RPC_URL": "https://foreign.test"},
        {"RECIPE_RPC_TOKEN": RPC_TOKEN, "RECIPE_RPC_URL": None},
        {"RECIPE_RPC_TOKEN": RPC_TOKEN, "RECIPE_RPC_URL": "http://127.0.0.1:bad"},
        {"RECIPE_RPC_TOKEN": RPC_TOKEN, "RECIPE_RUNTIME_QUERY_OWNER": ""},
    ],
)
def test_unconfigured_or_invalid_control_adapter_never_falls_back_to_service_db(
    saved, config, monkeypatch
):
    saved.app.config.pop("RECIPE_RUNTIME_QUERY_AUTHORIZER")
    saved.app.config.update(config)
    monkeypatch.setattr(
        "youju.runtime_queries._readonly", lambda _: pytest.fail("Unauthorized source read")
    )
    response = post(saved)
    assert response.status_code == 503
    assert TOKEN not in response.text and RPC_TOKEN not in response.text
    assert not saved.rpc_requests


@pytest.mark.parametrize(
    "failure",
    ["connection", "authentication", "source-permission", "server", "malformed", "redirect"],
)
def test_control_rpc_unavailable_or_unauthorized_fails_closed_without_source_reads(
    saved, monkeypatch, failure
):
    def broken(request):
        if failure == "connection":
            raise httpx.ConnectError(TOKEN, request=request)
        if failure == "authentication":
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
        if failure == "source-permission":
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        if failure == "server":
            return httpx.Response(500, text=TOKEN)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://foreign.test"}, text=TOKEN)
        return httpx.Response(200, text=TOKEN)

    saved.app.config["RECIPE_RUNTIME_QUERY_AUTHORIZER"] = RPCTaskStatusAuthorizer(
        RecipeRPCClient("http://127.0.0.1:8090", RPC_TOKEN, transport=httpx.MockTransport(broken))
    )
    monkeypatch.setattr(
        "youju.runtime_queries._readonly", lambda _: pytest.fail("Unauthorized source read")
    )
    response = post(saved)
    assert response.status_code in {403, 503}
    assert TOKEN not in response.text and RPC_TOKEN not in response.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", False),
        ("active", 1),
        ("assigned", False),
        ("assigned", "true"),
        ("input_revision_valid", False),
        ("input_revision_valid", None),
        ("session_current", False),
        ("session_current", 1),
        ("source_id", "foreign"),
        ("owner_id", "recipe-service"),
        ("lifecycle", "legacy"),
    ],
)
def test_host_injected_authorizer_cannot_bypass_central_assignment_validation(saved, field, value):
    calls = []

    class HostAuthorizer:
        async def get_status(self, **kwargs):
            calls.append(kwargs)
            workspace, task = await saved.authorizer.get_status(**kwargs)
            task["assignment"][field] = value
            return workspace, task

    saved.app.config["RECIPE_RUNTIME_QUERY_AUTHORIZER"] = HostAuthorizer()
    assert post(saved).status_code == 403
    assert calls == [
        {"task_id": saved.data["task_id"], "workspace_id": saved.ws, "owner": "source-server"}
    ]


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("task", "id", "other"),
        ("task", "workspace_id", "other"),
        ("task", "input_revision_id", "other"),
        ("task", "kind", "other"),
        ("task", "status", "candidate_ready"),
        ("task", "cancel_requested", 0),
        ("task", "base_session_revision", True),
        ("task", "revision_id", "finalized-revision"),
        ("task", "result_ref", "finalized-result"),
        ("workspace", "workspace_id", "other"),
        ("workspace", "source_id", "foreign"),
        ("workspace", "owner_id", "foreign"),
        ("workspace", "lifecycle", "legacy"),
        ("workspace", "session_revision", True),
    ],
)
def test_injected_metadata_still_requires_full_current_task_workspace_tuple(
    saved, target, field, value
):
    class HostAuthorizer:
        async def get_status(self, **kwargs):
            workspace, task = await saved.authorizer.get_status(**kwargs)
            (task if target == "task" else workspace)[field] = value
            return workspace, task

    saved.app.config["RECIPE_RUNTIME_QUERY_AUTHORIZER"] = HostAuthorizer()
    assert post(saved).status_code == 403


@pytest.mark.parametrize(
    "missing",
    ["assignment", "active", "assigned", "input_revision_valid", "session_current", "owner_id"],
)
def test_old_or_partial_status_contract_fails_closed(saved, missing):
    class HostAuthorizer:
        async def get_status(self, **kwargs):
            workspace, task = await saved.authorizer.get_status(**kwargs)
            if missing == "assignment":
                del task[missing]
            else:
                del task["assignment"][missing]
            return workspace, task

    saved.app.config["RECIPE_RUNTIME_QUERY_AUTHORIZER"] = HostAuthorizer()
    assert post(saved).status_code == 403


def test_host_authorizer_failure_is_sanitized_and_cannot_enable_source_only_fallback(saved):
    class HostAuthorizer:
        async def get_status(self, **kwargs):
            raise RuntimeError(TOKEN + RPC_TOKEN + str(saved.path))

    saved.app.config["RECIPE_RUNTIME_QUERY_AUTHORIZER"] = HostAuthorizer()
    response = post(saved)
    assert response.status_code == 503
    assert TOKEN not in response.text and str(saved.path) not in response.text


def test_status_rechecked_each_page_after_cancellation_with_source_pending_still_running(saved):
    assert post(saved, limit=1).json["next_cursor"] == 1
    with Session(saved.service.engine) as session, session.begin():
        session.get(Job, saved.data["task_id"]).cancel_requested = True
    assert post(saved, cursor=1, limit=1).status_code == 403
    assert len(saved.rpc_requests) == 4


def test_explicit_owner_cannot_be_substituted_for_authenticated_rpc_caller(saved):
    saved.app.config["RECIPE_RUNTIME_QUERY_OWNER"] = "foreign"
    assert post(saved).status_code == 403
    assert not saved.rpc_requests
