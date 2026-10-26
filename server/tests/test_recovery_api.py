"""Blocked-source HTTP recovery: saved-resource reuse, depth bounds and permission fences."""

import asyncio
import fcntl
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_onboarding import BASE, run_onboarding, run_recipe_worker, setup_source

from youju import create_app
from youju.api.auth import AuthClient
from youju.jobs import public_jobs
from youju.models import (
    AuditResult,
    AuditRun,
    Base,
    FetchAttempt,
    OnboardingResult,
    RepairRequest,
    ResourceScan,
    Source,
    SourcePipeline,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.notices import decide_route
from youju.onboarding.auditing import current_source_positions, evaluation_day

HEADERS = {"Authorization": "Bearer operator"}


def client_for(store):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": store.engine.url.database,
            "SOURCE_STORAGE": str(store.blobs.root),
            "AUTH_ISSUER": "https://synthetic.example.org",
            "AUTH_CLIENT_ID": "synthetic",
            "AUTH_CLIENT_SECRET": "synthetic-only",
        }
    )
    app.extensions["auth"] = AuthClient(
        app.config,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "sub": "operator",
                    "permissions": ["youju:source:manage"]
                    if request.headers and b"operator" in request.content
                    else [],
                },
            )
        ),
    )
    return app.test_client()


def blocked_depth(store, depth=21):
    with Session(store.engine) as session, session.begin():
        row = session.get(SourcePipeline, "source")
        row.config = dict(row.config, max_depth=20)
        row.stage, row.state = "inventory", "blocked"
        row.summary = {"inventory_incomplete": True, "inventory_issues": []}
        target = store._register(
            session, row, session.get(Source, "source"), "example.org", BASE + "deep", depth
        )
        return target.id


