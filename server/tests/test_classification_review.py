"""Small synthetic batch tests; official SDK stub ports, no paid model/network."""

import asyncio
import copy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import setup_source
from test_sourcekit import unknown

from youju import create_app
from youju.models import PageAssessment, SourcePipeline
from youju.onboarding.classification import Classifier
from youju.onboarding.classification_budget import ClassificationBudget
from youju.onboarding.classification_review import (
    BatchClassificationReviewer,
    ReviewPolicy,
    configured_reviewer,
)
from youju.onboarding.xdog import XdogModels

KEY = "review-test"


def prepare(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)

    async def inventory():
        for _ in range(4):
            await pipeline.worker.run_once("source", 50)
            store.build_bundles("source")
            if store.finish_inventory("source"):
                break

    asyncio.run(inventory())
    pages = store.bundles("source")
    for page in pages:
        result = (
            asyncio.run(Classifier().classify(page)) if page["url"].endswith("/") else unknown()
        )
        store.assess(page, result, KEY)
    return engine, store


def answer(page):
    blocks = [b for doc in page["page_digest"]["documents"] for b in doc["blocks"]]
    evidence = next(b["id"] for b in blocks if "招聘" in b["text"])
    return {
        "page_id": page["page_id"],
        "assessment": {
            **unknown(),
            "page_roles": ["article"],
            "business_type": "recruitment",
            "requires_review": False,
            "evidence_ids": [evidence],
            "reason": "Saved vacancy headline",
        },
    }


def test_batch_saves_each_page_and_does_not_inflate_cheap_coverage_or_recharge(tmp_path):
    engine, store = prepare(tmp_path)
    calls = []

    def stub(task, payload, schema, tier):
        assert task == "review_page_classification_batch" and tier == "large"
        assert len(json.dumps(payload, ensure_ascii=False).encode()) <= 128_000
        calls.append(payload)
        return {"results": [answer(page) for page in payload["pages"]]}

    models = XdogModels(stub=stub)
    reviewer = configured_reviewer(
        store,
        models,
        KEY,
        {
            "CLASSIFICATION_REVIEW_ENABLED": True,
            "CLASSIFICATION_REVIEW_BATCH_SIZE": 2,
            "CLASSIFICATION_REVIEW_MAX_BATCHES": 1,
        },
    )
    try:
        before = ClassificationBudget(store, KEY).summarize("source")["cheap_covered"]
        summary = asyncio.run(reviewer.run_once("source"))
        assert len(calls) == 1 and len(calls[0]["pages"]) == 2
        assert summary["page_outcomes"] == {"resolved": 2}
        assert summary["deferred_budget"] == 1 and not summary["can_continue"]
        assert summary["human_review_required"] is False
        assert ClassificationBudget(store, KEY).summarize("source")["cheap_covered"] == before
        again = BatchClassificationReviewer(store, KEY, reviewer.models, reviewer.policy)
        asyncio.run(again.run_once("source"))
        assert len(calls) == 1
        with Session(engine) as session:
            ledger = session.get(SourcePipeline, "source").summary["classification_agent_review"]
            pages = ledger["batches"][0]["pages"]
            for page in pages:
                row = session.get(PageAssessment, page["assessment_id"])
                assert row.result["model_metadata"]["review_kind"] == "ai_batch"
                assert page["original_ref"] and page["result_ref"]
    finally:
        engine.dispose()


def test_bad_cross_page_evidence_only_rejects_that_page(tmp_path):
    engine, store = prepare(tmp_path)

    def stub(task, payload, schema, tier):
        rows = [answer(page) for page in payload["pages"]]
        rows[1]["assessment"]["evidence_ids"] = rows[0]["assessment"]["evidence_ids"]
        return {"results": rows}

    reviewer = BatchClassificationReviewer(
        store, KEY, XdogModels(stub=stub), ReviewPolicy(batch_size=2)
    )
    try:
        result = asyncio.run(reviewer.run_once("source"))
        assert result["page_outcomes"] == {"resolved": 1, "invalid": 1}
        assert result["can_continue"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["provider", "duplicate", "stale", "cancel"])
