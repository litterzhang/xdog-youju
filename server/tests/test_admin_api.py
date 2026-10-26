"""Focused first-version auth/registry flows; no live issuer, crawler or model."""

import fcntl
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju import create_app
from youju.api.auth import AuthClient
from youju.database import create_sqlite_engine, initialize_database
from youju.models import ResourceScan, SourcePipeline


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / "source.sqlite3"
    engine = create_sqlite_engine(path)
    initialize_database(engine)
    engine.dispose()
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(path),
            "SOURCE_STORAGE": str(tmp_path / "blobs"),
            "AUTH_ISSUER": "https://auth.example.org",
            "AUTH_CLIENT_ID": "synthetic-youju",
            "AUTH_CLIENT_SECRET": "synthetic-secret",
        }
    )
    calls = []

    def issuer(request):
        data = json.loads(request.content)
        calls.append((request.url.path, data))
        assert data["client_id"] == "synthetic-youju"
        assert data["client_secret"] == "synthetic-secret"
        if request.url.path == "/machine/userinfo":
            token = data["access_token"]
            if token == "expired":
                return httpx.Response(401, json={"error": "invalid"})
            # Unrelated provider roles do not grant this application's admin permission.
            return httpx.Response(
                200,
                json={
                    "sub": token,
                    "roles": ["admin"],
                    "permissions": ["youju:source:manage"] if token == "operator" else [],
                },
            )
        return httpx.Response(200, json={"access_token": "operator"})

    app.extensions["auth"] = AuthClient(app.config, transport=httpx.MockTransport(issuer))
    return app, app.test_client(), calls


def auth(token="operator"):
    return {"Authorization": f"Bearer {token}"}


def register_source(client):
    assert (
        client.post(
            "/api/admin/regions", json={"code": "340000", "name": "安徽省"}, headers=auth()
        ).status_code
        == 201
    )
    org = client.post(
        "/api/admin/organizations",
        json={
            "name": "合成大学",
            "organization_type": "university",
            "root_domain": "example.org",
            "location_region_code": "340000",
        },
        headers=auth(),
    )
    assert org.status_code == 201
    source = client.post(
        "/api/admin/sources",
        json={
            "name": "合成招聘来源",
            "organization_id": org.json["id"],
            "base_url": "https://jobs.example.org/",
            "region_codes": ["340000"],
            "seed_urls": ["https://jobs.example.org/notices"],
            "description": "仅合成测试",
        },
        headers=auth(),
    )
    assert source.status_code == 201
    return org.json, source.json


def test_auth_machine_contract_and_permission_enforcement(configured):
    _, client, calls = configured
    response = client.post("/api/auth/login", json={"username": "synthetic", "password": " x "})
    assert response.json == {"access_token": "operator"}
    assert calls[-1][0] == "/machine/password/login"
    assert calls[-1][1]["password"] == " x "
    assert client.post("/api/auth/wechat", json={"code": "synthetic-code"}).status_code == 200
    assert calls[-1][0] == "/machine/wechat/miniprogram"
    assert client.get("/api/auth/me", headers=auth()).json == {
        "id": "operator",
        "permissions": ["youju:source:manage"],
    }
    assert client.get("/api/auth/me", headers=auth("reader")).json["permissions"] == []
    assert client.get("/api/admin/sources").status_code == 401
    assert client.get("/api/admin/sources", headers=auth("reader")).status_code == 403
    assert (
        client.post(
            "/api/admin/organizations", headers=auth("reader"), json={"is_admin": True}
        ).status_code
        == 403
    )
    assert client.get("/api/admin/sources", headers=auth("expired")).status_code == 401
    assert client.get("/api/auth/me", headers=auth()).headers["Cache-Control"] == "no-store"


