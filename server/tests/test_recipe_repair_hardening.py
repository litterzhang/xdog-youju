"""Input safety, semantic feedback, lossless failure facts and unified repair gates."""

import asyncio
from copy import deepcopy

import pytest
from sqlalchemy.orm import Session
from test_auto_repair import failed_incremental
from test_onboarding import run_onboarding, setup_source
from test_recipe_runtime_v3 import material, route
from test_recipe_subflows import Unsupported

from youju.extraction_service import RecipeRunner
from youju.model_limits import MAX_AGENT_INPUT_BYTES, input_bytes
from youju.models import AuditResult
from youju.onboarding.diagnostics import failed_result, failure_record, page_record
from youju.onboarding.repair import select_repair_samples
from youju.onboarding.xdog import XdogConfig, XdogModels, _CappedStream
from youju.positions.parsing import extract_position_draft
from youju.recipe_runtime.contracts import template_candidate
from youju.recipe_runtime.errors import RecipeError


def test_input_budget_is_decimal_utf8_and_cannot_be_raised():
    assert MAX_AGENT_INPUT_BYTES == 1_000_000
    assert input_bytes({"text": "汉"}) > input_bytes({"text": "a"})
    assert XdogConfig().max_payload_bytes == MAX_AGENT_INPUT_BYTES
    with pytest.raises(ValueError, match="1,000,000"):
        XdogConfig(max_payload_bytes=MAX_AGENT_INPUT_BYTES + 1)


def test_oversized_agent_payload_is_rejected_before_model_with_script_facts_retained():
    async def scenario():
        bundle = material()
        bundle["large_metadata"] = "汉" * 350000
        models = XdogModels(stub=lambda *_: pytest.fail("Oversized model was invoked"))
        with pytest.raises(RecipeError) as caught:
            await RecipeRunner(Unsupported(), models).run(
                template_candidate(),
                bundle,
                now_iso=route("detailed")["evaluated_at"],
                routing=route("detailed"),
            )
        error = caught.value
        assert error.code == "MODEL_INPUT_LIMIT"
        assert not models.calls and not error.execution["model_used"]
        assert error.partial_result["positions"][0]["title"]["value"] == "研发岗"
        result, issues = failed_result(error, failure_ref="synthetic-ref")
        assert result["status"] == "partial" and len(result["positions"]) == 1
        assert result["coverage"]["complete"] is False
        assert issues[0]["failure_code"] == "MODEL_INPUT_LIMIT"
        record = failure_record(bundle, "candidate", route("detailed")["evaluated_at"], error)
        assert record["partial_output"]["value"] == error.partial_result
        assert page_record(record, section="partial_output")["available"]

    asyncio.run(scenario())


def test_context_guard_includes_system_tools_and_followup_history():
    from xdog.ai.types import AssistantMessage, Context, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream

    async def scenario():
        calls = []

        def provider(model, context, options):
            calls.append(context)
            return EventStream.empty(AssistantMessage(stop_reason="stop"))

        stream = _CappedStream(provider, XdogConfig(max_payload_bytes=1000), {"type": "object"})
        from xdog.ai.types import StreamOptions

        context = Context(system_prompt="s" * 1100, messages=())
        await stream("synthetic", context, StreamOptions()).result()
        assert stream.failure.code == "MODEL_INPUT_LIMIT" and not calls
        assert stream.blocked_requests == 1 and stream.requests == 0
        followup = _CappedStream(provider, XdogConfig(max_payload_bytes=2000), {"type": "object"})
        small = Context(messages=(UserMessage(content=(TextContent(text="hello"),)),))
        await followup("synthetic", small, StreamOptions()).result()
        assert followup.requests == 1
        large = deepcopy(small)
        from dataclasses import replace

        large = replace(
            large, messages=(*large.messages, UserMessage(content=(TextContent(text="汉" * 1000),)))
        )
        await followup("synthetic", large, StreamOptions()).result()
        assert followup.failure.code == "MODEL_INPUT_LIMIT"
        assert followup.requests == 1 and len(calls) == 1

    asyncio.run(scenario())


