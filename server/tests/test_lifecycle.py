"""Baseline admission, monitoring and operator authorization are independent."""

import asyncio
import copy
import fcntl
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_admin_api import auth, configured, register_source  # noqa: F401
from test_onboarding import BASE, notice, run_onboarding, setup_source, table

from youju.admin import pipeline_json
from youju.models import (
    AuditRun,
    IncrementalControl,
    IncrementalRun,
    OnboardingResult,
    ScanPolicy,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.onboarding.lifecycle import (
    backfill_lifecycle,
    freeze_onboarding,
    incremental_json,
    set_incremental,
)


def admitted(session, source_id):
    """Minimal admitted state, for API/backfill tests only (no parser execution)."""
    pipeline = session.get(SourcePipeline, source_id)
    recipe = SourceRecipe(source_id=source_id, digest="a" * 64, body_ref="b" * 64, status="active")
    session.add(recipe)
    session.flush()
    audit = AuditRun(
        source_id=source_id,
        recipe_id=recipe.id,
        kind="onboarding",
        status="complete",
        summary={"passed": True, "position_count": 2, "admission": {"policy": "quarantine-v1"}},
        finished_at=utc_now(),
    )
    session.add(audit)
    session.flush()
    pipeline.stage, pipeline.state = "active", "ready"
    binding = session.get(SourceRecipeBinding, source_id)
    if binding is None:
        binding = SourceRecipeBinding(source_id=source_id)
        session.add(binding)
    binding.active_recipe_id = recipe.id
    pipeline.summary = {"audit_id": audit.id, "audit": copy.deepcopy(audit.summary)}
    return pipeline, audit


def test_baseline_default_off_bounded_incremental_and_independent_failure(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session:
            frozen = copy.deepcopy(session.get(OnboardingResult, "source").summary)
            baseline_audit = session.get(OnboardingResult, "source").audit_id
            count = session.scalar(select(func.count()).select_from(AuditRun))
            assert incremental_json(session, "source")["state"] == "disabled"
            assert pipeline_json(session, "source")["stage"] == "completed"
        transport.calls.clear()
        with Session(engine) as session, session.begin():
            for policy in session.scalars(select(ScanPolicy)):
                policy.next_scan_at = utc_now() - timedelta(seconds=1)
        asyncio.run(pipeline.step("source", limit=1))
        assert not transport.calls
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(AuditRun)) == count
            assert session.scalar(select(func.count()).select_from(IncrementalRun)) == 0
        body, media = transport.pages[BASE]
        transport.pages[BASE] = (
            body.replace(b"</body>", '<a href="/new">新增招聘公告</a></body>'.encode()),
            media,
        )
        transport.pages[BASE + "new"] = (notice(table("新增岗位")), media)
        with Session(engine) as session, session.begin():
            set_incremental(session, "source", True)
        asyncio.run(pipeline.step("source", limit=1))
        with Session(engine) as session, session.begin():
            running_id = incremental_json(session, "source")["latest_run"]["id"]
            set_incremental(session, "source", False)
        before = len(transport.calls)
        asyncio.run(pipeline.step("source", limit=1))
        assert len(transport.calls) == before  # Paused batch; no future acquisitions.
        with Session(engine) as session, session.begin():
            set_incremental(session, "source", True)
        for _ in range(20):
            asyncio.run(pipeline.step("source", limit=2))
            with Session(engine) as session:
                run = session.get(IncrementalRun, running_id)
                if run.status != "running":
                    assert run.status == "complete", run.summary
                    assert run.summary["new_resources"] == 1
                    assert run.summary["changed_resources"] == 1
                    assert run.summary["audit"]["passed"]
                    break
        else:
            pytest.fail("Incremental audit did not complete within bounded synthetic passes")
        before = len(transport.calls)
        asyncio.run(pipeline.step("source", limit=2))
        assert len(transport.calls) == before
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(IncrementalRun)) == 1
            assert session.get(OnboardingResult, "source").summary == frozen
            assert session.get(SourcePipeline, "source").summary["audit_id"] == baseline_audit
        # A source-wide incremental failure is isolated; the first admission stays finalized.
        with Session(engine) as session, session.begin():
            for policy in session.scalars(select(ScanPolicy)):
                policy.next_scan_at = utc_now() - timedelta(seconds=1)
        run_id = pipeline.incrementals.begin("source", scheduled=True)
        with Session(engine) as session, session.begin():
            session.get(IncrementalRun, run_id).summary = {"audit": {"passed": False}}
        pipeline.incrementals.update(run_id, complete=True)
        with Session(engine) as session:
            assert incremental_json(session, "source")["error"]["code"] == (
                "INCREMENTAL_AUDIT_NOT_READY"
            )
        pipeline.incrementals.block("source", "SOURCE_EXECUTION_FAILURE")
        pipeline.incrementals.update(run_id)
        with Session(engine) as session:
            assert incremental_json(session, "source")["state"] == "blocked"
            assert session.get(OnboardingResult, "source").summary == frozen
            assert session.get(SourcePipeline, "source").state == "ready"
            assert pipeline_json(session, "source")["state"] == "completed"
    finally:
        engine.dispose()