def test_resume_api_projects_depth_reason_and_reuses_completed_material(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        for _ in range(5):
            state = asyncio.run(pipeline.step("source", limit=50))
            if state["stage"] == "classification":
                break
        with Session(engine) as session:
            before = session.scalar(select(func.count()).select_from(FetchAttempt))
            done = set(
                session.scalars(
                    select(ResourceScan.resource_id).where(ResourceScan.state == "done")
                )
            )
        target = blocked_depth(store)
        client = client_for(store)
        response = client.get("/api/admin/sources/source", headers=HEADERS)
        recovery = response.json["onboarding"]["recovery"]
        assert response.json["onboarding"]["summary"]["error"]["code"] == "INVENTORY_DEPTH_LIMIT"
        assert recovery["can_resume"] and recovery["suggested_limits"] == {"max_depth": 100}
        assert recovery["depth_limited_resources"] == 1
        resumed = client.post(
            "/api/admin/sources/source/resume", json={"max_depth": 100}, headers=HEADERS
        )
        assert resumed.status_code == 202, resumed.json
        assert resumed.json["onboarding"]["state"] == "ready"
        assert resumed.json["onboarding"]["recovery"]["limits"]["max_depth"] == 100
        assert "error" not in resumed.json["onboarding"]["summary"]
        with Session(engine) as session:
            assert session.get(ResourceScan, target).state == "ready"
            assert session.scalar(select(func.count()).select_from(FetchAttempt)) == before
            assert done <= set(
                session.scalars(
                    select(ResourceScan.resource_id).where(ResourceScan.state == "done")
                )
            )
        # HTTP only queues: no network or model generation happened in the recovery request.
        calls = list(transport.calls)
        assert (
            client.post(
                "/api/admin/sources/source/resume", json={"max_depth": 100}, headers=HEADERS
            ).status_code
            == 409
        )
        assert transport.calls == calls
    finally:
        engine.dispose()


def test_recovery_rejects_invalid_limits_unauthorized_callers_and_live_owners(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        with Session(engine) as session, session.begin():
            for scan in session.scalars(select(ResourceScan)):
                scan.state = "done"
        blocked_depth(store)
        client = client_for(store)
        for value in (101, 19, True, "100"):
            response = client.post(
                "/api/admin/sources/source/resume", json={"max_depth": value}, headers=HEADERS
            )
            assert response.status_code == 400
        assert (
            client.post("/api/admin/sources/source/resume", json={}, headers=HEADERS).status_code
            == 400
        )
        assert (
            client.post(
                "/api/admin/sources/source/resume",
                json={"max_depth": 100, "allow_http": True},
                headers=HEADERS,
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/admin/sources/source/resume",
                json={"max_depth": 100},
                headers={"Authorization": "Bearer reader"},
            ).status_code
            == 403
        )
        assert (
            client.post("/api/admin/sources/source/repair", json={}, headers=HEADERS).status_code
            == 409
        )
        with Session(engine) as session, session.begin():
            session.get(SourcePipeline, "source").lease_token = "live-owner"
        assert client.get("/api/admin/sources/source", headers=HEADERS).json["onboarding"][
            "recovery"
        ]["requires_operator"]
        assert (
            client.post(
                "/api/admin/sources/source/resume", json={"max_depth": 100}, headers=HEADERS
            ).status_code
            == 409
        )
    finally:
        engine.dispose()


def test_depth_100_is_allowed_but_resume_cannot_bypass_depth_101(tmp_path):
    engine, store, _, _ = setup_source(tmp_path)
    try:
        with Session(engine) as session, session.begin():
            row = session.get(SourcePipeline, "source")
            assert row.config["max_depth"] == 100
            source = session.get(Source, "source")
            at_limit = store._register(session, row, source, "example.org", BASE + "at-limit", 100)
            beyond = store._register(
                session, row, source, "example.org", BASE + "beyond-limit", 101
            )
            accepted_id, rejected_id = at_limit.id, beyond.id
        with Session(engine) as session:
            assert session.get(ResourceScan, accepted_id).state == "ready"
            assert session.get(ResourceScan, rejected_id).state == "review"
        store.resume("source", max_depth=100)
        with Session(engine) as session:
            assert session.get(ResourceScan, rejected_id).state == "review"
            assert session.get(SourcePipeline, "source").summary["inventory_incomplete"]
    finally:
        engine.dispose()


async def prepare_repair(pipeline, *, initial):
    """Managed synthetic authoring, followed by a saved failed audit to review."""
    store = pipeline.store
    if initial:
        for _ in range(12):
            state = await pipeline.step("source", limit=50)
            if state["stage"] == "authoring" and state["state"] == "running":
                await run_recipe_worker(pipeline)
            if state["stage"] == "audit":
                break
        assert state["stage"] == "audit", state
    else:
        assert (await run_onboarding(pipeline))[-1]["stage"] == "active"
    bundle = next(b for b in store.bundles("source") if b["url"] == BASE + "two")
    with Session(store.engine) as session, session.begin():
        state = session.get(SourcePipeline, "source")
        binding = session.get(SourceRecipeBinding, "source")
        recipe_id = binding.candidate_recipe_id if initial else binding.active_recipe_id
        generation = state.summary["candidate_generation"] if initial else binding.generation + 1
        errors = [
            {
                "code": "SCRIPT_PARSE_FAILED",
                "failure_code": "SCRIPT_PARSE_FAILED",
                "scope": "resource",
                "message": "Synthetic extraction failure",
            }
        ]
        row = AuditResult(
            bundle_id=bundle["id"],
            recipe_id=recipe_id,
            generation=generation,
            evaluated_on=evaluation_day(),
            status="failed",
            errors=errors,
            result={
                "schema_version": "position-facts-v3",
                "status": "failed",
                "positions": [],
                "errors": errors,
                "coverage": {"supported": False, "complete": False, "needs_detailed": True},
                "routing": decide_route(bundle, bundle.get("assessment"), now=utc_now()).as_dict(),
                "execution": {
                    "path": "detailed",
                    "model_used": False,
                    "runtime_contract": "recipe-runtime-v3",
                    "parser": "parser.py",
                    "script_issues": [],
                    "failure": {
                        "code": "SCRIPT_PARSE_FAILED",
                        "origin": "script_draft",
                        "evidence_digest": "a" * 64,
                    },
                },
            },
        )
        session.add(row)
        session.flush()
        run = AuditRun(
            source_id="source",
            recipe_id=recipe_id,
            kind="onboarding" if initial else "incremental",
            status="blocked",
            finished_at=utc_now(),
            summary={
                "evaluated_on": evaluation_day(),
                "generation": generation,
                "statuses": {"failed": 1},
                "passed": False,
                "results": [{"id": row.id, "bundle_id": bundle["id"], "status": "failed"}],
            },
        )
        session.add(run)
        session.flush()
        if initial:
            state.state = "blocked"
            state.summary = dict(state.summary, audit=run.summary, audit_id=run.id)
        else:
            assert (state.stage, state.state) == ("active", "ready")
        return run.id


def persistent_snapshot(store):
    """Queueing repair may insert requests, not change material, admission or job rows."""
    with Session(store.engine) as session:
        return {
            name: [
                dict(row)
                for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for name, table in Base.metadata.tables.items()
            if name not in {"recipe_repair_requests", "recipe_repair_incidents"}
        }


def published_ids(store):
    with Session(store.engine) as session:
        return [row["id"] for row in public_jobs(session, day=date.fromisoformat(evaluation_day()))]


@pytest.mark.parametrize("initial", [False, True], ids=["active-ready", "initial-blocked-audit"])
def test_reviewed_audit_queues_durable_repair_without_fetch_or_activation(tmp_path, initial):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        audit_id = asyncio.run(prepare_repair(pipeline, initial=initial))
        before = persistent_snapshot(store)
        calls = list(transport.calls)
        state = store.status("source")
        visible = published_ids(store)
        current = current_source_positions(store, "source")
        assert bool(visible) is not initial
        client = client_for(store)
        assert client.get("/api/admin/sources/source", headers=HEADERS).json["onboarding"][
            "recovery"
        ]["can_repair"]
        # Explicit audit context is carried to the durable request, not trusted as ownership.
        reply = client.post(
            "/api/admin/sources/source/repair", json={"audit_id": audit_id}, headers=HEADERS
        )
        assert reply.status_code == 202, reply.json
        onboarding = reply.json["onboarding"]
        assert onboarding["stage"] == ("audit" if initial else "completed")
        assert onboarding["state"] == ("blocked" if initial else "completed")
        if not initial and state["state"] == "blocked":
            assert onboarding["maintenance"]["state"] == "blocked"
        else:
            assert onboarding["maintenance"] is None
        assert onboarding["active_recipe_id"] == state["active_recipe_id"]
        assert onboarding["candidate_recipe_id"] == state["candidate_recipe_id"]
        after = store.status("source")
        assert (after["stage"], after["state"]) == (state["stage"], state["state"])
        assert after["binding"] == state["binding"]
        assert after["summary"] == state["summary"]
        assert "repair_request" not in after["summary"]
        assert transport.calls == calls
        assert persistent_snapshot(store) == before
        assert published_ids(store) == visible
        after_current = current_source_positions(store, "source")
        for values in (current, after_current):
            for row in values:
                # A read advances only its job-clock evaluation, not saved facts.
                row["validity"].pop("evaluated_at", None)
        assert after_current == current
        with Session(engine) as session:
            request = session.scalar(select(RepairRequest))
            assert request.status == "queued" and request.origin == "manual_audit"
            assert request.base_recipe_id == (
                state["candidate_recipe_id"] or state["active_recipe_id"]
            )
            assert request.context["requested_audit_id"] == audit_id
            assert request.context["base_generation"] == state["binding"]["generation"]
            assert request.context["baseline_recipe_id"] == state["active_recipe_id"]
            assert (session.get(OnboardingResult, "source") is None) is initial
        # Duplicate requests must not dispatch another Agent or replace the binding.
        assert (
            client.post("/api/admin/sources/source/repair", json={}, headers=HEADERS).status_code
            == 409
        )
        assert persistent_snapshot(store) == before and transport.calls == calls
    finally:
        engine.dispose()


def test_repair_context_retry_and_permission_lock_and_lease_fences(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        audit_id = asyncio.run(prepare_repair(pipeline, initial=False))
        client = client_for(store)
        before = persistent_snapshot(store)
        calls = list(transport.calls)
        for context in (
            {"audit_id": "invalid"},
            {"previous_request_id": True},
            {"audit_id": audit_id, "untrusted": "ignored?"},
        ):
            assert (
                client.post(
                    "/api/admin/sources/source/repair", json=context, headers=HEADERS
                ).status_code
                == 400
            )
        assert (
            client.post(
                "/api/admin/sources/source/repair",
                json={"audit_id": audit_id},
                headers={"Authorization": "Bearer reader"},
            ).status_code
            == 403
        )
        # Correctly shaped but unknown context cannot authorize an unrelated audit/request.
        for context in ({"audit_id": "0" * 32}, {"previous_request_id": "0" * 32}):
            assert (
                client.post(
                    "/api/admin/sources/source/repair", json=context, headers=HEADERS
                ).status_code
                == 409
            )
        with open(store.engine.url.database + ".source.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                assert (
                    client.post(
                        "/api/admin/sources/source/repair", json={}, headers=HEADERS
                    ).status_code
                    == 409
                )
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        assert persistent_snapshot(store) == before
        with Session(engine) as session, session.begin():
            session.get(SourcePipeline, "source").lease_token = "live-owner"
        assert (
            client.post("/api/admin/sources/source/repair", json={}, headers=HEADERS).status_code
            == 409
        )
        with Session(engine) as session, session.begin():
            session.get(SourcePipeline, "source").lease_token = None
        assert persistent_snapshot(store) == before
        assert (
            client.post(
                "/api/admin/sources/source/repair", json={"audit_id": audit_id}, headers=HEADERS
            ).status_code
            == 202
        )
        with Session(engine) as session, session.begin():
            parent = session.scalar(select(RepairRequest))
            parent.status = "failed"
            parent_id = parent.id
        retry = client.post(
            "/api/admin/sources/source/repair",
            json={"audit_id": audit_id, "previous_request_id": parent_id},
            headers=HEADERS,
        )
        assert retry.status_code == 202, retry.json
        with Session(engine) as session:
            requests = session.scalars(select(RepairRequest)).all()
            assert len(requests) == 2
            child = next(row for row in requests if row.id != parent_id)
            assert child.origin == "audit_feedback" and child.status == "queued"
            assert child.context["parent_request_id"] == parent_id
            assert child.context["requested_audit_id"] == audit_id
        assert persistent_snapshot(store) == before
        assert transport.calls == calls
    finally:
        engine.dispose()