def test_native_agent_receives_semantic_feedback_and_can_resubmit(monkeypatch):
    import xdog.ai as ai
    from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall, Usage
    from xdog.ai.utils.event_stream import EventStream

    class Provider:
        def __init__(self):
            self.calls = []

        def models(self):
            return (
                Model(
                    id="repair-test/model",
                    provider="repair-test",
                    api="test",
                    supports_tool_calls=True,
                ),
            )

        def model(self, name):
            assert name == "repair-test/model"
            return self.models()[0]

        def stream(self, model, context, options):
            self.calls.append(context)
            draft = extract_position_draft(material())
            if len(self.calls) == 1:
                draft["notice_facts"]["conditions"] = []
                draft["notice_facts"]["condition_logic"] = {"operator": "all", "items": []}
            else:
                feedback = context.messages[-1]
                assert any(
                    isinstance(p, TextContent) and "Host canonical validation rejected" in p.text
                    for p in feedback.content
                )
            return EventStream.empty(
                AssistantMessage(
                    content=(
                        ToolCall(
                            id=f"submit-{len(self.calls)}",
                            name="submit_result",
                            arguments={"result": draft},
                        ),
                    ),
                    stop_reason="toolUse",
                    usage=Usage(input=10, output=5),
                )
            )

    provider = Provider()
    monkeypatch.setattr(ai, "provider", lambda _: provider)
    models = XdogModels(
        XdogConfig(
            provider="repair-test",
            small_model="unused",
            large_model="unused",
            flow_model="repair-test/model",
            live_enabled=True,
        )
    )
    result = asyncio.run(
        RecipeRunner(Unsupported(), models).run(
            template_candidate(),
            material(),
            now_iso=route("detailed")["evaluated_at"],
            routing=route("detailed"),
        )
    )
    assert result["positions"] and len(provider.calls) == 2
    assert models.calls[0]["usage"]["validation_rejections"] == 1


@pytest.mark.parametrize(
    "code", ["MODEL_ABORTED", "MODEL_PROVIDER_ERROR", "RESULT_INVALID", "DRAFT_INVALID"]
)
def test_manual_repair_uses_same_diagnosis_gate_as_automatic(tmp_path, code):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        _, summary = failed_incremental(engine, store, code=code)
        with Session(engine) as session:
            row = session.get(AuditResult, summary["results"][0]["id"])
            feedback = {
                "issues": [
                    {
                        "bundle_id": row.bundle_id,
                        "audit_result_id": row.id,
                        "audit_status": row.status,
                        "errors": row.errors,
                    }
                ]
            }
        if code == "DRAFT_INVALID":
            assert (
                len(select_repair_samples(store, "source", pipeline.classifier_key, feedback)) == 1
            )
        else:
            with pytest.raises(ValueError, match="eligible"):
                select_repair_samples(store, "source", pipeline.classifier_key, feedback)
    finally:
        engine.dispose()


def empty_material():
    bundle = material()
    bundle["page"]["blocks"] = []
    bundle["page"]["tables"] = [{"rows": [["序号", "岗位", "条件"], ["0", "", ""]]}]
    return bundle


class EmptyTableScript:
    async def run_script(self, *args):
        return {
            "schema_version": "position-draft-v1",
            "notice_facts": {},
            "positions": [],
            "coverage": {
                "supported": True,
                "complete": True,
                "needs_detailed": False,
                "empty_capture": {"table_index": 0},
            },
            "issues": [],
        }


def test_verified_empty_table_is_an_observation_not_a_failure_or_invented_job():
    async def scenario():
        models = XdogModels(
            stub=lambda *_: pytest.fail("Empty capture should not buy a model call")
        )
        result = await RecipeRunner(EmptyTableScript(), models).run(
            template_candidate(),
            empty_material(),
            now_iso=route("detailed")["evaluated_at"],
            routing=route("detailed"),
        )
        assert result["status"] == "empty" and result["positions"] == []
        assert not result["execution"]["model_used"] and not models.calls
        assert result["coverage"]["empty_capture"] == {"table_index": 0}

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["data_present", "missing_capture"])
def test_empty_claim_cannot_hide_data_or_missing_material(case):
    async def scenario():
        bundle = empty_material()
        if case == "data_present":
            bundle["page"]["tables"][0]["rows"][1] = ["1", "研发岗", "本科"]
        else:
            bundle["status"] = "partial"
        with pytest.raises(RecipeError) as caught:
            await RecipeRunner(EmptyTableScript()).run(
                template_candidate(),
                bundle,
                now_iso=route("detailed")["evaluated_at"],
                routing=route("detailed"),
            )
        assert caught.value.code == (
            "DRAFT_INVALID" if case == "data_present" else "EMPTY_CAPTURE_UNVERIFIED"
        )

    asyncio.run(scenario())


