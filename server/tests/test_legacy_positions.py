"""Legacy cleanup never deletes genuine v3 work, material, or user feedback."""

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_jobs_api import saved as saved

from youju.legacy_positions import purge_legacy_positions
from youju.models import AuditResult, ParsingFeedback, Position, ResourceVersion
from youju.onboarding.author_prompt import author_system_prompt


def make_legacy(session, position_id):
    position = session.get(Position, position_id)
    raw = dict(position.facts, schema_version="position-facts-v2")
    session.execute(
        text("UPDATE positions SET facts=:facts WHERE id=:id"),
        {"facts": json.dumps(raw), "id": position_id},
    )


def test_purge_removes_v2_and_migrated_rows_but_keeps_new_extraction_and_material(saved):
    with Session(saved.engine) as session, session.begin():
        make_legacy(session, saved.ids["future"])
        position = session.get(Position, saved.ids["partial"])
        audit = session.scalar(select(AuditResult).where(AuditResult.parse_id == position.parse_id))
        audit.result = {
            **audit.result,
            "execution": {**audit.result["execution"], "path": "migration"},
        }
    with Session(saved.engine) as session, session.begin():
        before = {row.id: row.body_ref for row in session.scalars(select(ResourceVersion))}
        report = purge_legacy_positions(session)
        assert report == {"before": 7, "deleted": 2, "remaining": 5}
        assert {row.id: row.body_ref for row in session.scalars(select(ResourceVersion))} == before
        assert session.get(Position, saved.ids["today"]) is not None
        assert purge_legacy_positions(session)["deleted"] == 0


def test_legacy_feedback_prevents_implicit_report_deletion(saved):
    with Session(saved.engine) as session, session.begin():
        make_legacy(session, saved.ids["future"])
        session.add(
            ParsingFeedback(
                position_id=saved.ids["future"],
                source_id="source",
                reporter_sub="synthetic",
                payload_digest="synthetic",
                field="other",
                message="Keep this user report",
                context={},
            )
        )
    with Session(saved.engine) as session, pytest.raises(ValueError, match="feedback"):
        purge_legacy_positions(session)
    with Session(saved.engine) as session:
        assert session.get(Position, saved.ids["future"]) is not None
        assert len(list(session.scalars(select(ParsingFeedback)))) == 1


def test_authoring_requires_scoped_employment_fields_without_inventing_entitlement():
    prompt = author_system_prompt("skill", "contract")
    assert "employment_type and staffing_status independently" in prompt
    assert "at least one available employment-bearing" in prompt
    assert "does not imply staffing entitlement" in prompt
