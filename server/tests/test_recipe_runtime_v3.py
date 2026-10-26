"""Real official Flow, injected script/model boundaries, no provider or network."""

import asyncio
from copy import deepcopy

import pytest

from youju.extraction_service import ConfiguredExtractionModel
from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.positions.parsing import extract_position_draft
from youju.recipe_runtime.contracts import template_candidate
from youju.recipe_runtime.errors import RecipeError
from youju.recipe_runtime.runner import RecipeRunner


class ScriptDouble:
    async def run_script(self, files, module, function, inputs):
        assert (
            module in {"parser", "undated_parser"}
            and set(files) == {f"{module}.py"}
            and function == "parse"
        )
        return extract_position_draft(inputs["bundle"], inputs["config"])


def material():
    rows = [["岗位名称", "招聘人数", "学历要求"], ["研发岗", "2", "本科及以上"]]
    return {
        "status": "complete",
        "page": {
            "title": "招聘公告",
            "status": "ok",
            "blocks": [{"id": "b1", "text": "报名截止时间：2026年11月20日"}],
            "tables": [
                {
                    "id": "t1",
                    "rows": rows,
                    "cell_evidence": [[f"r{r}c{c}" for c in range(3)] for r in range(2)],
                }
            ],
        },
        "attachments": [],
        "errors": [],
    }


def route(mode):
    return {
        "category": "fixed_expired" if mode == "quick" else "fixed_active",
        "mode": mode,
        "reason": "Synthetic deliberately fallible cost hint",
        "policy_version": "test-v1",
        "evaluated_at": "2026-10-24T13:55:03+08:00",
    }


def test_cost_hypothesis_cannot_change_job_dates_or_fields():
    async def run():
        runner, candidate, bundle = RecipeRunner(ScriptDouble()), template_candidate(), material()
        quick = await runner.run(candidate, bundle, routing=route("quick"))
        detailed = await runner.run(candidate, bundle, routing=route("detailed"))
        assert quick["positions"] == detailed["positions"]
        assert "validity" not in quick and "quality_tier" not in quick
        assert quick["positions"][0]["deadline_on"] == {"value": "2026-11-20", "tag": "source"}
        assert quick["execution"]["model_used"] is False

    asyncio.run(run())


def test_old_recipe_is_rejected_not_relabelled():
    candidate = template_candidate()
    candidate["validity_py"] = "def check(ctx,bundle,now_iso,config): return {}"
    assert RecipeRunner(ScriptDouble()).validate(candidate)


def test_route_cannot_export_a_job_date():
    async def run():
        with pytest.raises(RecipeError, match="never job dates"):
            await RecipeRunner(ScriptDouble()).run(
                template_candidate(),
                material(),
                routing={**route("quick"), "deadline_on": "2014-06-30"},
            )

    asyncio.run(run())


def test_detailed_fallback_cannot_run_on_quick_route():
    class Unsupported:
        async def run_script(self, *args):
            return {
                "schema_version": "position-draft-v1",
                "notice_facts": {},
                "positions": [],
                "coverage": {"supported": False, "complete": False, "needs_detailed": True},
                "issues": [],
            }

    class Detailed(ConfiguredExtractionModel):
        def __init__(self):
            self.calls = 0
            super().__init__(XdogModels(XdogConfig(), stub=self.complete))

        def complete(self, *args):
            self.calls += 1
            return extract_position_draft(material())

    async def run():
        detailed = Detailed()
        runner = RecipeRunner(Unsupported(), detailed)
        quick = await runner.run(template_candidate(), material(), routing=route("quick"))
        assert detailed.calls == 0 and quick["positions"] == []
        full = await runner.run(template_candidate(), material(), routing=route("detailed"))
        assert detailed.calls == 1 and full["positions"]

    asyncio.run(run())


def test_missing_annex_does_not_request_detailed_model_alone():
    bundle = deepcopy(material())
    bundle["status"] = "partial"
    bundle["attachments"] = [
        {
            "status": "failed",
            "blocks": [],
            "errors": [{"code": "FETCH_FAILED", "message": "Unavailable"}],
        }
    ]

    async def run():
        result = await RecipeRunner(ScriptDouble()).run(
            template_candidate(), bundle, routing=route("detailed")
        )
        assert result["positions"] and result["execution"]["model_used"] is False

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "报名截止2026年11月20日14:00 UTC",
        "报名截止2026年11月20日14:00 pm",
        "报名截止日期并非2026年11月20日",
        "报名截止2026年11月20日，但",
        "报名截止2026年11月20日14:00:30",
    ],
)
def test_exact_job_dates_abstain_on_unsupported_or_negated_time(text):
    from youju.positions.parsing import extract_position_facts

    value = material()
    value["page"]["blocks"] = [{"id": "b1", "text": text}]
    result = extract_position_facts(value)
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert result["positions"][0]["title"]["value"] == "研发岗"
    assert result["errors"]


