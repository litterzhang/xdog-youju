"""Synthetic v3 business contracts; no services, database, network or models."""

import ast
import copy
from pathlib import Path

import pytest

from youju.positions import (
    DRAFT_SCHEMA,
    Condition,
    ExtractionDraft,
    PositionFacts,
    TaggedField,
    merge_drafts,
    normalize_extraction,
    validate_draft,
)
from youju.positions.conditions import conditions_from_texts
from youju.positions.contracts import CONTRACT
from youju.positions.deadlines import date_fact
from youju.positions.extraction import validate_output
from youju.positions.facts import POSITION_FIELDS


def tagged(value, tag="source"):
    return {"value": value, "tag": tag}


def draft(*positions, common=None, complete=True):
    return ExtractionDraft(
        common or {},
        list(positions),
        {"supported": True, "complete": complete, "needs_detailed": False},
    ).as_dict()


def position(key="row:1", title="助理", **fields):
    return {
        "key": key,
        "fields": {"title": tagged(title), **fields},
        "dates": {},
        "conditions": [],
        "condition_logic": None,
    }


def test_domain_has_no_orchestration_imports():
    root = Path(__file__).parents[1] / "src/youju/positions"
    for file in root.glob("*.py"):
        tree = ast.parse(file.read_text())
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [
            alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names
        ]
        assert not any(
            name and ("onboarding" in name or "recipe_service" in name) for name in modules
        )


def test_canonical_draft_and_normalization_are_json_native_and_pure():
    data = draft(position(headcount=tagged(0), headcount_description=tagged("若干名")))
    snapshot = copy.deepcopy(data)
    assert not validate_draft(data)
    assert DRAFT_SCHEMA["properties"]["schema_version"]["const"] == "position-draft-v1"
    result = normalize_extraction(
        data, context={"now": "2099-12-31", "validity": {"deadline_on": "2000-01-01"}}
    )
    assert data == snapshot
    assert set(result) == {"schema_version", "status", "positions", "errors", "coverage"}
    assert result["schema_version"] == CONTRACT == "position-facts-v3"
    assert result["positions"][0]["headcount"] == tagged(0)
    assert result["positions"][0]["deadline_on"] == tagged(None, "unknown")
    assert not validate_output(
        {}, {**result, "routing": {"anything": True}, "execution": "owned-by-runtime"}
    )
    assert PositionFacts.from_dict(result["positions"][0]).as_dict() == result["positions"][0]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("education", {"level": "bachelor"}),
        ("degree", {"level": "doctorate"}),
        ("study_form", {"form": "full_time"}),
        ("major", {"names": ["计算机科学"], "unrestricted": False}),
        ("work_experience", {"minimum_years": 3, "kind": "相关工作"}),
        ("age", {"minimum": None, "maximum": 35, "reference_date": "2026-10-01"}),
        ("graduation_status", {"status": "应届毕业生"}),
        ("graduation_year", {"years": [2026]}),
        ("political_status", {"statuses": ["中共党员"]}),
        ("license", {"names": ["教师资格证"]}),
        ("language", {"name": "英语", "requirement": "CET-6"}),
        ("other", {"text": "特殊条件待复核"}),
    ],
)
def test_per_condition_typed_values(kind, value):
    c = Condition("c1", kind, "equals", TaggedField(value, "source"))
    assert Condition.from_dict(c.as_dict()).fact.value == value
    with pytest.raises(ValueError):
        Condition("old", kind, "equals", TaggedField("untyped old requirement", "source"))


@pytest.mark.parametrize(
    "kind,value",
    [
        ("education", {"level": "university-ish"}),
        ("degree", {"level": "postgraduate"}),
        ("major", {"names": [], "unrestricted": False}),
        ("work_experience", {"minimum_years": True, "kind": None}),
        ("work_experience", {"minimum_years": -1, "kind": "相关"}),
        ("age", {"minimum": 40, "maximum": 30, "reference_date": None}),
        ("age", {"minimum": None, "maximum": 30, "reference_date": "2026-02-30"}),
        ("graduation_year", {"years": [True]}),
    ],
)
def test_malformed_condition_values_are_rejected(kind, value):
    with pytest.raises(ValueError):
        Condition("c", kind, "equals", TaggedField(value, "source"))


def test_degree_is_not_education_alternatives_and_exceptions_preserved():
    conditions, logic = conditions_from_texts(
        [(None, "博士学位"), (None, "本科或三年经验，优秀者可放宽")]
    )
    assert [c.type for c in conditions] == ["degree", "other"]
    assert conditions[0].fact.value == {"level": "doctorate"}
    assert conditions[1].fact.value == {"text": "本科或三年经验，优秀者可放宽"}
    assert logic["operator"] == "all"
    conditions, _ = conditions_from_texts([(None, "全日制本科及以上；具有3年以上相关工作经验")])
    assert [c.type for c in conditions] == ["study_form", "education", "work_experience"]


def test_bad_field_and_condition_do_not_discard_other_jobs_or_fields():
    good = position("row:good", headcount=tagged(2))
    bad = position("row:partial", headcount=tagged(-3), work_location=tagged("合肥"))
    bad["conditions"] = [
        {
            "id": "exp",
            "type": "work_experience",
            "operator": "at_least",
            "scope": "position",
            **tagged(3),
        }
    ]
    bad["condition_logic"] = {"condition_id": "exp"}
    result = normalize_extraction(draft(good, bad))
    assert len(result["positions"]) == 2
    assert result["positions"][0]["headcount"]["value"] == 2
    assert result["positions"][1]["headcount"]["value"] is None
    assert result["positions"][1]["work_location"]["value"] == "合肥"
    assert result["positions"][1]["conditions"][0]["tag"] == "unknown"
    assert result["coverage"]["needs_detailed"]
    assert not validate_output({}, result)