def test_failed_or_stale_batch_is_never_replayed_or_force_adopted(tmp_path, failure):
    engine, store = prepare(tmp_path)
    calls = []

    def stub(task, payload, schema, tier):
        calls.append(task)
        if failure == "provider":
            raise RuntimeError("synthetic-private-provider-error")
        if failure == "cancel":
            raise asyncio.CancelledError
        rows = [answer(page) for page in payload["pages"]]
        if failure == "duplicate":
            return {"results": rows + copy.deepcopy(rows)}
        with Session(engine) as session, session.begin():
            for row in session.scalars(
                select(PageAssessment).where(PageAssessment.classifier_key == KEY)
            ):
                row.result = {**row.result, "reason": "Changed while Agent was in flight"}
        return {"results": rows}

    reviewer = BatchClassificationReviewer(store, KEY, XdogModels(stub=stub))
    try:
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(reviewer.run_once("source"))
        else:
            result = asyncio.run(reviewer.run_once("source"))
            assert not result["page_outcomes"].get("resolved")
        asyncio.run(reviewer.run_once("source"))
        assert len(calls) == 1
        with Session(engine) as session:
            ledger = session.get(SourcePipeline, "source").summary["classification_agent_review"]
            assert "synthetic-private-provider-error" not in json.dumps(ledger)
    finally:
        engine.dispose()


def test_review_never_changes_inflight_authoring_and_cli_defaults_to_dry_run(tmp_path):
    engine, store = prepare(tmp_path)
    calls = []
    reviewer = BatchClassificationReviewer(
        store, KEY, XdogModels(stub=lambda *args: calls.append(args))
    )
    try:
        with Session(engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, "source")
            pipeline.stage = "authoring"
        with pytest.raises(ValueError, match="classification/incremental"):
            asyncio.run(reviewer.run_once("source"))
        assert not calls
        app = create_app({"TESTING": True})
        result = app.test_cli_runner().invoke(args=["source-classification-review", "source"])
        assert result.exit_code == 0 and json.loads(result.output)["provider_calls"] == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("problem", ["oversize", "capture"])
def test_unreviewable_page_never_spends_model_budget(tmp_path, monkeypatch, problem):
    from youju.onboarding import classification_review

    engine, store = prepare(tmp_path)
    calls = []
    if problem == "capture":
        monkeypatch.setattr(classification_review, "page_issues", lambda bundle: ["unreadable"])
    else:
        build = classification_review.build_digest

        def oversized(bundle):
            value = build(bundle)
            value["documents"][0]["title"] = "large" * 50_000
            return value

        monkeypatch.setattr(classification_review, "build_digest", oversized)
    reviewer = BatchClassificationReviewer(
        store, KEY, XdogModels(stub=lambda *args: calls.append(args))
    )
    try:
        summary = asyncio.run(reviewer.run_once("source"))
        assert not calls and summary["reserved_batches"] == 0
        assert summary["capture_or_input_unresolved"] == 3
        assert not summary["can_continue"] and not summary["human_review_required"]
    finally:
        engine.dispose()


def test_pipeline_batches_review_before_advancing_to_authoring(tmp_path):
    from youju.onboarding.pipeline import Pipeline

    engine, store = prepare(tmp_path)
    calls = []

    def stub(task, payload, schema, tier):
        calls.append(len(payload["pages"]))
        return {"results": [answer(page) for page in payload["pages"]]}

    pipeline = Pipeline(store)
    pipeline.classifier_key = KEY
    pipeline.classification_budget = ClassificationBudget(store, KEY)
    pipeline.classification_reviewer = BatchClassificationReviewer(
        store, KEY, XdogModels(stub=stub), ReviewPolicy(batch_size=2)
    )
    try:
        first = asyncio.run(pipeline.step("source"))
        assert first["stage"] == "classification"
        second = asyncio.run(pipeline.step("source"))
        assert second["stage"] == "authoring"
        assert calls == [2, 1]
        assert second["summary"]["classification"]["methods"]["large"] == 3
    finally:
        engine.dispose()