def test_empty_detailed_script_cannot_self_certify_that_no_agent_is_needed():
    class EmptySupported:
        async def run_script(self, *args):
            return {
                "schema_version": "position-draft-v1",
                "notice_facts": {},
                "positions": [],
                "coverage": {"supported": True, "complete": True, "needs_detailed": False},
                "issues": [],
            }

    async def scenario():
        with pytest.raises(RecipeError) as failure:
            await RecipeRunner(EmptySupported()).run(
                template_candidate(), material(), routing=route("detailed")
            )
        assert failure.value.code == "MODEL_DISABLED"
        assert failure.value.execution["model_used"] is False
        quick = await RecipeRunner(EmptySupported()).run(
            template_candidate(), material(), routing=route("quick")
        )
        assert quick["positions"] == [] and not quick["execution"]["model_used"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "text",
    [
        "应聘者须在2026年1月1日前年满18周岁",
        "应聘人员应于2026年7月1日前取得博士学位",
        "报名后面试截止日期为2026年10月1日",
        "报名截止后于2026年10月1日通知面试",
    ],
)
def test_qualification_or_interview_dates_never_become_job_cutoffs(text):
    from youju.positions.parsing import extract_position_facts

    value = material()
    value["page"]["blocks"] = [{"id": "b1", "text": text}]
    result = extract_position_facts(value)
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert result["positions"][0]["title"]["value"] == "研发岗"


def test_paired_start_heading_does_not_invent_a_closing_date():
    from youju.positions.parsing import extract_position_facts

    value = material()
    value["page"]["blocks"] = [
        {"id": "b1", "text": "报名时间"},
        {"id": "b2", "text": "2026年11月1日起报名"},
    ]
    position = extract_position_facts(value)["positions"][0]
    assert position["application_start_on"] == {"value": "2026-11-01", "tag": "source"}
    assert position["deadline_on"]["value"] is None


def test_unknown_other_job_scope_blocks_only_common_date_inheritance():
    from youju.positions.parsing import extract_position_facts

    value = material()
    value["page"]["blocks"].append({"id": "scope", "text": "其他岗位报名时间以附件为准"})
    result = extract_position_facts(value)
    assert result["positions"][0]["deadline_on"]["value"] is None
    assert result["positions"][0]["title"]["value"] == "研发岗"


def test_official_job_code_is_a_source_neutral_field_not_a_title_identity():
    from youju.positions.parsing import extract_position_facts

    value = material()
    value["page"]["tables"] = [
        {"rows": [["岗位代码", "岗位名称", "人数"], ["001-A", "研发岗", "2"]]}
    ]
    position = extract_position_facts(value)["positions"][0]
    assert position["position_code"] == {"value": "001-A", "tag": "source"}


