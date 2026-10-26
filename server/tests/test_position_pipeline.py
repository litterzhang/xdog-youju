"""Real official Flow -> draft normalization -> audit/storage/query, without external IO."""

import asyncio
from copy import deepcopy
from datetime import date, datetime
from threading import RLock
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_jobs_api import DAY, DraftSandbox, runtime_result
from test_jobs_api import saved as saved
from test_onboarding import NeedsDetailedSandbox
from test_sourcekit import bundle

from youju.database import create_sqlite_engine, initialize_database
from youju.feedback import submit_feedback
from youju.jobs import job_validity
from youju.models import AuditResult, AuditRun, Position, SourceRecipeBinding
from youju.onboarding.auditing import AUDIT_POLICY, AuditLedger
from youju.onboarding.recipes import RecipeError, RecipeRunner, template_candidate
from youju.onboarding.xdog import XdogModels
from youju.positions.deadlines import date_fact
from youju.positions.drafts import DRAFT_SCHEMA, ExtractionDraft
from youju.positions.extraction import validate_output
from youju.positions.facts import Condition, PositionFacts, TaggedField
from youju.positions.normalization import normalize_extraction

NOW = "2026-10-14T10:00:00+08:00"


def full_draft(title="unknown 助理"):
    fields = {
        "title": TaggedField(title, "source"),
        "hiring_unit": TaggedField("示例院系", "source"),
        "employer": TaggedField("推断合同单位", "ai_inferred"),
        "headcount": TaggedField(0, "source"),
        "employment_type": TaggedField("personnel_agency", "ai_inferred"),
        "staffing_status": TaggedField("non_established", "source"),
        "work_location": TaggedField("合肥", "ai_inferred"),
        "requirements_text": TaggedField("博士学位或三年相关工作经验", "source"),
    }
    conditions = (
        Condition("degree", "degree", "equals", TaggedField({"level": "doctorate"}, "source")),
        Condition(
            "experience",
            "work_experience",
            "at_least",
            TaggedField({"minimum_years": 3, "kind": "相关工作"}, "source"),
        ),
    )
    return ExtractionDraft(
        positions=[
            {
                "key": "d0:t0:r1",
                "fields": {k: v.as_dict() for k, v in fields.items()},
                "dates": {"end": date_fact(2026, 10, 1, inferred=True)},
                "conditions": [item.as_dict() for item in conditions],
                "condition_logic": {
                    "operator": "any",
                    "items": [{"condition_id": item.id} for item in conditions],
                },
            }
        ],
        coverage={"supported": True, "complete": True, "needs_detailed": False},
    ).as_dict()


