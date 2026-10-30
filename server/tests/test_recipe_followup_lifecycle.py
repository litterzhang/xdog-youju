"""Runtime re-audits never rewrite terminal source revision decisions."""

import asyncio
from unittest.mock import AsyncMock

import pytest
import test_route_fact_admission as admission
from sqlalchemy.orm import Session
from test_route_fact_admission import STAMP

from youju.models import AuditRun, SourcePipeline, SourceRecipe, SourceRecipeBinding
from youju.onboarding.recipe_bridge import SourceRecipeCoordinator
from youju.onboarding.recipe_client import RecipeRPCError


@pytest.fixture
def store(monkeypatch):
    yield from admission.store.__wrapped__(monkeypatch)


def failed_audit(store, *, same_revision=True):
    with Session(store.engine) as session, session.begin():
        active = session.get(SourceRecipe, "recipe")
        active.revision_id, active.workspace_id = "accepted", "workspace"
        session.add(
            SourceRecipe(
                id="candidate",
                source_id="source",
                digest="new-runtime",
                body_ref=active.body_ref,
                status="validated",
                workspace_id="workspace",
                revision_id="accepted" if same_revision else "new-revision",
            )
        )
        binding = session.get(SourceRecipeBinding, "source")
        binding.workspace_id = "workspace"
        binding.active_revision_id = "accepted"
        binding.candidate_revision_id = "accepted" if same_revision else "new-revision"
        binding.candidate_recipe_id = "candidate"
        pipeline = session.get(SourcePipeline, "source")
        pipeline.stage, pipeline.state = "audit", "blocked"
        pipeline.summary = dict(pipeline.summary, audit_id="failed-audit", candidate_generation=2)
        session.add(
            AuditRun(
                id="failed-audit",
                source_id="source",
                recipe_id="candidate",
                kind="onboarding",
                status="blocked",
                finished_at=STAMP,
                summary={
                    "passed": False,
                    "generation": 2,
                    "evaluated_on": "2026-10-24",
                    "results": [],
                    "counts": {},
                },
            )
        )
    rpc = type("RPC", (), {"call": AsyncMock(side_effect=lambda method, **kw: kw)})()
    return SourceRecipeCoordinator(store, rpc, None, classifier_key="c", toolkit_key="t")


def test_accepted_revision_runtime_failure_does_not_send_reject(store):
    coordinator = failed_audit(store)
    asyncio.run(coordinator.reject_candidate("source", "failed-audit"))
    coordinator.rpc.call.assert_not_awaited()
    with Session(store.engine) as session:
        assert session.get(AuditRun, "failed-audit").summary["passed"] is False
        assert "revision_review" not in session.get(SourcePipeline, "source").summary
        assert session.get(SourceRecipeBinding, "source").candidate_recipe_id == "candidate"


@pytest.mark.parametrize("change", ["generation", "candidate", "audit_pointer"])
def test_late_failure_cannot_reject_current_candidate(store, change):
    coordinator = failed_audit(store, same_revision=False)
    with Session(store.engine) as session, session.begin():
        pipeline = session.get(SourcePipeline, "source")
        if change == "generation":
            pipeline.summary = dict(pipeline.summary, candidate_generation=3)
        elif change == "candidate":
            session.get(SourceRecipeBinding, "source").candidate_recipe_id = "recipe"
        else:
            pipeline.summary = dict(pipeline.summary, audit_id="new-audit")
    with pytest.raises(RecipeRPCError, match="RECIPE_REVIEW_CHANGED"):
        asyncio.run(coordinator.reject_candidate("source", "failed-audit"))
    coordinator.rpc.call.assert_not_awaited()


def test_new_revision_reject_is_idempotent_and_legacy_conflict_is_read_only(store):
    from youju.onboarding.recipe_versions import review_diagnostic

    coordinator = failed_audit(store, same_revision=False)
    asyncio.run(coordinator.reject_candidate("source", "failed-audit"))
    asyncio.run(coordinator.reject_candidate("source", "failed-audit"))
    coordinator.rpc.call.assert_awaited_once()
    with Session(store.engine) as session, session.begin():
        row = session.get(SourcePipeline, "source")
        pending = row.summary["revision_review"]
        intent = {**pending["intent"], "revision_id": "accepted"}
        row.summary = dict(row.summary, revision_review={"status": "pending", "intent": intent})
        binding = session.get(SourceRecipeBinding, "source")
        binding.candidate_revision_id = "accepted"
        session.get(SourceRecipe, "candidate").revision_id = "accepted"
    with Session(store.engine) as session:
        before = session.get(SourcePipeline, "source").summary
        diagnostic = review_diagnostic(session, "source")
        assert diagnostic["runtime_review_conflict"] and not diagnostic["changed"]
        assert session.get(SourcePipeline, "source").summary == before
    with pytest.raises(RecipeRPCError, match="RECIPE_RUNTIME_REVIEW_CONFLICT"):
        asyncio.run(coordinator.flush_review("source"))
    coordinator.rpc.call.assert_awaited_once()


def test_review_diagnostic_cli_opens_sqlite_read_only(store, tmp_path):
    import hashlib
    import json
    import sqlite3

    from flask import Flask

    from youju.onboarding.cli import register_onboarding_commands

    failed_audit(store)
    path = tmp_path / "diagnostic.sqlite3"
    connection = store.engine.raw_connection()
    try:
        with sqlite3.connect(path) as target:
            connection.driver_connection.backup(target)
    finally:
        connection.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    app = Flask(__name__)
    register_onboarding_commands(app)
    result = app.test_cli_runner().invoke(
        args=["source-recipe-review-diagnose", "source", "--database", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changed"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
