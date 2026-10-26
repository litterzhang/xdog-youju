"""Focused source repair scheduling and admission checks; synthetic saved material only."""

import asyncio
import copy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import BASE, run_onboarding, setup_source

from youju.models import (
    AuditResult,
    AuditRun,
    FetchAttempt,
    RepairRequest,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.onboarding.repair import audit_feedback, repair_verdict, request_repair


def test_repair_schedules_saved_failures_and_preserves_baseline_and_acquisition(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        with Session(engine) as session, session.begin():
            state = session.get(SourcePipeline, "source")
            binding = session.get(SourceRecipeBinding, "source")
            recipe_id, summary = binding.active_recipe_id, copy.deepcopy(state.summary)
            new_generation = binding.generation + 1
            old_run = session.get(AuditRun, summary["audit_id"])
            rows = session.scalars(select(AuditResult)).all()
            # Simulate one fresh bounded audit rejection; do not overwrite the
            # original successful outcomes used by the initial synthetic source.
            new_results = []
            target = next(b for b in store.bundles("source") if b["url"] == BASE + "two")["id"]
            for old in rows:
                result, errors = copy.deepcopy(old.result), []
                failed = old.bundle_id == target
                if failed:
                    errors = [
                        {
                            "code": "TRUNCATED_LITERAL_FIELD",
                            "message": "Synthetic title error",
                            "path": "positions[0].title",
                            "failure_code": "DRAFT_INVALID",
                        }
                    ]
                    result = {**result, "status": "failed", "positions": [], "errors": errors}
                    result["execution"] = {
                        **result["execution"],
                        "failure": {
                            "code": "DRAFT_INVALID",
                            "origin": "script_draft",
                            "evidence_digest": "a" * 64,
                        },
                    }
                row = AuditResult(
                    bundle_id=old.bundle_id,
                    recipe_id=recipe_id,
                    evaluated_on=old.evaluated_on,
                    generation=new_generation,
                    status="failed" if failed else "success",
                    result=result,
                    errors=errors,
                )
                session.add(row)
                session.flush()
                new_results.append({"id": row.id})
            latest = AuditRun(
                source_id="source",
                recipe_id=recipe_id,
                kind="incremental",
                status="complete",
                finished_at=utc_now(),
                summary={**old_run.summary, "generation": new_generation, "results": new_results},
            )
            session.add(latest)
            session.flush()
            binding.generation = new_generation
            state.summary = dict(state.summary, audit_id=latest.id, audit=latest.summary)
            audit_id = latest.id
        calls = list(transport.calls)
        with Session(engine) as session:
            attempts = session.scalars(select(FetchAttempt.id)).all()
        feedback = audit_feedback(store, "source", audit_id)
        assert len(feedback["issues"]) == 1 and len(feedback["baseline_parsed"]) == 2
        requested = request_repair(store, "source")
        request_view = requested["repairs"][0]
        assert requested["stage"] == "active" and requested["state"] == "ready"
        with Session(engine) as session:
            request = session.get(RepairRequest, request_view["id"])
            saved = json.loads(store.blobs.read(request.feedback_ref))
            assert request.origin == "manual_audit" and request.status == "queued"
            assert saved["selected_bundle_ids"] == [target]
            assert request.base_recipe_digest and saved["base_recipe"]
            assert request.context["base_revision_id"]
            assert session.get(SourceRecipeBinding, "source").active_recipe_id == recipe_id
            assert session.scalars(select(FetchAttempt.id)).all() == attempts
            active = session.get(SourceRecipe, recipe_id)
            original_validation = copy.deepcopy(active.validation)
            assert active.status == "active"
            request_id = request.id
        assert transport.calls == calls
        with pytest.raises(ValueError, match="already pending"):
            request_repair(store, "source")
        # A reviewed retry gets a fresh intent and keeps the original regression floors.
        with Session(engine) as session, session.begin():
            session.get(RepairRequest, request_id).status = "failed"
        resumed = request_repair(store, "source", previous_request_id=request_id)
        assert resumed["stage"] == "active" and resumed["state"] == "ready"
        with Session(engine) as session:
            retry = session.get(RepairRequest, resumed["repairs"][0]["id"])
            assert retry.origin == "audit_feedback" and retry.id != request_id
            assert retry.context["parent_request_id"] == request_id
            active = session.get(SourceRecipe, recipe_id)
            assert active.status == "active" and active.validation == original_validation
    finally:
        engine.dispose()


def test_repair_requires_full_targets_and_no_parsed_row_loss():
    feedback = {
        "baseline_parsed": ["old"],
        "baseline_position_counts": {"old": 2},
        "selected_bundle_ids": ["broken"],
    }
    old = {"bundle_id": "old", "status": "success", "errors": [], "position_count": 2}
    repaired = {"bundle_id": "broken", "status": "partial", "errors": [], "position_count": 1}
    assert not repair_verdict(feedback, [old, repaired], complete=False)["passed"]
    assert repair_verdict(feedback, [old, repaired], complete=True)["passed"]
    assert repair_verdict(feedback, [old], complete=True)["unrepaired_targets"] == ["broken"]
    assert repair_verdict(feedback, [dict(old, position_count=1), repaired], complete=True)[
        "regressions"
    ] == ["old"]
    assert not repair_verdict(
        feedback, [old, dict(repaired, errors=[{"code": "UNSUPPORTED_FIELD"}])], complete=True
    )["passed"]