def test_new_output_sdk_audit_sqlite_public_read_and_feedback_context(saved):
    material = bundle(
        "unknown",
        texts=["博士学位或三年相关工作经验"],
        rows=[["岗位名称", "人数"], ["unknown 助理", "0"]],
    )
    material["page"]["version_id"] = "version-unknown"
    draft = full_draft()
    facts = normalize_extraction(draft)["positions"][0]
    calls = []

    def model_stub(operation, payload, schema, tier):
        calls.append(operation)
        assert "ai_inferred" in payload["instructions"]
        assert schema == DRAFT_SCHEMA
        assert payload["previous_draft"]["coverage"]["supported"] is False
        return deepcopy(draft)

    result = asyncio.run(
        RecipeRunner(NeedsDetailedSandbox(), XdogModels(stub=model_stub)).run(
            template_candidate(), material, now_iso=NOW
        )
    )
    assert calls == ["extract_position_draft"]
    assert result["positions"] == [facts] and "evidence" not in facts
    assert "validity" not in result and "quality_tier" not in result
    assert result["execution"]["path"] == "detailed" and result["execution"]["model_used"]
    assert not validate_output(material, result)
    ledger = AuditLedger(SimpleNamespace(engine=saved.engine, lock=RLock()))
    outcome = ledger.record(material, "recipe", DAY.isoformat(), result, generation=1)
    with Session(saved.engine) as session:
        audit = session.get(AuditResult, outcome["id"])
        position = session.scalar(select(Position).where(Position.parse_id == audit.parse_id))
        assert position.facts == facts
        assert position.facts["headcount"]["value"] == 0
        assert position.facts["deadline_on"]["tag"] == "ai_inferred"
        assert position.deadline_on is None  # No inferred candidate enters exact SQL Date facets.
        assert position.facts["staffing_status"]["value"] == "non_established"

        current_position_id = position.id
    response = saved.client.get("/api/jobs?employment=personnel_agency&education=unknown")
    assert response.status_code == 200
    row = next(item for item in response.json["items"] if item["id"] == current_position_id)
    assert row["facts"] == facts
    assert "education_text" not in row and "employment_arrangement" not in row
    assert row["validity"]["status"] == "unknown"
    # A doctoral degree is not a declared doctoral education requirement.
    assert (
        saved.client.get("/api/jobs?employment=personnel_agency&education=doctorate").json["total"]
        == 0
    )
    with Session(saved.engine) as session, session.begin():
        feedback, created = submit_feedback(
            session,
            row["id"],
            "synthetic-user",
            "conditions",
            "Please review alternatives",
            "",
            DAY,
        )
        assert created and feedback.context["field_value"] == facts["conditions"]
        assert feedback.context["condition_logic"] == facts["condition_logic"]


def test_cost_route_changes_neither_job_facts_nor_official_job_expiry():
    def never_call(*args):
        raise AssertionError("Supported draft must not call a model on either route")

    expired = bundle(texts=["报名截止2026年10月01日。"], rows=[["岗位名称", "人数"], ["助理", "2"]])
    runner = RecipeRunner(DraftSandbox(), XdogModels(stub=never_call))
    quick = asyncio.run(runner.run(template_candidate(), expired, now_iso=NOW))
    detailed_route = {**quick["routing"], "category": "unknown", "mode": "detailed"}
    detailed = asyncio.run(
        runner.run(template_candidate(), expired, now_iso=NOW, routing=detailed_route)
    )
    assert quick["execution"]["path"] == "quick"
    assert detailed["execution"]["path"] == "detailed"
    assert quick["positions"] == detailed["positions"]
    assert not quick["execution"]["model_used"] and not detailed["execution"]["model_used"]
    assert job_validity(PositionFacts.from_dict(quick["positions"][0]), DAY)["status"] == "expired"
    assert "validity" not in quick and "quality_tier" not in detailed
    old = {"status": "success", "positions": [{"title": "助理", "evidence": {}}], "errors": []}
    assert validate_output(expired, old)
    invalid_model = RecipeRunner(NeedsDetailedSandbox(), XdogModels(stub=lambda *args: old))
    with pytest.raises(RecipeError):
        asyncio.run(
            invalid_model.run(
                template_candidate(), bundle(rows=[["岗位名称"], ["助理"]]), now_iso=NOW
            )
        )
    candidate = template_candidate()
    candidate["config"]["position_contract"] = "position-facts-v2"
    assert runner.validate(candidate)[0]["code"] == "RECIPE_INVALID"
    old_candidate = {
        "workflow": candidate["workflow"],
        "validity_py": "old",
        "fast_parse_py": "old",
        "config": candidate["config"],
    }
    assert runner.validate(old_candidate)


