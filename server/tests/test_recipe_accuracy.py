"""Accuracy contracts: real SDK branching, pure clocks and provider doubles."""

import asyncio
from copy import deepcopy

import pytest
from test_xdog_recipes import bundle, cost_route
from xdog.flow.events import NodeStarted

from youju.onboarding.periods import (
    evaluate_period,
    extract_application_period,
    rule_key,
    validate_period,
)
from youju.onboarding.recipes import RecipeAuthor, RecipeError, RecipeRunner, template_candidate
from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.positions.drafts import DRAFT_SCHEMA
from youju.positions.parsing import extract_position_draft

NOW = "2026-10-23T15:00:00+08:00"


def material(deadline="2026-10-31"):
    value = bundle(deadline)
    value["page"]["version_id"] = "page-v1"
    return value


class SandboxDouble:
    def __init__(self, capability="supported", empty=False, failed=False):
        self.capability, self.empty, self.failed = capability, empty, failed

    async def run_script(self, files, module, function, inputs):
        assert (
            module in {"parser", "undated_parser"}
            and set(files) == {f"{module}.py"}
            and function == "parse"
        )
        assert set(inputs) == {"bundle", "config"}
        if self.failed:
            raise ValueError("Synthetic parser failure")
        draft = extract_position_draft(inputs["bundle"], inputs["config"])
        if self.empty:
            draft["positions"] = []
            draft["coverage"]["complete"] = False
        if self.capability == "unsupported":
            draft["coverage"].update(supported=False, complete=False, needs_detailed=True)
        return draft


def test_period_clock_boundary_version_scope_and_cache_identity():
    value = material("2026-10-23")
    period = extract_application_period(value)
    assert not validate_period(value, period)
    result, errors = evaluate_period(value, period, NOW)
    assert not errors and result["status"] == "not_expired" and not result["coarse_eligible"]
    result, _ = evaluate_period(value, period, "2026-10-24T00:00:00+08:00")
    assert result["coarse_eligible"]
    key = rule_key(value, adapter_digest="adapter")
    value["page"]["version_id"] = "page-v2"
    assert validate_period(value, period)[0]["code"] == "PERIOD_VERSION"
    assert rule_key(value, adapter_digest="adapter") != key
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_period(value, period, "2026-10-23T15:00:00")


def test_exact_minute_retains_stated_precision_and_rejects_interview():
    value = material("2026-10-23 14:00")
    period = extract_application_period(value)
    for clock, expected in [("14:00:59", False), ("14:01:00", True)]:
        result, errors = evaluate_period(value, period, f"2026-10-23T{clock}+08:00")
        assert not errors and result["coarse_eligible"] is expected
    value["page"]["blocks"][-1]["text"] = "报名后面试截止日期：2026-10-22"
    period = extract_application_period(value)
    assert not evaluate_period(value, period, NOW)[0]["coarse_eligible"]


@pytest.mark.parametrize(
    "extra",
    [
        "其他岗位报名截止时间以附件为准",
        "本招聘公告长期有效",
        "报名时间延长至2026年11月1日",
        "第二轮报名截止2026年11月1日",
        "报名截止为公告发布后7个工作日",
    ],
)
def test_other_scopes_and_conflicts_never_close_whole_notice(extra):
    value = material("2026-10-22")
    value["page"]["blocks"].append({"id": "page-v1:b9", "text": extra, "locator": "p[9]"})
    period = extract_application_period(value)
    result, _ = evaluate_period(value, period, NOW)
    assert not result["coarse_eligible"]


def test_missing_attachment_preserves_common_cutoff_without_faking_complete_material():
    value = material("2026-10-22")
    value["status"] = "partial"
    value["attachments"] = [
        {
            "format": "pdf",
            "status": "failed",
            "errors": [{"code": "FETCH_FAILED", "message": "Missing attachment"}],
            "blocks": [],
        }
    ]
    period = extract_application_period(value)
    assert period["kind"] == "fixed_deadline"
    result, errors = evaluate_period(value, period, NOW)
    assert not errors and result["status"] == "expired" and result["coarse_eligible"]
    assert result["scope"] == "available_materials" and result["material_issues"]
    assert result["material_timing_impact"] == "non_blocking"
    assert result["current_hiring_status"] == "not_verified"


def test_adapter_cannot_claim_unsupported_boundary_or_scope():
    value = material("2026-10-22")
    period = extract_application_period(value)
    period["windows"][0]["boundary"] = "exclusive"
    assert "PERIOD_SEMANTICS" in {e["code"] for e in validate_period(value, period)}
    value["page"]["blocks"].append({"id": "new", "locator": "p[9]", "text": "招聘长期有效"})
    assert "PERIOD_COVERAGE" in {e["code"] for e in validate_period(value, period)}