def test_register_approve_enqueue_and_safe_status_projection(configured):
    app, client, calls = configured
    _, source = register_source(client)
    path = f"/api/admin/sources/{source['id']}"
    assert source["status"] == "candidate"
    assert source["onboarding"]["stage"] == "not_started"
    assert client.post(path + "/onboard", json={}, headers=auth()).status_code == 409
    assert (
        client.post(path + "/approve", json={"authority_evidence": ""}, headers=auth()).status_code
        == 400
    )
    approved = client.post(
        path + "/approve",
        json={"authority_evidence": "合成官方出处与发布主体，测试用"},
        headers=auth(),
    )
    assert approved.status_code == 200 and approved.json["verified_at"]
    queued = client.post(path + "/onboard", json={}, headers=auth())
    assert queued.status_code == 202
    assert queued.json["onboarding"]["stage"] == "inventory"
    assert queued.json["onboarding"]["acquisition"] == {"ready": 2}
    assert client.post(path + "/onboard", json={}, headers=auth()).status_code == 202
    engine = create_sqlite_engine(app.config["DATABASE"])
    try:
        with Session(engine) as session, session.begin():
            assert session.scalar(select(func.count()).select_from(ResourceScan)) == 2
            pipeline = session.get(SourcePipeline, source["id"])
            pipeline.stage, pipeline.state = "audit", "blocked"
            pipeline.summary = {
                "audit": {
                    "processed": 2,
                    "statuses": {"failed": 1, "partial": 1},
                    "validity": {"unknown": 1, "expired": 1},
                    "results": [{"private": "omit-this"}],
                },
                "error": {"code": "AUDIT_NOT_READY", "message": "private-detail"},
            }
        status = client.get(path, headers=auth())
        assert status.json["onboarding"]["state"] == "blocked"
        assert status.json["onboarding"]["summary"]["audit"]["statuses"]["partial"] == 1
        assert b"omit-this" not in status.data and b"private-detail" not in status.data
        assert all(p.startswith("/machine/") for p, _ in calls)
    finally:
        engine.dispose()


def test_scope_validation_privilege_injection_and_busy_database(configured):
    app, client, _ = configured
    org, source = register_source(client)
    payload = {
        "name": "合成来源",
        "organization_id": org["id"],
        "base_url": "https://evil.example.net/",
        "region_codes": [],
        "seed_urls": [],
    }
    assert client.post("/api/admin/sources", headers=auth(), json=payload).status_code == 400
    for url in (
        "http://jobs.example.org/",
        "https://127.0.0.1/",
        "https://jobs.example.org:444/",
        "https://jobs.example.org.evil.net/",
    ):
        payload["base_url"] = url
        assert client.post("/api/admin/sources", headers=auth(), json=payload).status_code == 400
    payload["base_url"] = "https://jobs.example.org/new"
    payload["status"] = "active"
    assert client.post("/api/admin/sources", headers=auth(), json=payload).status_code == 400
    assert (
        client.post(
            f"/api/admin/sources/{source['id']}/onboard", headers=auth(), json={"storage": "/tmp"}
        ).status_code
        == 400
    )
    lock_path = Path(app.config["DATABASE"]).with_suffix(".sqlite3.source.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert (
            client.post(
                "/api/admin/regions", headers=auth(), json={"code": "new", "name": "新地区"}
            ).status_code
            == 409
        )
        assert client.get("/api/admin/sources", headers=auth()).status_code == 200
    assert client.get("/api/admin/sources?page=0", headers=auth()).status_code == 400