def test_validator_checks_only_business_not_bundle_equality_or_validity():
    result = normalize_extraction(draft(position()))
    hostile_bundle = {
        "status": "failed",
        "errors": ["missing all attachments"],
        "page": {"tables": [{"rows": [["岗位名称"], ["other job"], ["other job"]]}]},
    }
    assert not validate_output(hostile_bundle, result)
    result["positions"][0]["headcount"] = tagged(True)
    assert validate_output(hostile_bundle, result)[0]["code"] == "POSITION_V3_SCHEMA"


def test_common_and_local_condition_ids_are_namespaced_and_logic_preserved():
    def atom(value, scope):
        return Condition(
            "same", "degree", "equals", TaggedField({"level": value}, "source"), scope
        ).as_dict()

    common = {"conditions": [atom("master", "notice")], "condition_logic": {"condition_id": "same"}}
    job = position()
    job.update(conditions=[atom("doctorate", "position")], condition_logic={"condition_id": "same"})
    result = normalize_extraction(draft(job, common=common))
    assert [c["id"] for c in result["positions"][0]["conditions"]] == ["n:same", "p:same"]
    assert result["positions"][0]["condition_logic"]["operator"] == "all"
    assert not validate_output({}, result)


def test_merge_by_local_key_retains_duplicate_titles_and_missing_script_values():
    script = draft(position("row:1", headcount=tagged(2)), position("row:2", headcount=tagged(5)))
    detailed = draft(position("row:2", work_location=tagged("合肥")), position("row:3"))
    snapshot = copy.deepcopy(script)
    result = merge_drafts(script, detailed)
    assert script == snapshot
    assert [p["key"] for p in result["positions"]] == ["row:1", "row:2", "row:3"]
    assert result["positions"][1]["fields"]["headcount"] == tagged(5)
    assert result["positions"][1]["fields"]["work_location"] == tagged("合肥")
    assert len(normalize_extraction(result)["positions"]) == 3


def test_merge_cannot_downgrade_source_and_conflicting_source_is_unknown():
    script = draft(position(headcount=tagged(2)))
    detailed = draft(position(headcount=tagged(4, "ai_inferred")))
    merged = merge_drafts(script, detailed)
    assert merged["positions"][0]["fields"]["headcount"] == tagged(2)
    detailed["positions"][0]["fields"]["headcount"] = tagged(4)
    merged = merge_drafts(script, detailed)
    assert merged["positions"][0]["fields"]["headcount"] == tagged(None, "unknown")
    assert merged["coverage"]["needs_detailed"]


def test_missing_or_duplicate_merge_keys_rejected_without_title_matching():
    material = draft(position(), position())
    assert validate_draft(material)
    with pytest.raises(ValueError):
        merge_drafts(material, draft(position()))
    material["positions"].pop()
    material["positions"][0].pop("key")
    assert validate_draft(material)


def test_v2_and_flat_official_date_contracts_are_not_accepted():
    result = normalize_extraction(draft(position()))
    raw = result["positions"][0]
    raw["schema_version"] = "position-facts-v2"
    with pytest.raises(ValueError):
        PositionFacts.from_dict(raw)
    fields = {name: TaggedField() for name in POSITION_FIELDS}
    fields["deadline_on"] = TaggedField("2026-10-01", "source")
    with pytest.raises(ValueError, match="structured"):
        PositionFacts(fields)
    job = position(deadline_on=tagged("2026-10-01"))
    result = normalize_extraction(draft(job))
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert "UNSTRUCTURED_APPLICATION_DATE" in {e["code"] for e in result["errors"]}


def test_date_context_cannot_supply_a_year_or_a_deadline():
    job = position()
    job["dates"] = {"end": date_fact(None, 11, 30)}
    result = normalize_extraction(draft(job), context={"year": 2026, "deadline_on": "2026-11-30"})
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert result["positions"][0]["application_dates"]["value"]["end"]["month"] == 11


def test_model_refinement_cannot_silently_weaken_known_condition_logic():
    job = position()
    job["conditions"] = [
        Condition(
            "c1", "education", "at_least", TaggedField({"level": "bachelor"}, "source")
        ).as_dict(),
        Condition(
            "c2",
            "work_experience",
            "at_least",
            TaggedField({"minimum_years": 3, "kind": "相关工作"}, "source"),
        ).as_dict(),
    ]
    job["condition_logic"] = {
        "operator": "all",
        "items": [{"condition_id": "c1"}, {"condition_id": "c2"}],
    }
    original = draft(job)
    detailed = copy.deepcopy(original)
    detailed["positions"][0]["condition_logic"]["operator"] = "any"
    merged = merge_drafts(original, detailed)
    assert merged["positions"][0]["condition_logic"] is None
    assert len(merged["positions"][0]["conditions"]) == 2
    assert merged["coverage"]["needs_detailed"]
    assert not validate_draft(merged)


@pytest.mark.parametrize("broken", [0, -1, 10000, True])
def test_invalid_structured_years_cannot_be_source_dates(broken):
    job = position()
    fact = date_fact(2026, 11, 14)
    fact["year"] = broken
    job["dates"] = {"end": fact}
    result = normalize_extraction(draft(job))
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert any(e["code"] == "INVALID_APPLICATION_DATE" for e in result["errors"])