def test_actual_flow_quick_coverage_supported_script_and_model_fallback():
    async def scenario():
        candidate = template_candidate()
        runner = RecipeRunner(SandboxDouble(empty=True))
        events = []
        result = await runner.run(
            candidate, material("2026-10-22"), now_iso=NOW, on_event=events.append
        )
        assert result["positions"] == [] and result["status"] == "review"
        assert result["coverage"]["complete"] is False
        execution = dict(result["execution"])
        stages = execution.pop("stages")
        from youju.recipe_runtime.diagnostics import valid_stage_receipts

        assert valid_stage_receipts(stages) and set(stages) == {"script_draft"}
        assert execution == {
            "path": "quick",
            "model_used": False,
            "runtime_contract": "recipe-runtime-v3",
            "parser": "parser.py",
            "script_issues": [],
        }
        assert [e.node_id for e in events if isinstance(e, NodeStarted)] == [
            "parse",
            "detail",
            "finish",
        ]
        assert "validity" not in result and "quality_tier" not in result
        attempt = await RecipeAuthor(None, runner).validate_candidate(
            candidate, [material("2026-10-22")], NOW, 1
        )
        assert not attempt["errors"] and attempt["samples"][0]["workflow_tested"]
        assert attempt["samples"][0]["coverage"]["complete"] is False
        with pytest.raises(RecipeError) as failure:
            await RecipeRunner(SandboxDouble(failed=True)).run(
                candidate, material("2026-10-22"), now_iso=NOW
            )
        assert failure.value.code == "SCRIPT_PARSE_FAILED"
        runner = RecipeRunner(SandboxDouble())
        result = await runner.run(candidate, material(), now_iso=NOW)
        assert result["status"] == "success" and len(result["positions"]) == 1
        assert result["execution"]["path"] == "detailed" and not result["execution"]["model_used"]
        assert result["positions"][0]["deadline_on"] == {"value": "2026-10-31", "tag": "source"}
        with pytest.raises(RecipeError) as failure:
            await RecipeRunner(SandboxDouble("unsupported")).run(candidate, material(), now_iso=NOW)
        assert failure.value.code == "MODEL_DISABLED"
        assert failure.value.rejected_origin == "script_draft"
        calls = []

        def complete(task, payload, schema, tier):
            calls.append(task)
            assert task == "extract_position_draft" and schema == DRAFT_SCHEMA and tier == "flow"
            assert payload["previous_draft"]["coverage"]["needs_detailed"]
            assert "config" not in payload
            return extract_position_draft(payload["bundle"])

        models = XdogModels(XdogConfig(), stub=complete)
        runner = RecipeRunner(SandboxDouble("unsupported"), models)
        events = []
        result = await runner.run(candidate, material(), now_iso=NOW, on_event=events.append)
        assert result["execution"]["model_used"] and calls == ["extract_position_draft"]
        assert [e.node_id for e in events if isinstance(e, NodeStarted)] == [
            "parse",
            "detail",
            "finish",
        ]
        attempt = await RecipeAuthor(models, runner).validate_candidate(
            candidate, [material()], NOW, 1
        )
        assert not attempt["errors"] and attempt["samples"][0]["workflow_tested"]
        # Quick policy never buys fallback, even when the draft honestly reports a gap.
        before = len(calls)
        quick = await runner.run(candidate, material(), now_iso=NOW, routing=cost_route("quick"))
        assert not quick["execution"]["model_used"] and len(calls) == before
        assert quick["positions"] == result["positions"]
        # An actual parser exception may fall back on detailed, never on quick.
        rescued = await RecipeRunner(SandboxDouble(failed=True), models).run(
            candidate, material(), now_iso=NOW
        )
        assert rescued["positions"] and rescued["execution"]["model_used"]
        assert rescued["execution"]["script_issues"] == [
            {"code": "SCRIPT_PARSE_FAILED", "message": "Isolated parser needs detailed fallback"}
        ]

    asyncio.run(scenario())


def test_editable_flow_and_notes_cannot_add_execution_authority():
    candidate = template_candidate()
    runner = RecipeRunner(SandboxDouble())
    assert not runner.validate(candidate)
    changed = deepcopy(candidate)
    changed["workflow"]["name"] = "custom"
    assert not runner.validate(changed)
    changed["workflow"]["nodes"][1]["subflow"]["nodes"][2]["tools"] = ["bash"]
    assert runner.validate(changed)[0]["code"] == "RECIPE_INVALID"
    candidate["config"]["structure_notes"] = {"model": "other"}
    assert runner.validate(candidate)[0]["code"] == "RECIPE_INVALID"
    for field in ("validity_py", "fast_parse_py"):
        changed = template_candidate()
        changed[field] = "raise AssertionError('legacy code must not run')"
        assert runner.validate(changed)[0]["code"] == "RECIPE_INVALID"


