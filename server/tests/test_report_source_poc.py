"""Two grouped diagnostic regressions; synthetic source/models only."""

import asyncio
import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import BASE, run_onboarding, setup_source

from youju.models import (
    AuditResult,
    AuditRun,
    Resource,
    ResourceScan,
    SourcePipeline,
    SourceRecipeBinding,
)
from youju.onboarding.auditing import current_source_positions, evaluation_day

SPEC = importlib.util.spec_from_file_location(
    "report_source_poc", Path(__file__).parents[1] / "scripts" / "report-source-poc.py"
)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def dump(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return list(connection.iterdump())


def test_readonly_content_and_real_current_position_gates(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)
    database = tmp_path / "source.sqlite3"
    try:
        before = dump(database)
        pending = REPORT.snapshot_report(database, store.blobs.root, "source")
        assert dump(database) == before
        assert pending["registry_status"] == "active"  # Registry approval is NOT activation.
        assert pending["pipeline"]["stage"] == "inventory"
        assert pending["positions"]["current"] == 0
        assert pending["active_recipe"] is None
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        with Session(engine) as session, session.begin():
            binding = session.get(SourceRecipeBinding, "source")
            active_generation = binding.generation
            state = session.get(SourcePipeline, "source")
            # Neither legacy config nor a pending candidate epoch is publication authority.
            state.config = dict(state.config, audit_generation=99)
            state.summary = dict(state.summary, candidate_generation=100)
        before = dump(database)
        report = REPORT.snapshot_report(database, store.blobs.root, "source")
        assert dump(database) == before
        assert report["phase"] == "ongoing"
        assert report["pipeline"]["generation"] == active_generation
        assert report["audits"]["scope"]["generation"] == active_generation
        assert report["pipeline"]["candidate_generation"] == 100
        assert "audit_generation" not in report["pipeline"]["config"]
        assert report["positions"]["current"] == len(current_source_positions(store, "source")) == 2
        assert report["positions"]["current_including_expired"] == 3
        assert report["positions"]["expired_current"] == 1
        assert report["positions"]["total_stored_history"] == 3
        assert report["positions"]["accepted_stored_rows_in_audit_scope"] == 3
        assert report["active_recipe"]["entry_is_parse_subflow"]
        assert report["active_recipe"]["native_subflow_count"] == 3
        assert all(report["active_recipe"]["parser_scripts_present"].values())
        assert report["acquisition"]["fetch_history"]["mode_outcomes"]["unrecorded:downloaded"] == 3
        assert report["boundaries"]["checks"]["registered"]["outside_root_or_invalid"] == 0
        with Session(engine) as session, session.begin():
            attachment = session.scalar(
                select(Resource).where(Resource.normalized_url == BASE + "positions.xlsx")
            )
            session.get(ResourceScan, attachment.id).state = "ready"
        changed = REPORT.snapshot_report(database, store.blobs.root, "source")
        assert changed["positions"]["current"] == 1  # Pending dependency hides only current rows.
        assert changed["positions"]["total_stored_history"] == 3
    finally:
        engine.dispose()


def test_profile_budget_history_review_and_latest_actual_onboarding(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)
    database = tmp_path / "source.sqlite3"
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        with Session(engine) as session, session.begin():
            state = session.get(SourcePipeline, "source")
            binding = session.get(SourceRecipeBinding, "source")
            recipe_id, key = (
                binding.active_recipe_id,
                state.summary["classification"]["classifier_key"],
            )
            state.stage = "audit"
            binding.candidate_recipe_id = recipe_id
            binding.active_recipe_id = None
            # Candidate scope is deliberately distinct from active publication/config.
            binding.generation = 4
            state.config = dict(state.config, audit_generation=99, api_key="PRIVATE_FIXTURE")
            state.summary = dict(
                state.summary,
                candidate_generation=5,
                classification=dict(state.summary["classification"], llm_fraction=0.25),
                classification_budgets={
                    "old-profile": {"reservations": [{"tier": "small"}]},
                    key: {"reservations": []},
                },
            )
            old = session.scalar(select(AuditResult))
            session.add(
                AuditResult(
                    bundle_id=old.bundle_id,
                    recipe_id=recipe_id,
                    evaluated_on=evaluation_day(),
                    generation=5,
                    status="review",
                    result={"positions": [{}, {}]},
                    errors=[{"code": "UNKNOWN_EVIDENCE", "message": "PRIVATE_FIXTURE"}],
                )
            )
            session.add(
                AuditRun(
                    source_id="source", recipe_id=recipe_id, kind="incremental", status="running"
                )
            )
        before = dump(database)
        report = REPORT.snapshot_report(database, store.blobs.root, "source", phase="final")
        assert dump(database) == before and "PRIVATE_FIXTURE" not in json.dumps(report)
        assert report["phase"] == "final" and report["pipeline"]["stage"] == "audit"
        assert report["pipeline"]["generation"] == 4
        assert report["pipeline"]["candidate_generation"] == 5
        assert "audit_generation" not in report["pipeline"]["config"]
        assert report["audits"]["scope"]["generation"] == 5
        assert report["audits"]["scope"]["recipe_id"] == recipe_id
        assert (
            report["positions"]["current"] == 0
        )  # Neither old generation nor registry is current.
        assert report["positions"]["total_stored_history"] == 3
        assert report["positions"]["accepted_stored_rows_in_audit_scope"] == 0
        assert report["positions"]["review_candidate_rows_in_audit_scope"] == 2
        assert report["audits"]["latest_onboarding"]["status"] == "complete"
        assert report["audits"]["missing"] == 2
        budget = report["classification"]
        assert budget["llm_reserved_calls"] == 1 and budget["llm_profile_reserved_calls"] == 0
        assert budget["denominator"] == 4 and budget["limit"] == 1 and budget["within_limit"]
    finally:
        engine.dispose()