def test_toggle_permissions_validation_and_inflight_disable(configured):  # noqa: F811
    app, client, _ = configured
    _, source = register_source(client)
    source_id = source["id"]
    client.post(
        f"/api/admin/sources/{source_id}/approve",
        json={"authority_evidence": "合成依据"},
        headers=auth(),
    )
    client.post(f"/api/admin/sources/{source_id}/onboard", json={}, headers=auth())
    path = f"/api/admin/sources/{source_id}/incremental"
    assert client.get(path, headers=auth("normal")).status_code == 403
    assert client.post(path, json={"enabled": True}, headers=auth("normal")).status_code == 403
    assert client.post(path, json={"enabled": True}, headers=auth()).status_code == 409
    assert client.post(path, json={"enabled": 1}, headers=auth()).status_code == 400
    assert (
        client.post(path, json={"enabled": True, "source_id": "other"}, headers=auth()).status_code
        == 400
    )
    from youju.database import create_sqlite_engine

    engine = create_sqlite_engine(app.config["DATABASE"])
    try:
        with Session(engine) as session, session.begin():
            pipeline, audit = admitted(session, source_id)
            freeze_onboarding(session, pipeline, audit)
        assert client.get(path, headers=auth()).json["enabled"] is False
        assert client.post(path, json={"enabled": True}, headers=auth()).json["enabled"] is True
        # Disable uses its independent control transaction, not the acquisition process lock.
        with open(app.config["DATABASE"] + ".source.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = client.post(path, json={"enabled": False}, headers=auth())
            assert result.status_code == 200 and result.json["enabled"] is False
        assert client.post(path, json={"enabled": True}, headers=auth("normal")).status_code == 403
        with Session(engine) as session:
            assert session.get(IncrementalControl, source_id).enabled is False
    finally:
        engine.dispose()


def test_legacy_backfill_uses_first_passed_baseline_not_incremental(tmp_path):
    engine, _, _, _ = setup_source(tmp_path)
    try:
        with Session(engine) as session, session.begin():
            pipeline, audit = admitted(session, "source")
            original_id = audit.id
            first_time = audit.finished_at
            increment = AuditRun(
                source_id="source",
                recipe_id=audit.recipe_id,
                kind="incremental",
                status="complete",
                summary={"passed": True, "position_count": 999},
                finished_at=utc_now(),
            )
            session.add(increment)
            session.flush()
            pipeline.summary = {"audit": increment.summary, "audit_id": increment.id}
        assert backfill_lifecycle(engine) == 1
        assert backfill_lifecycle(engine) == 0
        with Session(engine) as session:
            frozen = session.get(OnboardingResult, "source")
            assert frozen.audit_id == original_id and frozen.completed_at == first_time
            assert frozen.summary["audit"]["position_count"] == 2
            assert frozen.provenance == "legacy-onboarding-audit"
            assert incremental_json(session, "source")["enabled"] is False
    finally:
        engine.dispose()
