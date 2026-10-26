"""Saved-material reanalysis controls; temporary SQLite and synthetic transport only."""

import asyncio
import fcntl
import json
import socket
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import BASE, notice, setup_source, table

from youju import create_app
from youju.crawlers import CrawlError, CrawlErrorCode
from youju.models import (
    AuditResult,
    AuditRun,
    Base,
    OrganizationScope,
    ParseResult,
    Position,
    RegistryStatus,
    Resource,
    ResourceKind,
    ResourceScan,
    Source,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import utc_now
from youju.notices import decide_route
from youju.onboarding.acquisition import AcquisitionWorker
from youju.onboarding.auditing import current_source_positions, evaluation_day
from youju.onboarding.recipes import candidate_digest, template_candidate
from youju.onboarding.store import PipelineStore, json_bytes, toolkit_key
from youju.positions.parsing import table_row_facts


def forbidden(*args, **kwargs):
    raise AssertionError("Reanalysis must not acquire resources or call a model")


@pytest.fixture
def saved_source(tmp_path, monkeypatch):
    # Even fixture construction may not reach the network. The synthetic adapter
    # records the original acquisition separately from the operation under test.
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    pages = {
        BASE: (
            "<html><title>招聘公告列表</title><body><h1>招聘公告列表</h1>"
            '<a href="/one">公开招聘公告</a><a href="/missing.pdf">历史附件</a>'
            '<a href="/missing.docx">历史附件</a></body></html>'.encode(),
            "text/html; charset=utf-8",
        ),
        BASE + "one": (notice(table()), "text/html; charset=utf-8"),
        BASE + "missing.pdf": CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Synthetic denial"),
        BASE + "missing.docx": CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Synthetic denial"),
    }
    engine, store, transport, pipeline = setup_source(tmp_path, pages=pages)
    try:
        assert asyncio.run(pipeline.step("source"))["stage"] == "classification"
        assert asyncio.run(pipeline.step("source"))["stage"] == "authoring"
        assert pipeline.models.calls == []
        with Session(engine) as session, session.begin():
            source = session.get(SourcePipeline, "source")
            candidate = template_candidate()
            recipe = SourceRecipe(
                id="old-recipe",
                source_id="source",
                workspace_id="synthetic-prior-workspace",
                revision_id="synthetic-prior-revision",
                digest=candidate_digest(candidate),
                body_ref=store.blobs.put(json_bytes(candidate)),
                status="active",
                validation={"toolkit_key": toolkit_key(), "history": [{"round": 1}]},
            )
            session.add(recipe)
            session.flush()
            resource = session.scalar(
                select(Resource).where(Resource.normalized_url == BASE + "one")
            )
            scan = session.get(ResourceScan, resource.id)
            parse = ParseResult(
                id="old-parse",
                version_id=scan.last_version_id,
                parser_key="b" * 64,
                status="valuable",
                role="notice",
            )
            session.add(parse)
            session.flush()
            audit_summary = {"passed": True, "generation": 4, "position_count": 1}
            facts = table_row_facts({"title": "历史岗位"}).as_dict()
            bundle = store.load_bundle(scan.bundle_id, classifier_key=pipeline.classifier_key)
            route = decide_route(bundle, bundle.get("assessment"), now=utc_now()).as_dict()
            result = {
                "schema_version": "position-facts-v3",
                "status": "success",
                "positions": [facts],
                "errors": [],
                "coverage": {"supported": True, "complete": True, "needs_detailed": False},
                "routing": route,
                "execution": {
                    "path": route["mode"],
                    "model_used": False,
                    "runtime_contract": "recipe-runtime-v3",
                    "script_issues": [],
                },
            }
            session.add_all(
                [
                    Position(
                        parse_id=parse.id,
                        ordinal=1,
                        facts=table_row_facts({"title": "历史岗位"}).as_dict(),
                        evidence={},
                    ),
                    AuditResult(
                        bundle_id=scan.bundle_id,
                        recipe_id=recipe.id,
                        evaluated_on=evaluation_day(),
                        generation=4,
                        status="success",
                        parse_id=parse.id,
                        result=result,
                    ),
                    AuditRun(
                        id="old-audit",
                        source_id="source",
                        recipe_id=recipe.id,
                        kind="onboarding",
                        status="complete",
                        summary=audit_summary,
                        finished_at=utc_now(),
                    ),
                    Resource(
                        id="structural-asset",
                        original_url=BASE + "style.css",
                        source_id="source",
                        kind=ResourceKind.UNKNOWN,
                    ),
                ]
            )
            session.flush()
            session.add(ResourceScan(resource_id="structural-asset", state="ready"))
            source.stage, source.state = "active", "ready"
            binding = session.get(SourceRecipeBinding, "source")
            binding.active_recipe_id = binding.candidate_recipe_id = recipe.id
            binding.workspace_id = recipe.workspace_id
            binding.active_revision_id = binding.candidate_revision_id = recipe.revision_id
            binding.generation = 4
            source.activated_at = utc_now() - timedelta(days=1)
            source.summary = dict(
                source.summary,
                audit=audit_summary,
                audit_id="old-audit",
                authoring={"candidate_count": 1, "coverage": {"covered_formats": ["html"]}},
                error={"code": "OLD_ERROR", "message": "Prior diagnostic"},
                classification_budgets={
                    "old-profile": {"reservations": [{"bundle_id": scan.bundle_id}]}
                },
                inventory_issues=[{"code": "document_limit", "url": BASE + "missing.pdf"}],
                reader_version="saved-reader",
            )
        assert store.counts("source") == {"done": 2, "review": 2}
        assert len(current_source_positions(store, "source")) == 1
        # Fail loudly if CLI or stage advancement drifts into acquisition,
        # reparsing, assessment recomputation or model dispatch.
        monkeypatch.setattr(AcquisitionWorker, "run_once", forbidden)
        for name in ("resume", "claim", "save", "fail", "queue_due", "build_bundles"):
            monkeypatch.setattr(PipelineStore, name, forbidden)
        monkeypatch.setattr(pipeline.classifier, "classify", forbidden)
        monkeypatch.setattr(pipeline.models, "complete", forbidden)
        yield store, transport, pipeline
    finally:
        engine.dispose()


def snapshot(store, *, include_pipeline=False):
    with Session(store.engine) as session:
        return {
            name: [
                dict(row)
                for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for name, table in Base.metadata.tables.items()
            if include_pipeline
            or table not in (SourcePipeline.__table__, SourceRecipeBinding.__table__)
        }


def invoke(store):
    return (
        create_app({"TESTING": True})
        .test_cli_runner()
        .invoke(
            args=[
                "source-reanalyze",
                "source",
                "--database",
                store.engine.url.database,
                "--storage",
                str(store.blobs.root),
            ]
        )
    )


def test_cli_reanalysis_preserves_acquisition_and_advances_to_fresh_authoring(saved_source):
    store, transport, pipeline = saved_source
    before = snapshot(store)
    prior = store.status("source")
    original_blobs = {path.name: path.read_bytes() for path in store.blobs.root.iterdir()}
    calls = list(transport.calls)
    with Session(store.engine) as session:
        old = session.get(SourcePipeline, "source")
        timestamps = old.started_at, old.activated_at
    result = invoke(store)
    assert result.exit_code == 0, result.output
    status = json.loads(result.output)
    assert (status["stage"], status["state"]) == ("classification", "ready")
    assert status["candidate_recipe_id"] is None
    assert status["active_recipe_id"] == "old-recipe"
    assert status["config"] == prior["config"]
    assert status["binding"] == dict(prior["binding"], candidate_revision_id=None)
    assert status["binding"]["generation"] == 4
    assert status["summary"]["candidate_generation"] == 5
    assert status["acquisition"] == prior["acquisition"]
    assert status["non_page_resources"] == 1
    summary = status["summary"]
    assert not {"audit", "audit_id", "authoring", "classification", "error"} & summary.keys()
    for key in ("classification_budgets", "inventory_issues", "reader_version"):
        assert summary[key] == prior["summary"][key]
    history = summary["reanalysis"]
    assert history["generation"] == 5
    assert history["previous_stage"] == "active" and history["previous_state"] == "ready"
    assert history["previous_active_recipe_id"] == "old-recipe"
    assert history["previous_candidate_recipe_id"] == "old-recipe"
    assert json.loads(store.blobs.read(history["previous_summary_ref"])) == prior["summary"]
    assert snapshot(store) == before
    for reference, content in original_blobs.items():
        assert store.blobs.read(reference) == content
    with Session(store.engine) as session:
        row = session.get(SourcePipeline, "source")
        assert (row.started_at, row.activated_at) == timestamps
        assert row.lease_token is row.lease_expires_at is None
    assert current_source_positions(store, "source") == []

    advanced = asyncio.run(pipeline.step("source"))
    assert (advanced["stage"], advanced["state"]) == ("authoring", "ready")
    assert advanced["candidate_recipe_id"] is None
    assert advanced["active_recipe_id"] == "old-recipe"
    assert advanced["binding"] == dict(prior["binding"], candidate_revision_id=None)
    assert advanced["binding"]["generation"] == 4
    assert advanced["summary"]["candidate_generation"] == 5
    assert current_source_positions(store, "source") == []
    assert snapshot(store) == before  # Same classifier profile reused all assessments.
    assert transport.calls == calls
    assert pipeline.models.calls == []


@pytest.mark.parametrize(
    "case, message",
    [
        ("unregistered", "Approve the registered source"),
        ("unapproved", "Approve the registered source"),
        ("no_scope", "matching approved organization root"),
        ("not_onboarded", "onboarding has not been started"),
        ("leased", "still leased"),
        ("expired_lease", "still leased"),
        ("ready_with_lease", "still leased"),
        ("running_without_token", "still leased"),
        ("page_ready", "pending acquisition"),
        ("page_running", "pending acquisition"),
        ("attachment_ready", "pending acquisition"),
        ("no_bundles", "No saved resource bundles"),
    ],
)
def test_reanalysis_preconditions_fail_without_mutating_rows_or_blobs(saved_source, case, message):
    store, transport, _ = saved_source
    source_id = "missing" if case == "unregistered" else "source"
    with Session(store.engine) as session, session.begin():
        row = session.get(SourcePipeline, "source")
        if case == "unapproved":
            session.get(Source, "source").status = RegistryStatus.PAUSED
        elif case == "no_scope":
            session.delete(session.get(OrganizationScope, "org"))
        elif case == "not_onboarded":
            session.delete(row)
        elif "lease" in case:
            row.state = "ready" if case == "ready_with_lease" else "running"
            row.lease_token = "original-owner"
            row.lease_expires_at = utc_now() + timedelta(hours=-1 if case == "expired_lease" else 1)
        elif case == "running_without_token":
            row.state = "running"
        elif case in {"page_ready", "page_running", "attachment_ready"}:
            url = BASE + ("missing.pdf" if case == "attachment_ready" else "one")
            resource = session.scalar(select(Resource).where(Resource.normalized_url == url))
            scan = session.get(ResourceScan, resource.id)
            scan.state = "running" if case == "page_running" else "ready"
            scan.available_at = utc_now() + timedelta(days=1)  # Not yet due is still pending.
            if scan.state == "running":
                scan.lease_token = "acquisition-owner"
                scan.lease_expires_at = utc_now() - timedelta(hours=1)
        elif case == "no_bundles":
            for scan in session.scalars(select(ResourceScan)):
                scan.bundle_id = None
    before = snapshot(store, include_pipeline=True)
    blobs = {path.name for path in store.blobs.root.iterdir()}
    calls = list(transport.calls)
    with pytest.raises(ValueError, match=message):
        store.reanalyze(source_id)
    assert snapshot(store, include_pipeline=True) == before
    assert {path.name for path in store.blobs.root.iterdir()} == blobs
    assert transport.calls == calls


def test_cli_reanalysis_honors_database_process_lock(saved_source):
    store, _, _ = saved_source
    before = snapshot(store, include_pipeline=True)
    with open(store.engine.url.database + ".source.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = invoke(store)
            assert result.exit_code != 0
            assert "Another source command owns this database" in result.output
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    assert snapshot(store, include_pipeline=True) == before


@pytest.mark.parametrize("without_audits", [False, True], ids=["saved-audits", "no-audits"])
def test_reanalysis_snapshots_link_history_without_nested_growth(saved_source, without_audits):
    store, _, _ = saved_source
    with Session(store.engine) as session, session.begin():
        row = session.get(SourcePipeline, "source")
        row.state, row.stage = "blocked", "audit"
        row.summary = dict(row.summary, inventory_incomplete=True)
        if without_audits:
            # Publication epoch still bounds allocation even without any audit rows.
            for model in (AuditResult, AuditRun):
                for record in session.scalars(select(model)):
                    session.delete(record)
    original = store.status("source")["summary"]
    before = snapshot(store)
    for generation in range(5, 9):
        store.reanalyze("source")
        status = store.status("source")
        assert status["binding"]["generation"] == 4
        assert status["binding"]["active_revision_id"] == "synthetic-prior-revision"
        assert status["binding"]["candidate_revision_id"] is None
        assert status["active_recipe_id"] == "old-recipe"
        assert status["summary"]["candidate_generation"] == generation
        assert current_source_positions(store, "source") == []
        assert status["summary"]["inventory_incomplete"] is True
        assert status["summary"]["reanalysis"]["generation"] == generation
        assert "audit" not in status["summary"]
    archived = status["summary"]
    for generation in range(8, 4, -1):
        metadata = archived["reanalysis"]
        assert metadata["generation"] == generation and "summary" not in metadata
        archived = json.loads(store.blobs.read(metadata["previous_summary_ref"]))
    assert archived == original
    assert snapshot(store) == before