def test_sandbox_infrastructure_is_not_reclassified_as_source_script_failure():
    from youju.onboarding.sandbox import SandboxError

    class BrokenSandbox:
        async def run_script(self, *args):
            raise SandboxError("synthetic infrastructure unavailable")

    async def scenario():
        with pytest.raises(RecipeError) as caught:
            await RecipeRunner(BrokenSandbox()).run(
                template_candidate(),
                material(),
                now_iso=route("quick")["evaluated_at"],
                routing=route("quick"),
            )
        assert caught.value.code == "SCRIPT_RUNTIME_UNAVAILABLE"
        assert caught.value.partial_result is None

    asyncio.run(scenario())


def test_source_prompts_cannot_amplify_or_move_material_into_system_instructions():
    candidate = template_candidate()
    node = candidate["workflow"]["nodes"][1]["subflow"]["nodes"][2]
    node["prompt"] = "{{payload}}{{payload}}"
    assert RecipeRunner.validate(candidate)
    node["prompt"] = "{{payload}}"
    node["system_prompt"] = "{{payload}}"
    assert RecipeRunner.validate(candidate)


def test_failed_enrichment_is_not_a_reason_to_redownload_a_readable_parent():
    from youju.onboarding.pipeline import Pipeline

    bundle = material()
    bundle["page"]["status"] = "ok"
    audit = {
        "status": "review",
        "position_count": 1,
        "errors": [],
        "failure": {
            "code": "MODEL_PROVIDER_ERROR",
            "origin": "model_execution",
            "evidence_digest": "a" * 64,
        },
    }
    assert not Pipeline._needs_parent_render(bundle, audit)
    bundle["page"]["truncated"] = True
    assert Pipeline._needs_parent_render(bundle, audit)


def test_source_error_strings_cannot_mint_recipe_repair_authority(tmp_path):
    from sqlalchemy import select

    from youju.onboarding.repair_diagnostics import diagnose_row

    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session:
            row = session.scalar(select(AuditResult))
            row.errors = [{"code": "DRAFT_INVALID", "failure_code": "DRAFT_INVALID"}]
            assert "failure" not in row.result["execution"]
            assert not diagnose_row(session, row)["repairable"]
    finally:
        engine.dispose()


def test_partial_known_facts_remain_regression_protected(tmp_path):
    from sqlalchemy import select

    from youju.onboarding.repair import protectable_facts

    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        with Session(engine) as session:
            row = session.scalar(select(AuditResult))
            issue = {
                "code": "MISSING_POSITION_TITLE",
                "message": "A different blank row was discarded",
            }
            row.status = "review"
            row.result = deepcopy(row.result)
            row.result.update(status="partial", errors=[issue])
            row.result["coverage"]["complete"] = False
            row.errors = [issue]
            assert protectable_facts(session, row)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "capture_mode,capture_state",
    [
        ("static", "done"),
        ("dynamic", "done"),
        ("dynamic", "ready"),
        ("dynamic", "review"),
    ],
)
def test_empty_capture_audit_requires_acquisition_check_not_code_repair(
    tmp_path, capture_mode, capture_state
):
    from sqlalchemy import select
    from test_onboarding import BASE

    from youju.models import ResourceScan, SourceRecipe
    from youju.onboarding.auditing import AuditLedger
    from youju.onboarding.repair_diagnostics import diagnose_row

    html = (
        "<html><head><title>学院岗位招聘</title></head><body>"
        "<h1>学院岗位招聘</h1><table><tr><th>序号</th><th>岗位</th><th>招聘条件</th></tr>"
        "<tr><td>0</td><td></td><td></td></tr></table></body></html>"
    ).encode()
    engine, store, _, pipeline = setup_source(
        tmp_path, pages={BASE: (html, "text/html; charset=utf-8")}
    )
    try:
        asyncio.run(pipeline.worker.run_once("source", 50))
        store.build_bundles("source")
        bundle = store.bundles("source")[0]
        result = asyncio.run(
            RecipeRunner(EmptyTableScript()).run(
                template_candidate(),
                bundle,
                now_iso=route("detailed")["evaluated_at"],
                routing=route("detailed"),
            )
        )
        with Session(engine) as session, session.begin():
            scan = session.scalar(select(ResourceScan))
            scan.mode = capture_mode  # Synthetic mode metadata, not a real browser experiment.
            scan.state = capture_state
            recipe = SourceRecipe(
                source_id="source",
                digest="empty-recipe",
                body_ref="fixture",
                status="validated",
                validation={},
            )
            session.add(recipe)
            session.flush()
            recipe_id = recipe.id
        saved = AuditLedger(store).record(bundle, recipe_id, "2026-10-24", result)
        with Session(engine) as session:
            row = session.get(AuditResult, saved["id"])
            assert row.parse_id is None
            if capture_mode != "dynamic" or capture_state != "done":
                assert row.status == "review"
                assert row.errors[0]["code"] == "EMPTY_CAPTURE_NEEDS_RENDER"
                assert diagnose_row(session, row)["category"] == "acquisition"
                assert not diagnose_row(session, row)["repairable"]
            else:
                assert row.status == "empty" and not row.errors
                assert not saved["review_required"]
            assert saved["extraction_status"] == "empty_capture"
    finally:
        engine.dispose()