def test_position_windows_keep_later_scope_and_cannot_be_omitted():
    value = material()
    value["page"]["blocks"] = []
    rows = [["岗位名称", "报名截止日期"], ["岗位甲", "2026-10-22"], ["岗位乙", "2026-10-31"]]
    ids = []
    for n, row in enumerate(rows):
        keys = []
        for c, text in enumerate(row):
            key = f"v1:r{n}c{c}"
            value["page"]["blocks"].append({"id": key, "text": text, "locator": f"td[{n}][{c}]"})
            keys.append(key)
        ids.append(keys)
    value["page"]["tables"] = [{"rows": rows, "cell_evidence": ids, "id": "t1"}]
    period = extract_application_period(value)
    result, errors = evaluate_period(value, period, NOW)
    assert not errors and period["kind"] == "scoped"
    assert result["status"] == "not_expired" and not result["coarse_eligible"]
    omitted = deepcopy(period)
    omitted["windows"] = omitted["windows"][:1]
    assert "PERIOD_COVERAGE" in {e["code"] for e in validate_period(value, omitted)}
    result, errors = evaluate_period(value, period, "2026-11-01T00:00:00+08:00")
    assert not errors and result["coarse_eligible"]


@pytest.mark.parametrize(
    "text",
    [
        "报名截止日期并非2026年10月22日。",
        "报名截止2026年10月22日，但",
    ],
)
def test_actual_negation_or_unfinished_exception_is_not_a_material_flag(text):
    value = material("2026-10-22")
    value["page"]["blocks"][-1]["text"] = text
    for status in ("complete", "partial"):
        value["status"] = status
        period = extract_application_period(value)
        result, errors = evaluate_period(value, period, NOW)
        assert not errors and result["status"] == "unknown"
        assert not result["coarse_eligible"] and result["timing_blockers"]


def test_explicit_24_hour_cutoff_is_next_midnight_not_an_invalid_time():
    value = material("2026-10-23 24:00")
    value["status"] = "partial"
    value["page"]["truncated"] = True
    period = extract_application_period(value)
    active, errors = evaluate_period(value, period, "2026-10-23T23:59:59+08:00")
    assert not errors and active["status"] == "not_expired"
    expired, errors = evaluate_period(value, period, "2026-10-24T00:00:00+08:00")
    assert not errors and expired["status"] == "expired"
    assert expired["material_issues"] and expired["material_timing_impact"] == "non_blocking"


@pytest.mark.parametrize("suffix", ["UTC", "GMT+08:00", "pm", "下午"])
def test_explicit_unsupported_clock_zone_or_meridiem_never_assumes_shanghai(suffix):
    value = material(f"2026-10-23 14:00 {suffix}")
    period = extract_application_period(value)
    result, errors = evaluate_period(value, period, NOW)
    assert not errors and result["status"] == "unknown"
    assert not result["coarse_eligible"]
    assert "unsupported_time" in period["coverage"]["unresolved"]


@pytest.mark.parametrize("address", ["hr.UTC@example.org", "UTC@example.org", "hr@GMT.org"])
def test_contact_email_is_not_an_explicit_clock_zone(address):
    value = material(f"2026-10-23 14:00，联系邮箱：{address}")
    period = extract_application_period(value)
    result, errors = evaluate_period(value, period, NOW)
    assert not errors and result["status"] == "expired"
    assert result["coarse_eligible"]


@pytest.mark.parametrize("clock", [NOW, "2026-11-01T00:00:00+08:00"])
@pytest.mark.parametrize("mode", ["quick", "detailed"])
def test_exact_position_deadline_is_not_a_cost_route_or_clock_projection(clock, mode):
    value = material("2026-10-23 14:00")
    models = XdogModels(stub=lambda *_: pytest.fail("Supported date extraction reached a model"))
    result = asyncio.run(
        RecipeRunner(SandboxDouble(), models).run(
            template_candidate(), value, now_iso=clock, routing=cost_route(mode)
        )
    )
    position = result["positions"][0]
    assert position["deadline_on"] == {"value": "2026-10-23", "tag": "source"}
    assert position["application_dates"]["value"]["end"] == {
        "date": "2026-10-23",
        "year": 2026,
        "month": 10,
        "day": 23,
        "year_status": "explicit",
        "tag": "source",
        "time": "14:00",
    }
    assert position["title"] == {"value": "管理助理", "tag": "source"}
    assert result["routing"]["mode"] == mode and not result["execution"]["model_used"]
    assert "validity" not in result and "quality_tier" not in result
    assert models.calls == []


def test_position_scoped_dates_survive_an_intentionally_wrong_expired_route():
    value = material()
    value["page"]["blocks"] = []
    value["page"]["tables"] = [
        {
            "id": "scoped",
            "rows": [
                ["岗位名称", "报名截止日期"],
                ["岗位甲", "2026-10-22"],
                ["岗位乙", "2026-10-31"],
            ],
            "cell_evidence": [],
        }
    ]

    async def scenario():
        runner = RecipeRunner(SandboxDouble())
        quick = await runner.run(
            template_candidate(), value, now_iso=NOW, routing=cost_route("quick")
        )
        detailed = await runner.run(
            template_candidate(), value, now_iso=NOW, routing=cost_route("detailed")
        )
        assert quick["positions"] == detailed["positions"]
        assert [p["deadline_on"]["value"] for p in quick["positions"]] == [
            "2026-10-22",
            "2026-10-31",
        ]
        assert quick["status"] == "success" and quick["coverage"]["complete"]

    asyncio.run(scenario())
