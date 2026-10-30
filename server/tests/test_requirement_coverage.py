"""Requirement coverage is scoped diagnostics, not fact inference or gold."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_runtime_field_completeness import atom, execute, script_draft

from youju.positions.completeness import completion_issues, needs_semantic_completion
from youju.positions.drafts import validate_model_draft
from youju.positions.normalization import normalize_extraction


def draft_for(text):
    draft = script_draft()
    draft["positions"][0]["fields"]["requirements_text"] = {"value": text, "tag": "source"}
    draft["positions"][0]["conditions"] = [
        atom("general", "other", {"text": "身体健康"}, "unspecified")
    ]
    draft["positions"][0]["condition_logic"] = {"condition_id": "general"}
    return draft


def gaps(draft):
    return [
        i
        for i in completion_issues(draft)
        if i.get("source_path", "").endswith("requirements_text")
    ]


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/requirement_omissions.json").read_text())[
        "cases"
    ],
)
def test_traceable_real_omissions_survive_unrelated_common_other(case):
    draft = draft_for(case["requirements_text"])
    draft["notice_facts"] = draft["positions"][0].copy()
    draft["notice_facts"].pop("key")
    draft["notice_facts"]["fields"] = {}
    draft["positions"][0]["conditions"] = []
    draft["positions"][0]["condition_logic"] = None
    assert validate_model_draft(draft, deepcopy(draft)) == []  # Schema alone is not coverage.
    issues = gaps(draft)
    assert {t for i in issues for t in i["candidate_types"]} >= {
        "education",
        "major",
        "work_experience",
    }
    assert all(i["position_key"] and i["excerpt"] and i["reason"] for i in issues)
    assert needs_semantic_completion(draft)
    assert any(i.get("candidate_types") for i in normalize_extraction(draft)["errors"])


def test_existing_education_cannot_mask_missing_experience_or_wrong_education():
    draft = draft_for("硕士研究生及以上学历；具有2年及以上相关工作经验")
    p = draft["positions"][0]
    p["conditions"] = [atom("edu", "education", {"level": "bachelor"}, "at_least")]
    p["condition_logic"] = {"condition_id": "edu"}
    assert {t for i in gaps(draft) for t in i["candidate_types"]} == {
        "education",
        "work_experience",
    }
    p["conditions"][0]["value"]["level"] = "master"
    assert {t for i in gaps(draft) for t in i["candidate_types"]} == {"work_experience"}
    p["conditions"].append(
        atom("exp", "work_experience", {"minimum_years": 2, "kind": "相关工作"}, "at_least")
    )
    p["condition_logic"] = {
        "operator": "all",
        "items": [{"condition_id": c["id"]} for c in p["conditions"]],
    }
    assert not completion_issues(draft)
    result, calls = execute(draft, draft)
    assert not calls and not result["errors"]


def test_scope_preferences_teaching_and_unknowns_are_not_invented_requirements():
    for text in ["硕士优先", "有指导博士研究生的能力，承担本科及研究生教学", "身体健康", ""]:
        assert not gaps(draft_for(text))
    draft = draft_for("本科及以上学历；具有3年工作经验")
    second = deepcopy(draft["positions"][0])
    second["key"] = "second"
    second["fields"]["requirements_text"]["value"] = ""
    second["conditions"] = [
        atom("exp", "work_experience", {"minimum_years": 3, "kind": "工作"}, "at_least")
    ]
    second["condition_logic"] = {"condition_id": "exp"}
    draft["positions"].append(second)
    assert any("work_experience" in i["candidate_types"] for i in gaps(draft))
    assert all(i["position_key"] != "second" for i in gaps(draft))


def test_qualified_missing_requirement_requests_interpretation_not_hard_fact():
    draft = draft_for("硕士研究生学历或具有3年工作经验，优秀者可放宽")
    before = deepcopy(draft)
    assert needs_semantic_completion(draft)
    assert all(i["code"] == "UNRESOLVED_CONDITION" for i in gaps(draft))
    assert draft == before


def test_coverage_gap_reaches_flow_model_input_and_postmerge_check():
    draft = draft_for("硕士研究生及以上学历；2年及以上相关工作经验")
    result, calls = execute(draft, draft)
    assert len(calls) == 1 and calls[0]["completion_issues"]
    assert any(i.get("candidate_types") for i in result["errors"])


def test_alternative_requires_logic_not_just_both_typed_atoms():
    draft = draft_for("硕士研究生学历或具有3年工作经验，满足其一")
    p = draft["positions"][0]
    p["conditions"] = [
        atom("edu", "education", {"level": "master"}),
        atom("exp", "work_experience", {"minimum_years": 3, "kind": "工作"}, "at_least"),
    ]
    p["condition_logic"] = {
        "operator": "all",
        "items": [{"condition_id": c["id"]} for c in p["conditions"]],
    }
    assert gaps(draft) and all(i["code"] == "UNRESOLVED_CONDITION" for i in gaps(draft))
    p["condition_logic"]["operator"] = "any"
    assert not gaps(draft)


def test_one_major_cannot_mask_an_explicit_list_of_other_majors():
    draft = draft_for("计算机、数学、物理相关专业")
    p = draft["positions"][0]
    p["conditions"] = [atom("maj", "major", {"names": ["计算机"], "unrestricted": False}, "in")]
    p["condition_logic"] = {"condition_id": "maj"}
    assert any("major" in i["candidate_types"] for i in gaps(draft))
    p["conditions"][0]["value"]["names"] = ["计算机", "数学", "物理"]
    assert not gaps(draft)


def test_preferred_clause_does_not_hide_separate_mandatory_requirement():
    draft = draft_for("本科及以上学历，硕士优先")
    issues = gaps(draft)
    assert len(issues) == 1 and issues[0]["code"] == "FIELD_COMPLETION_REQUIRED"
    assert issues[0]["excerpt"] == "本科及以上学历"
    assert (
        draft["positions"][0]["fields"]["requirements_text"]["value"][
            issues[0]["start"] : issues[0]["end"]
        ]
        == issues[0]["excerpt"]
    )


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/requirement_omissions.json").read_text())[
        "cases"
    ],
)
def test_real_case_completion_branch_merges_structured_requirements_offline(case):
    original = draft_for(case["requirements_text"])
    expected = case["expected_requirements"]
    refined = deepcopy(original)
    p = refined["positions"][0]
    p["conditions"] = [
        atom("edu", "education", {"level": expected["education"]}, "at_least"),
        atom("major", "major", {"names": expected["majors"], "unrestricted": False}, "in"),
        atom(
            "experience",
            "work_experience",
            {"minimum_years": expected["minimum_years"], "kind": expected["experience_kind"]},
            "at_least",
        ),
        p["conditions"][0],
    ]
    p["condition_logic"] = {
        "operator": "all",
        "items": [{"condition_id": c["id"]} for c in p["conditions"]],
    }
    result, calls = execute(original, refined)
    assert len(calls) == 1 and calls[0]["completion_issues"]
    assert not result["errors"]
    assert {c["type"] for c in result["positions"][0]["conditions"]} == {
        "education",
        "major",
        "work_experience",
        "other",
    }
    assert not gaps(result["execution"]["stages"]["merged_draft"]["value"])