def test_fail_closed_auth_origin_and_factory_without_database(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "missing.sqlite3"),
            "AUTH_ISSUER": "",
            "AUTH_CLIENT_ID": "",
            "AUTH_CLIENT_SECRET": "",
            "ALLOWED_ORIGINS": ["http://127.0.0.1:10089"],
        }
    )
    client = app.test_client()
    assert client.get("/api/auth/status").json == {"provider": "zdog-auth", "configured": False}
    assert (
        client.post(
            "/api/auth/login", json={"username": "synthetic", "password": "synthetic"}
        ).status_code
        == 503
    )
    assert client.get("/api/health").status_code == 200
    assert not (tmp_path / "missing.sqlite3").exists()
    assert (
        client.post(
            "/api/auth/login", headers={"Origin": "https://evil.example.org"}, json={}
        ).status_code
        == 403
    )
    assert (
        client.options(
            "/api/admin/sources",
            headers={
                "Origin": "http://127.0.0.1:10089",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        ).status_code
        == 204
    )
    assert (
        client.options(
            "/api/admin/sources",
            headers={"Origin": "https://evil.example.org", "Access-Control-Request-Method": "GET"},
        ).status_code
        == 403
    )
    assert not AuthClient(
        {
            "AUTH_ISSUER": "http://auth.example.org",
            "AUTH_CLIENT_ID": "synthetic",
            "AUTH_CLIENT_SECRET": "synthetic",
        }
    ).configured


def test_foreground_worker_picks_http_enqueued_sources_without_inline_execution(
    configured, monkeypatch
):
    app, client, _ = configured
    _, source = register_source(client)
    path = f"/api/admin/sources/{source['id']}"
    client.post(path + "/approve", json={"authority_evidence": "合成官方依据"}, headers=auth())
    client.post(path + "/onboard", json={}, headers=auth())
    calls = []

    async def step(pipeline, source_id, *, limit):
        calls.append((source_id, limit))
        with Session(pipeline.store.engine) as session, session.begin():
            record = session.get(SourcePipeline, source_id)
            record.stage = "classification"
        return pipeline.store.status(source_id)

    monkeypatch.setattr("youju.onboarding.pipeline.Pipeline.step", step)
    result = app.test_cli_runner().invoke(
        args=[
            "source-worker",
            "--database",
            app.config["DATABASE"],
            "--storage",
            app.config["SOURCE_STORAGE"],
            "--limit",
            "3",
        ]
    )
    assert result.exit_code == 0, result.output
    assert calls == [(source["id"], 3)]
    assert client.get(path, headers=auth()).json["onboarding"]["stage"] == "classification"

    # A differently configured global worker must not reclassify an existing source.
    engine = create_sqlite_engine(app.config["DATABASE"])
    try:
        with Session(engine) as session, session.begin():
            session.get(SourcePipeline, source["id"]).summary = {
                "classification": {"classifier_key": "synthetic-other-profile"}
            }
    finally:
        engine.dispose()
    skipped = app.test_cli_runner().invoke(
        args=[
            "source-worker",
            "--database",
            app.config["DATABASE"],
            "--storage",
            app.config["SOURCE_STORAGE"],
        ]
    )
    assert skipped.exit_code == 0, skipped.output
    assert "CLASSIFIER_PROFILE_MISMATCH" in skipped.output
    assert calls == [(source["id"], 3)]


def test_central_grants_revocation_and_malformed_permissions(configured):
    app, client, _ = configured
    # Legacy configuration and provider role labels must never restore access.
    app.config["ADMIN_SUBJECTS"] = ["operator"]
    info = {"sub": "operator", "roles": ["admin", "youju-admin"], "is_admin": True}
    validations = []

    def issuer(request):
        validations.append(request.url.path)
        return httpx.Response(200, json=info)

    app.extensions["auth"] = AuthClient(app.config, transport=httpx.MockTransport(issuer))
    for raw in (
        None,
        "youju:source:manage",
        {"youju:source:manage": True},
        ["youju:source:manage", 1],
        ["youju:source:manage", ""],
        ["*", "source:manage", "trading:use"],
    ):
        info["permissions"] = raw
        assert client.get("/api/auth/me", headers=auth()).json["permissions"] == []
        assert client.get("/api/admin/sources", headers=auth()).status_code == 403
    info.pop("permissions")
    assert client.get("/api/admin/sources", headers=auth()).status_code == 403
    info["permissions"] = ["trading:use", "youju:source:manage", "youju:source:manage"]
    assert client.get("/api/auth/me", headers=auth()).json["permissions"] == ["youju:source:manage"]
    before = len(validations)
    assert client.get("/api/admin/sources", headers=auth()).status_code == 200
    assert len(validations) == before + 1
    # Revoking a central grant takes effect on the next request with the same token.
    info["permissions"] = []
    assert client.get("/api/admin/sources", headers=auth()).status_code == 403


def test_auth_provision_is_explicit_and_never_assigns_users(configured):
    app, _, calls = configured
    assert calls == []  # Factory has no auth network or provisioning side effects.
    result = app.test_cli_runner().invoke(args=["auth-provision"])
    assert result.exit_code == 0, result.output
    assert "No user grants" in result.output
    assert calls == [
        (
            "/machine/provision",
            {
                "client_id": "synthetic-youju",
                "client_secret": "synthetic-secret",
                "permissions": [
                    {
                        "name": "youju:source:manage",
                        "description": "Manage Youju sources and onboarding",
                    }
                ],
                "roles": [
                    {
                        "name": "youju-admin",
                        "description": "Youju source administrator",
                        "permissions": ["youju:source:manage"],
                    }
                ],
            },
        )
    ]
    assert "synthetic-secret" not in result.output


def test_provision_and_authorization_fail_closed_without_leaking_provider_errors(configured):
    app, client, _ = configured

    def unavailable(request):
        return httpx.Response(503, json={"error": "synthetic-secret must not leak"})

    app.extensions["auth"] = AuthClient(app.config, transport=httpx.MockTransport(unavailable))
    response = client.get("/api/admin/sources", headers=auth())
    assert response.status_code == 503
    assert b"synthetic-secret" not in response.data
    result = app.test_cli_runner().invoke(args=["auth-provision"])
    assert result.exit_code != 0
    assert "AUTH_UNAVAILABLE" in result.output
    assert "synthetic-secret" not in result.output


def test_identity_projects_optional_display_fields_without_leaking_profile(configured):
    app, client, _ = configured
    info = {
        "sub": "reader",
        "permissions": [],
        "username": " Alice ",
        "avatar": "https://images.example.org/avatar.png",
        "email": "synthetic@example.org",
        "private_claim": "must-not-leak",
    }
    app.extensions["auth"] = AuthClient(
        app.config,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=info)),
    )
    assert client.get("/api/auth/me", headers=auth()).json == {
        "id": "reader",
        "permissions": [],
        "username": "Alice",
        "avatar": "https://images.example.org/avatar.png",
    }
    for username, avatar in (
        (None, None),
        ("", ""),
        (123, {}),
        ("x" * 129, "https://images.example.org/" + "x" * 2048),
        ("name\nadmin", "javascript:alert(1)"),
        ("\x00", "http://images.example.org/avatar.png"),
        (" ", "https://user:password@images.example.org/avatar.png"),
        (None, "https://[invalid/avatar.png"),
        (None, "https://images.example.org:invalid/avatar.png"),
    ):
        info.update(username=username, avatar=avatar)
        assert client.get("/api/auth/me", headers=auth()).json == {
            "id": "reader",
            "permissions": [],
        }
    info.update(username="admin", avatar=None)
    assert client.get("/api/auth/me", headers=auth()).json["username"] == "admin"
    assert client.get("/api/admin/sources", headers=auth()).status_code == 403
    info.pop("username")
    info["avatar"] = "https://images.example.org/avatar.png"
    assert client.get("/api/auth/me", headers=auth()).json["avatar"] == info["avatar"]


def test_verified_nickname_and_avatar_aliases_are_projected(configured):
    app, client, _ = configured
    info = {"sub": "reader", "permissions": [], "nickname": " 张大侠 ", "username": "zhang"}
    app.extensions["auth"] = AuthClient(
        app.config, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=info))
    )
    for field in ("avatar", "avatar_url", "picture"):
        info[field] = "https://images.example.org/profile.png"
        response = client.get("/api/auth/me", headers=auth())
        assert response.json == {
            "id": "reader",
            "permissions": [],
            "nickname": "张大侠",
            "username": "zhang",
            "avatar": info[field],
        }
        info.pop(field)
    info.update(nickname="\x00", username=None)
    assert client.get("/api/auth/me", headers=auth()).json == {"id": "reader", "permissions": []}

    info.clear()
    info.update(
        sub="reader",
        permissions=[],
        name="张大侠",
        preferred_username="zhang",
        picture="https://images.example.org/profile.png",
    )
    assert client.get("/api/auth/me", headers=auth()).json == {
        "id": "reader",
        "permissions": [],
        "nickname": "张大侠",
        "username": "zhang",
        "avatar": "https://images.example.org/profile.png",
    }
