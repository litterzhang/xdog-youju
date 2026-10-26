"""Shared runtime regressions; synthetic materials, native Flow, no paid models."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from test_recipe_runtime_v3 import material, route

from youju.extraction_service import ConfiguredExtractionModel
from youju.onboarding.auditing import audit_dimensions, position_field_issues
from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.positions.completeness import completion_issues, needs_semantic_completion
from youju.positions.conditions import conditions_from_texts
from youju.positions.drafts import DRAFT_INSTRUCTIONS, merge_drafts, validate_model_draft
from youju.positions.employment import employment_from_text
from youju.positions.facts import Condition, TaggedField
from youju.positions.normalization import normalize_extraction
from youju.positions.parsing import extract_position_draft
from youju.recipe_runtime.contracts import template_candidate
from youju.recipe_runtime.diagnostics import STAGE_BYTES, stage_receipts, valid_stage_receipts
from youju.recipe_runtime.runner import RecipeRunner
from youju.recipe_service.native_runtime import SYSTEM_PROMPT


def atom(identity, kind, value, operator="equals"):
    return Condition(identity, kind, operator, TaggedField(value, "source")).as_dict()


def script_draft(*texts):
    draft = extract_position_draft(material())
    p = draft["positions"][0]
    p["conditions"] = [atom("edu", "education", {"level": "bachelor"}, "at_least")]
    if texts:
        p["conditions"].append(atom("raw", "other", {"text": "\n".join(texts)}, "unspecified"))
    p["condition_logic"] = {
        "operator": "all",
        "items": [{"condition_id": c["id"]} for c in p["conditions"]],
    }
    draft["issues"] = []
    draft["coverage"].update(complete=True, needs_detailed=False)
    return draft


class Script:
    def __init__(self, draft):
        self.draft = draft

    async def run_script(self, *args):
        return deepcopy(self.draft)


def execute(draft, refined, *, bundle=None, candidate=None, mode="detailed"):
    calls = []

    def complete(task, payload, schema, tier):
        calls.append(deepcopy(payload))
        return deepcopy(refined)

    models = ConfiguredExtractionModel(XdogModels(XdogConfig(), stub=complete))
    result = asyncio.run(
        RecipeRunner(Script(draft), models).run(
            candidate or template_candidate(), bundle or material(), routing=route(mode)
        )
    )
    return result, calls


def test_runtime_inspection_prevents_supported_row_from_bypassing_completion():
    original = script_draft("年龄不超过40周岁")
    original["positions"][0]["fields"]["title"]["value"] = "研究助理（1人）"
    del original["positions"][0]["fields"]["headcount"]
    refined = deepcopy(original)
    p = refined["positions"][0]
    p["fields"]["headcount"] = {"value": 1, "tag": "source"}
    p["conditions"][-1] = atom(
        "age", "age", {"minimum": None, "maximum": 40, "reference_date": None}, "at_most"
    )
    p["condition_logic"]["items"][-1] = {"condition_id": "age"}
    result, calls = execute(original, refined)
    assert len(calls) == 1 and calls[0]["completion_issues"]
    assert result["execution"]["model_used"]
    assert result["positions"][0]["headcount"] == {"value": 1, "tag": "source"}
    assert {c["type"] for c in result["positions"][0]["conditions"]} == {"education", "age"}
    assert not result["errors"]
    stages = result["execution"]["stages"]
    assert valid_stage_receipts(stages)
    assert stages["script_draft"]["value"]["positions"][0]["fields"].get("headcount") is None
    assert stages["detailed_draft"]["value"]["positions"][0]["fields"]["headcount"]["value"] == 1
    assert original["coverage"]["needs_detailed"] is False  # No input/artifact rewriting.


def test_model_field_hints_are_attention_not_scope_or_source_authority():
    original = script_draft()
    bundle = material()
    bundle["page"]["blocks"].append({"text": "工作地点：合肥", "id": "location"})
    refined = deepcopy(original)
    refined["positions"][0]["fields"]["work_location"] = {"value": "合肥", "tag": "source"}
    result, calls = execute(original, refined, bundle=bundle)
    assert len(calls) == 1
    assert calls[0]["material_field_hints"][0]["scope"] == "unresolved"
    assert result["positions"][0]["work_location"]["value"] == "合肥"
    # Contact-only addresses and missing annexes alone do not trigger a model.
    bundle["page"]["blocks"][-1]["text"] = "联系地址：合肥"
    bundle["status"] = "partial"
    result, calls = execute(original, refined, bundle=bundle)
    assert calls == [] and result["positions"][0]["work_location"]["value"] is None


def test_bypassing_inspection_does_not_self_certify_a_structural_gap():
    original = script_draft("年龄不超过40周岁")
    candidate = template_candidate()
    detail = candidate["workflow"]["nodes"][1]["subflow"]
    for edge in detail["edges"]:
        if edge["from"] == "inspect":
            edge["when"] = {
                "equals": {"value": "yes", "text": "yes" if edge["to"] == "$output" else "no"}
            }
    result, calls = execute(original, original, candidate=candidate)
    assert calls == [] and result["status"] == "partial"
    assert any(e["code"] == "CONDITION_STRUCTURE_GAP" for e in result["errors"])
    result, calls = execute(original, original, mode="quick")
    assert calls == [] and result["positions"]  # Cost routing never gates saved jobs.


def test_legitimate_residual_and_preferences_do_not_make_whole_job_uncertain():
    for text in ("遵纪守法，身体健康，须具备良好的沟通能力", "具有高校工作经验者优先"):
        original = script_draft(text)
        assert not completion_issues(original)
        result = normalize_extraction(original)
        assert not result["errors"]
    assert needs_semantic_completion(script_draft("具有高校工作经验者优先"))
    assert not needs_semantic_completion(script_draft("遵纪守法，身体健康"))
    conditional = normalize_extraction(script_draft("硕士研究生（优秀者可放宽）"))
    assert any(e["code"] == "UNRESOLVED_CONDITION" for e in conditional["errors"])
    from youju.recipe_service.authoring import RecipeAuthor

    conditional["execution"] = {"path": "detailed"}
    # Honest schema limitations are not an instruction to patch source scripts.
    assert not RecipeAuthor.sample_errors({}, conditional)
    assert "Contact addresses" in DRAFT_INSTRUCTIONS
    assert (
        "headcount" in SYSTEM_PROMPT
        and "Field completeness is not schema validity" in SYSTEM_PROMPT
    )


def test_model_single_residual_rule_counts_notice_and_local_conditions():
    original = script_draft("具有良好沟通能力")
    refined = deepcopy(original)
    refined["notice_facts"]["conditions"] = [atom("common", "other", {"text": "身体健康"})]
    refined["notice_facts"]["condition_logic"] = {"condition_id": "common"}
    assert [e["code"] for e in validate_model_draft(refined, original)] == [
        "OTHER_CONDITION_CARDINALITY"
    ]
    result = normalize_extraction(refined)
    assert any(e["code"] == "OTHER_CONDITION_CARDINALITY" for e in result["errors"])
    assert len(result["positions"][0]["conditions"]) == 3  # Retained, never silently concatenated.


def test_bootstrap_extracts_simple_atoms_and_one_residual_without_inventing_requirements():
    conditions, logic = conditions_from_texts(
        [
            (None, "本科及以上；具有3年相关工作经验；年龄不超过40周岁；应届毕业生；2026届毕业生"),
            (None, "中共党员；身体健康；沟通能力良好；熟练的小鼠操作经验"),
            (None, "一般不超过50岁，特别优秀的年龄可适当放宽"),
        ]
    )
    by_type = {c.type: c.fact.value for c in conditions}
    assert by_type["age"] == {"minimum": None, "maximum": 40, "reference_date": None}
    assert by_type["work_experience"]["minimum_years"] == 3
    assert by_type["graduation_year"] == {"years": [2026]}
    assert by_type["graduation_status"]["status"] == "应届毕业生"
    assert by_type["political_status"] == {"statuses": ["中共党员"]}
    assert sum(c.type == "other" for c in conditions) == 1
    assert "放宽" in by_type["other"]["text"] and "小鼠" in by_type["other"]["text"]
    assert logic["operator"] == "all"
    bundle = material()
    bundle["page"]["tables"] = [{"rows": [["岗位名称"], ["实验助理（ 1 人）"]]}]
    p = extract_position_draft(bundle)["positions"][0]
    assert p["fields"]["headcount"] == {"value": 1, "tag": "source"}
    assert p["fields"]["title"]["value"] == "实验助理（ 1 人）"


def test_condition_refinement_can_extend_but_not_weaken_known_relations():
    original = script_draft("具有3年相关工作经验")
    refined = deepcopy(original)
    refined["positions"][0]["conditions"][-1] = atom(
        "exp", "work_experience", {"minimum_years": 3, "kind": "相关工作"}, "at_least"
    )
    refined["positions"][0]["condition_logic"]["items"][-1] = {"condition_id": "exp"}
    assert not merge_drafts(original, refined)["issues"]
    original = refined
    changed = deepcopy(original)
    changed["positions"][0]["condition_logic"]["operator"] = "any"
    assert any(
        i["code"] == "CONDITION_REFINEMENT_CONFLICT"
        for i in merge_drafts(original, changed)["issues"]
    )


def test_staffing_coexistence_and_cross_axis_scope_validation():
    fields, issues = employment_from_text("聘用人员为事业编制职工")
    assert not issues and fields["staffing_status"].value == "established"
    fields, issues = employment_from_text("实行备案制管理，不纳入事业编制")
    assert not issues and fields["staffing_status"].value == "non_established"
    fields, issues = employment_from_text("劳务派遣，纳入事业编制")
    assert issues[0]["code"] == "EMPLOYMENT_CONFLICT"
    original = script_draft()
    original["positions"][0]["fields"].update({k: v.as_dict() for k, v in fields.items()})
    result = normalize_extraction(original)
    assert any(
        e["code"] == "EMPLOYMENT_CONFLICT" and e["path"] == "positions[0].fields"
        for e in result["errors"]
    )
    fields, issues = employment_from_text("劳务派遣人员与人力资源公司签订劳动合同")
    assert not issues and fields["employment_type"].value == "labor_dispatch"
    assert fields["staffing_status"].value is None


def test_scoped_field_issue_is_not_assigned_to_an_unrelated_position():
    result = normalize_extraction(script_draft())
    result["errors"] = [
        {
            "code": "CONDITION_STRUCTURE_GAP",
            "message": "Synthetic gap",
            "path": "positions[0].conditions",
        }
    ]
    row = SimpleNamespace(result=result, errors=[], status="partial")
    assert audit_dimensions(row, "complete")["review_required"]
    assert position_field_issues(result, 1)
    assert not position_field_issues(result, 2)


def test_stage_evidence_has_hard_limits_and_validates_untrusted_receipts():
    stages = stage_receipts({"script_draft": {"text": "x" * STAGE_BYTES}})
    assert stages["script_draft"]["value"] is None and valid_stage_receipts(stages)
    broken = stage_receipts({"script_draft": {"text": "ok"}})
    broken["script_draft"]["value"]["text"] = "changed"
    assert not valid_stage_receipts(broken)
    broken["script_draft"]["value"] = {"x": float("nan")}
    assert not valid_stage_receipts(broken)


def test_author_runtime_rejects_structurally_incomplete_detail_not_optional_unknowns():
    from youju.recipe_service.authoring import RecipeAuthor

    result = normalize_extraction(script_draft("年龄不超过40周岁"))
    result["execution"] = {"path": "detailed"}
    assert any(
        e["code"] == "CONDITION_STRUCTURE_GAP" for e in RecipeAuthor.sample_errors({}, result)
    )
    result["execution"]["path"] = "quick"
    assert not RecipeAuthor.sample_errors({}, result)
    result = normalize_extraction(script_draft("遵纪守法，身体健康"))
    result["execution"] = {"path": "detailed"}
    assert not RecipeAuthor.sample_errors({}, result)


def test_preference_clause_cannot_hide_an_unrelated_unstructured_requirement():
    original = script_draft("年龄不超过40周岁；具有高校工作经验者优先")
    gaps = completion_issues(original)
    assert len(gaps) == 1 and gaps[0]["candidate_types"] == ["age"]


def test_author_runtime_failure_without_child_errors_never_validates_candidate():
    from youju.extraction_service import RecipeRunner as ApplicationRunner
    from youju.recipe_service.authoring import RecipeAuthor

    author = RecipeAuthor(None, ApplicationRunner(Script(script_draft("年龄不超过40周岁"))))
    attempt = asyncio.run(
        author.validate_candidate(
            template_candidate(), [material()], "2026-10-26T12:00:00+08:00", 1
        )
    )
    assert attempt["status"] == "rejected"
    assert attempt["errors"][0]["code"] == "MODEL_DISABLED"
    assert attempt["errors"][0]["sample_index"] == 0
    assert attempt["failures"][0]["error_code"] == "MODEL_DISABLED"