def test_detailed_refines_opaque_script_conditions_without_a_bootstrap_oracle():
    from datetime import date

    from youju.jobs import job_validity
    from youju.positions.drafts import ExtractionDraft
    from youju.positions.facts import Condition, TaggedField

    raw = {
        "key": "row:1",
        "fields": {
            "title": {"value": "研发岗", "tag": "source"},
            "requirements_text": {"value": "本科并具有2年相关工作经验", "tag": "source"},
        },
        "dates": {},
        "conditions": [
            Condition(
                "raw",
                "other",
                "unspecified",
                TaggedField({"text": "本科并具有2年相关工作经验"}, "source"),
            ).as_dict()
        ],
        "condition_logic": {"condition_id": "raw"},
    }
    script_draft = ExtractionDraft(
        positions=[raw],
        coverage={"supported": True, "complete": False, "needs_detailed": True},
        issues=[
            {"code": "UNRESOLVED_CONDITION", "message": "Bootstrap cannot interpret prose"},
            {"code": "AMBIGUOUS_APPLICATION_DATE", "message": "Bootstrap needs semantic scope"},
        ],
    ).as_dict()
    detailed = deepcopy(script_draft)
    detailed["issues"] = []
    detailed["coverage"].update(complete=True, needs_detailed=False)
    detailed["positions"][0]["conditions"] = [
        Condition(
            "edu", "education", "at_least", TaggedField({"level": "bachelor"}, "source")
        ).as_dict(),
        Condition(
            "exp",
            "work_experience",
            "at_least",
            TaggedField({"minimum_years": 2, "kind": "相关工作"}, "source"),
        ).as_dict(),
    ]
    detailed["positions"][0]["condition_logic"] = {
        "operator": "all",
        "items": [{"condition_id": "edu"}, {"condition_id": "exp"}],
    }
    from youju.positions.deadlines import date_fact

    detailed["positions"][0]["dates"] = {"end": date_fact(2026, 11, 20)}

    class Script:
        async def run_script(self, *args):
            return deepcopy(script_draft)

    class Detailed(ConfiguredExtractionModel):
        def __init__(self):
            super().__init__(XdogModels(XdogConfig(), stub=lambda *args: deepcopy(detailed)))

    async def scenario():
        result = await RecipeRunner(Script(), Detailed()).run(
            template_candidate(), material(), routing=route("detailed")
        )
        assert {item["type"] for item in result["positions"][0]["conditions"]} == {
            "education",
            "work_experience",
        }
        assert result["errors"] == [] and result["status"] == "success"
        assert {item["code"] for item in result["execution"]["script_issues"]} == {
            "UNRESOLVED_CONDITION",
            "AMBIGUOUS_APPLICATION_DATE",
        }
        from youju.positions.facts import PositionFacts

        validity = job_validity(PositionFacts.from_dict(result["positions"][0]), date(2026, 10, 24))
        assert validity["status"] == "not_expired"

    asyncio.run(scenario())


def test_unknown_condition_relationship_keeps_typed_atoms_without_inventing_and():
    from youju.positions.drafts import ExtractionDraft
    from youju.positions.facts import Condition, TaggedField
    from youju.positions.normalization import normalize_extraction

    job = {
        "key": "row:1",
        "fields": {"title": {"value": "研发岗", "tag": "source"}},
        "dates": {},
        "conditions": [
            Condition(
                "edu", "education", "at_least", TaggedField({"level": "bachelor"}, "source")
            ).as_dict()
        ],
        "condition_logic": None,
    }
    result = normalize_extraction(
        ExtractionDraft(
            positions=[job],
            coverage={"supported": True, "complete": False, "needs_detailed": False},
        ).as_dict()
    )
    assert result["positions"][0]["conditions"][0]["type"] == "education"
    assert result["positions"][0]["condition_logic"] is None
    assert any(item["code"] == "UNRESOLVED_CONDITION_LOGIC" for item in result["errors"])


@pytest.mark.parametrize(
    "category,mode", [("undated_low_priority", "quick"), ("undated_high_priority", "detailed")]
)
def test_dedicated_undated_script_dispatch_and_cost_metadata_isolation(category, mode):
    class UndatedScript:
        async def run_script(self, files, module, function, inputs):
            assert set(files) == {"undated_parser.py"}
            assert module == "undated_parser" and function == "parse"
            assert "date_hints" not in inputs["bundle"]["page"]
            assert "routing" not in inputs
            draft = extract_position_draft(inputs["bundle"])
            draft["coverage"]["needs_detailed"] = False
            return draft

    async def run():
        bundle = material()
        bundle["page"]["blocks"] = []
        bundle["page"]["date_hints"] = [{"kind": "html_meta", "value": "2020-01-01"}]
        result = await RecipeRunner(UndatedScript()).run(
            template_candidate(),
            bundle,
            routing={**route(mode), "category": category},
        )
        assert result["execution"]["parser"] == "undated_parser.py"
        assert result["positions"][0]["deadline_on"] == {"value": None, "tag": "unknown"}
        assert bundle["page"]["date_hints"]  # Pinned input was not mutated.

    asyncio.run(run())


def test_dedicated_script_is_required_and_independently_validated():
    candidate = template_candidate()
    del candidate["undated_parser_py"]
    assert RecipeRunner.validate(candidate)
    candidate = template_candidate()
    candidate["undated_parser_py"] = "def parse(ctx, bundle, config, routing): return {}"
    assert RecipeRunner.validate(candidate)