def test_absent_rejected_output_does_not_add_a_false_schema_defect():
    from youju.positions.extraction import validate_output

    error = RecipeError(
        "MODEL_INPUT_LIMIT",
        "Synthetic oversized input",
        routing=route("detailed"),
        execution={
            "path": "detailed",
            "model_used": False,
            "runtime_contract": "recipe-runtime-v3",
            "parser": "parser.py",
            "script_issues": [],
        },
        rejected_origin="model_input",
    )
    result, details = failed_result(error)
    assert result["status"] == "failed" and result["positions"] == []
    assert validate_output({}, result) == []
    assert details[0]["failure_code"] == "MODEL_INPUT_LIMIT"
    assert details[0]["rejected_output_available"] is False


@pytest.mark.parametrize("common_title", [False, True])
def test_model_cannot_rewrite_known_source_identity(common_title):
    from youju.positions.drafts import validate_model_draft

    previous = extract_position_draft(material())
    if common_title:
        previous["notice_facts"].setdefault("fields", {})["title"] = previous["positions"][0][
            "fields"
        ].pop("title")
    proposal = deepcopy(previous)
    target = proposal["notice_facts"] if common_title else proposal["positions"][0]
    target["fields"]["title"] = {"value": "研发岗（某实验室）", "tag": "source"}
    issues = validate_model_draft(proposal, previous)
    assert any("Preserve the existing source title" in i["message"] for i in issues)
    # Omitting it refines other fields without inventing another identity.
    target["fields"].pop("title")
    assert validate_model_draft(proposal, previous) == []


def test_identity_merge_failure_keeps_script_candidate_instead_of_zero_rows():
    class InferredScript:
        async def run_script(self, *args):
            result = extract_position_draft(material())
            result["positions"][0]["fields"]["title"]["tag"] = "ai_inferred"
            result["coverage"]["needs_detailed"] = True
            return result

    def proposal(task, payload, schema, tier):
        result = deepcopy(payload["previous_draft"])
        result["positions"][0]["fields"]["title"] = {"value": "研发工程师", "tag": "ai_inferred"}
        return result

    async def scenario():
        with pytest.raises(RecipeError) as caught:
            await RecipeRunner(InferredScript(), XdogModels(stub=proposal)).run(
                template_candidate(),
                material(),
                now_iso=route("detailed")["evaluated_at"],
                routing=route("detailed"),
            )
        assert caught.value.code == "REFINEMENT_IDENTITY_CONFLICT"
        assert caught.value.rejected_origin == "merged_draft"
        assert (
            caught.value.stage_outputs["merged_draft"]["positions"][0]["fields"]["title"]["value"]
            is None
        )
        result, errors = failed_result(caught.value)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["title"] == {"value": "研发岗", "tag": "ai_inferred"}
        assert errors[0]["failure_code"] == "REFINEMENT_IDENTITY_CONFLICT"

    asyncio.run(scenario())