def test_new_schema_rejects_old_database_and_runtime_merge_preserves_duplicate_rows():
    engine = create_sqlite_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE positions (id TEXT PRIMARY KEY, title TEXT)"))
        connection.execute(text("INSERT INTO positions VALUES ('kept', 'untouched')"))
    with pytest.raises(ValueError, match="fresh database"):
        initialize_database(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM positions")).scalar() == "untouched"
    engine.dispose()
    material = bundle(rows=[["岗位名称", "人数"], ["助理", "1"], ["助理", "1"]])

    class NeedsRefinement(DraftSandbox):
        async def run_script(self, *args):
            draft = await super().run_script(*args)
            draft["coverage"]["needs_detailed"] = True
            return draft

    calls = []

    def refine(task, payload, schema, tier):
        calls.append(task)
        draft = deepcopy(payload["previous_draft"])
        assert len(draft["positions"]) == 2
        draft["positions"].pop()  # Deliberate omission; the shared key-based merger must retain it.
        draft["positions"][0]["fields"]["work_location"] = TaggedField("合肥", "source").as_dict()
        draft["coverage"]["needs_detailed"] = False
        return draft

    result = asyncio.run(
        RecipeRunner(NeedsRefinement(), XdogModels(stub=refine)).run(
            template_candidate(), material, now_iso=NOW
        )
    )
    assert calls == ["extract_position_draft"]
    assert len(result["positions"]) == 2
    assert result["positions"][0]["work_location"]["value"] == "合肥"
    assert result["positions"][1]["work_location"]["value"] is None
    assert not validate_output(material, result)
    changed = deepcopy(result)
    changed["positions"][0]["deadline_on"] = {"value": "2026-02-30", "tag": "source"}
    assert "POSITION_V3_SCHEMA" in {error["code"] for error in validate_output(material, changed)}
    # Domain validation does not compare against the same extraction helper as an oracle.
    subset = deepcopy(result)
    subset["positions"].pop()
    assert not validate_output(material, subset)
    assert date.fromisoformat("2026-10-14") == DAY


def test_complete_material_with_unresolved_condition_preserves_known_job_by_default(saved):
    material = bundle(
        "future",
        texts=["报名截止2026年10月16日。"],
        rows=[
            ["岗位名称", "人数", "学历"],
            ["future 助理", "2", "硕士研究生（优秀者可放宽）"],
        ],
    )
    material["page"]["version_id"] = "version-future"
    result = runtime_result(material, now_iso=NOW, mode="quick")
    assert result["execution"]["path"] == "quick"
    assert result["coverage"]["needs_detailed"] and not result["coverage"]["complete"]
    assert any(issue["code"] == "UNRESOLVED_CONDITION" for issue in result["errors"])
    ledger = AuditLedger(SimpleNamespace(engine=saved.engine, lock=RLock()))
    outcome = ledger.record(material, "recipe", DAY.isoformat(), result, generation=1)
    assert outcome["status"] == "partial" and not outcome["errors"]
    with Session(saved.engine) as session, session.begin():
        audit = session.get(AuditResult, outcome["id"])
        position = session.scalar(select(Position).where(Position.parse_id == audit.parse_id))
        assert position.facts["headcount"]["value"] == 2
        assert position.deadline_on == date(2026, 10, 16)
        assert position.facts["conditions"][0]["type"] == "other"
        assert "优秀者可放宽" in position.facts["requirements_text"]["value"]
        new_id = position.id
        session.get(SourceRecipeBinding, "source").generation = 1
        session.add(
            AuditRun(
                id="field-admitted",
                source_id="source",
                recipe_id="recipe",
                kind="incremental",
                status="complete",
                finished_at=datetime.fromisoformat(NOW),
                summary={
                    "passed": True,
                    "audit_policy": AUDIT_POLICY,
                    "evaluated_on": DAY.isoformat(),
                    "generation": 1,
                    "remaining": 0,
                    "results": [outcome],
                    "admission": {"policy": "quarantine-v1", "blocking_reasons": []},
                },
            )
        )
    # include_partial is an acquisition opt-in, not a blanket field-completeness veto.
    response = saved.client.get("/api/jobs/" + new_id)
    assert response.status_code == 200
    row = response.json
    assert row["material_status"] == "complete" and row["review_required"]
    assert row["field_issues"] and row["facts"]["headcount"]["value"] == 2
    assert row["validity"]["status"] == "not_expired"
    assert saved.client.get("/api/jobs?q=future").json["total"] == 1
