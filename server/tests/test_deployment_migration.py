"""One-shot migration safety: synthetic originals survive, old claims are archived."""

import asyncio
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import run_onboarding, setup_source

from youju.database import create_sqlite_engine, initialize_database
from youju.deployment_migration import rebuild_contract_database
from youju.models import Position, ResourceScan, SourcePipeline, SourceRecipeBinding


def test_migration_preserves_material_and_requires_fresh_audit(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session:
            scans = {
                row.resource_id: (row.last_version_id, row.last_document_id, row.state)
                for row in session.scalars(select(ResourceScan))
            }
        source, target = tmp_path / "source.sqlite3", tmp_path / "migrated.sqlite3"
        with sqlite3.connect(source) as db:
            before = list(db.iterdump())
        report = rebuild_contract_database(source, target)
        with sqlite3.connect(source) as db:
            assert list(db.iterdump()) == before
        migrated = create_sqlite_engine(target)
        try:
            initialize_database(migrated)
            with Session(migrated) as session:
                assert {
                    row.resource_id: (row.last_version_id, row.last_document_id, row.state)
                    for row in session.scalars(select(ResourceScan))
                } == scans
                state = session.get(SourcePipeline, "source")
                assert state.state == "blocked" and state.stage == "classification"
                assert state.summary["error"]["code"] == "RECIPE_REGENERATION_REQUIRED"
                assert isinstance(state.config, dict)  # JSON was not encoded twice.
                binding = session.get(SourceRecipeBinding, "source")
                assert binding.active_revision_id is binding.active_recipe_id is None
                assert not list(session.scalars(select(Position)))
                assert report["archived_derived"]["positions"] == 3
        finally:
            migrated.dispose()
        with pytest.raises(ValueError, match="unused output"):
            rebuild_contract_database(source, target)
        with sqlite3.connect(target) as db:
            assert db.execute("pragma foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


def test_migration_refuses_a_leased_source_and_does_not_publish_output(tmp_path):
    engine, _, _, _ = setup_source(tmp_path)
    try:
        with Session(engine) as session, session.begin():
            session.get(SourcePipeline, "source").lease_token = "still-owned"
        target = tmp_path / "new.sqlite3"
        with pytest.raises(ValueError, match="leased"):
            rebuild_contract_database(tmp_path / "source.sqlite3", target)
        assert not target.exists()
    finally:
        engine.dispose()
