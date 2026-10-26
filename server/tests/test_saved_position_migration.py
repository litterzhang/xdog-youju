"""One-shot copied legacy conversion, with no provider or production writes."""

import json
import sqlite3
from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_jobs_api import saved as saved

from youju.database import create_sqlite_engine
from youju.jobs import public_jobs
from youju.models import AuditResult, Position
from youju.saved_position_migration import convert_v2, migrate_saved_positions


def legacy(title="助理"):
    return {
        "schema_version": "position-facts-v2",
        "title": {"value": title, "tag": "source"},
        "deadline_on": {"value": "2026-10-31", "tag": "source"},
        "employment_type": {"value": "public_institution_staff", "tag": "source"},
        "conditions": [
            {
                "id": "c1",
                "type": "degree",
                "operator": "unspecified",
                "scope": "position",
                "value": "博士学位",
                "tag": "ai_inferred",
            },
            {
                "id": "c2",
                "type": "age",
                "operator": "unspecified",
                "scope": "notice",
                "value": "一般45岁以下但优秀者可放宽",
                "tag": "source",
            },
        ],
        "condition_logic": {
            "operator": "any",
            "items": [{"condition_id": "c1"}, {"condition_id": "c2"}],
        },
    }


def test_legacy_dates_never_promote_and_ambiguous_atoms_preserve_logic_and_tags():
    raw = legacy()
    frozen = deepcopy(raw)
    facts = convert_v2(raw)
    assert raw == frozen
    assert facts.fields["application_dates"].value is None
    assert facts.fields["deadline_on"].value is None
    assert facts.fields["employment_type"].value is None
    assert facts.fields["staffing_status"].value == "established"
    assert facts.conditions[0].fact.value == {"level": "doctorate"}
    assert facts.conditions[0].fact.tag == "ai_inferred"
    assert facts.conditions[1].type == "other"
    assert facts.conditions[1].fact.value == {"text": raw["conditions"][1]["value"]}
    assert facts.condition_logic == raw["condition_logic"]


def test_copied_migration_preserves_originals_and_exposes_only_review_candidates(saved, tmp_path):
    source, destination = saved.engine.url.database, tmp_path / "new.sqlite3"
    with sqlite3.connect(source) as db:
        for (identity,) in db.execute("SELECT id FROM positions").fetchall():
            db.execute(
                "UPDATE positions SET facts=? WHERE id=?", (json.dumps(legacy(identity)), identity)
            )
    before = sha256(Path(source).read_bytes()).hexdigest()
    report = migrate_saved_positions(source, destination)
    assert report["original_positions"] == report["appended_positions"] == 7
    assert sha256(Path(source).read_bytes()).hexdigest() == before
    engine = create_sqlite_engine(destination)
    try:
        with Session(engine) as session:
            rows = list(public_jobs(session, day=date(2026, 10, 26)))
            assert len(rows) == 7
            assert all(row["extraction_path"] == "migration" for row in rows)
            assert all({"migrated", "needs_review"} <= set(row["display_tags"]) for row in rows)
            assert all(row["validity"]["status"] == "unknown" for row in rows)
            assert all(row["admission_audit_id"] is None for row in rows)
            assert len(list(session.scalars(select(Position)))) == 14
            assert (
                session.get(Position, saved.ids["future"]).facts["schema_version"]
                == "position-facts-v2"
            )
            assert len(list(session.scalars(select(AuditResult)))) == 14
        with pytest.raises(FileExistsError):
            migrate_saved_positions(source, destination)
        assert destination.is_file()
    finally:
        engine.dispose()


def test_failed_conversion_never_publishes_a_partial_output(saved, tmp_path):
    source, destination = saved.engine.url.database, tmp_path / "bad.sqlite3"
    with sqlite3.connect(source) as db:
        for (identity,) in db.execute("SELECT id FROM positions").fetchall():
            raw = legacy()
            raw["employment_type"] = {"value": "guaranteed", "tag": "source"}
            db.execute("UPDATE positions SET facts=? WHERE id=?", (json.dumps(raw), identity))
    with pytest.raises(ValueError, match="employment"):
        migrate_saved_positions(source, destination)
    assert not destination.exists()
