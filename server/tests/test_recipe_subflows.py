"""Native subflow/conditional execution and capability fences; no network/model spend."""

import asyncio
from copy import deepcopy

import pytest
from test_recipe_runtime_v3 import ScriptDouble, material, route

from youju.extraction_service import RecipeRunner
from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.recipe_runtime.contracts import template_candidate, validate_candidate
from youju.recipe_runtime.errors import RecipeError


class Unsupported(ScriptDouble):
    async def run_script(self, *args):
        result = await super().run_script(*args)
        result["coverage"]["needs_detailed"] = True
        return result


def test_three_native_subflows_with_direct_agent_and_no_nested_model_workflow(monkeypatch):
    candidate = template_candidate()
    assert [(n["id"], n["type"]) for n in candidate["workflow"]["nodes"]] == [
        ("parse", "subflow"),
        ("detail", "subflow"),
        ("finish", "subflow"),
    ]
    child = candidate["workflow"]["nodes"][1]["subflow"]
    assert any(n["type"] == "agent" for n in child["nodes"])
    assert any("when" in e for e in child["edges"])

    def forbidden(*args, **kwargs):
        pytest.fail("The native agent must not launch XdogModels.complete's nested workflow")

    monkeypatch.setattr(XdogModels, "complete", forbidden)
    calls = []

    def model(task, payload, schema, tier):
        from youju.positions.parsing import extract_position_draft

        calls.append(task)
        return extract_position_draft(payload["bundle"])

    models = XdogModels(XdogConfig(), stub=model)
    result = asyncio.run(
        RecipeRunner(Unsupported(), models).run(
            candidate,
            material(),
            now_iso=route("detailed")["evaluated_at"],
            routing=route("detailed"),
        )
    )
    assert result["positions"] and result["execution"]["model_used"]
    assert calls == ["extract_position_draft"]
    assert models.calls[0]["engine"] == "xdog-flow-native-agent"


def test_author_can_rename_child_nodes_and_change_parser_conditions():
    candidate = template_candidate()
    child = candidate["workflow"]["nodes"][0]["subflow"]
    # A source author chooses the general parser even for undated layouts.
    for e in child["edges"]:
        if e["from"] == "review" and e["to"] == "general":
            e["when"] = {"equals": {"value": "yes", "text": "yes"}}
        elif e["from"] == "review" and e["to"] == "undated":
            e["when"] = {"equals": {"value": "yes", "text": "no"}}
    for node in child["nodes"]:
        if node["id"] == "general":
            node["id"] = "source_layout"
    for e in child["edges"]:
        for key in ("from", "to"):
            if e[key] == "general":
                e[key] = "source_layout"
    assert not validate_candidate(candidate)
    routing = {**route("quick"), "category": "undated_low_priority"}
    result = asyncio.run(
        RecipeRunner(ScriptDouble()).run(
            candidate, material(), now_iso=routing["evaluated_at"], routing=routing
        )
    )
    assert result["execution"]["parser"] == "parser.py"
    assert result["positions"][0]["deadline_on"]["value"] == "2026-11-20"


def test_edited_flow_cannot_spend_model_budget_on_quick_route():
    candidate = template_candidate()
    edges = candidate["workflow"]["nodes"][1]["subflow"]["edges"]
    for e in edges:
        if e["from"] == "inspect":
            e["when"] = {
                "equals": {"value": "yes", "text": "yes" if e["to"] == "prepare" else "no"}
            }
    models = XdogModels(XdogConfig(), stub=lambda *_: pytest.fail("Quick model spend"))
    assert not validate_candidate(candidate)
    with pytest.raises(RecipeError) as failure:
        asyncio.run(
            RecipeRunner(Unsupported(), models).run(
                candidate,
                material(),
                now_iso=route("quick")["evaluated_at"],
                routing=route("quick"),
            )
        )
    assert failure.value.code == "MODEL_POLICY"
    assert not models.calls


@pytest.mark.parametrize(
    "change", ["inline", "path", "tools", "skills", "backend", "provider", "model", "schema"]
)
def test_editable_graph_cannot_expand_execution_authority(change):
    candidate = template_candidate()
    child = candidate["workflow"]["nodes"][1]["subflow"]
    agent = next(n for n in child["nodes"] if n["type"] == "agent")
    if change == "inline":
        child["nodes"][0]["code"] = "raise AssertionError('must not load')"
    elif change == "path":
        candidate["workflow"]["nodes"][0]["subflow"] = "/tmp/external.json"
    elif change in {"tools", "skills"}:
        agent[change] = ["bash"]
    elif change == "backend":
        agent[change] = "codex-cli"
    elif change == "provider":
        child[change] = "external"
    elif change == "model":
        agent[change] = "arbitrary-expensive-model"
    else:
        agent["outputs"][0]["schema"] = {"type": "object"}
    assert validate_candidate(candidate)[0]["code"] == "RECIPE_INVALID"


def test_skipped_agent_never_initializes_provider(monkeypatch):
    import xdog.ai as ai

    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("Skipped agent touched provider"))
    result = asyncio.run(
        RecipeRunner(Unsupported()).run(
            template_candidate(),
            material(),
            now_iso=route("quick")["evaluated_at"],
            routing=route("quick"),
        )
    )
    assert not result["execution"]["model_used"]


def test_malformed_model_result_keeps_original_failure_and_attempt_provenance():
    models = XdogModels(XdogConfig(), stub=lambda *_: {"wrong": "contract"})
    with pytest.raises(RecipeError) as failure:
        asyncio.run(
            RecipeRunner(Unsupported(), models).run(
                template_candidate(),
                material(),
                now_iso=route("detailed")["evaluated_at"],
                routing=route("detailed"),
            )
        )
    assert failure.value.code == "OUTPUT_SCHEMA"
    assert failure.value.execution["model_used"]
    assert failure.value.rejected_result == {"wrong": "contract"}
    assert models.calls[0]["status"] == "failed"


@pytest.mark.parametrize(
    "outcome", ["success", "provider_error", "token_limit", "cancel", "two_agents"]
)
def test_native_agent_transport_and_cleanup_with_synthetic_provider(monkeypatch, outcome):
    """Exercise SdkRunner too: only the provider stream is synthetic, never HTTP."""
    import xdog.ai as ai
    from xdog.ai.types import AssistantMessage, Model, ToolCall, Usage
    from xdog.ai.utils.event_stream import EventStream

    from youju.positions.parsing import extract_position_draft

    requests, cleanup = [], []
    entered = None

    class Provider:
        def models(self):
            return (
                Model(
                    id="subflow-test/model",
                    provider="subflow-test",
                    api="test",
                    supports_tool_calls=True,
                    context_window=200000,
                ),
            )

        def model(self, name):
            assert name == "subflow-test/model"
            return self.models()[0]

        def stream(self, model, context, options):
            requests.append(model)
            assert {tool.name for tool in context.tools} == {"submit_result"}
            assert options.web_search is False
            assert options.parallel_tool_calls is False
            if outcome == "cancel":

                async def hanging():
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                        yield
                    finally:
                        cleanup.append("closed")

                return EventStream(iterator=hanging())
            if outcome == "provider_error":
                return EventStream.empty(
                    AssistantMessage(
                        stop_reason="error", error_message="synthetic provider failure"
                    )
                )
            return EventStream.empty(
                AssistantMessage(
                    content=(
                        ToolCall(
                            id="native-flow-result",
                            name="submit_result",
                            arguments={"result": extract_position_draft(material())},
                        ),
                    ),
                    stop_reason="toolUse",
                    usage=Usage(input=10, output=2),
                )
            )

    provider = Provider()
    monkeypatch.setattr(ai, "provider", lambda name: provider)
    config = XdogConfig(
        provider="subflow-test",
        small_model="subflow-test/model",
        large_model="subflow-test/model",
        flow_model="subflow-test/model",
        author_model="subflow-test/model",
        live_enabled=True,
        max_tokens=1 if outcome == "token_limit" else None,
    )
    models = XdogModels(config)

    async def scenario():
        nonlocal entered
        entered = asyncio.Event()
        candidate = template_candidate()
        if outcome == "two_agents":
            child = candidate["workflow"]["nodes"][1]["subflow"]
            by_id = {n["id"]: n for n in child["nodes"]}
            for previous, name in (
                ("prepare", "prepare_second"),
                ("extract", "extract_second"),
                ("merge", "merge_second"),
            ):
                extra = deepcopy(by_id[previous])
                extra["id"] = name
                child["nodes"].append(extra)
            child["edges"] = [e for e in child["edges"] if e["from"] != "merge"]
            from youju.recipe_runtime.workflow import edge

            child["edges"].extend(
                [
                    edge("merge", "prepare_second"),
                    edge("prepare_second", "extract_second", {"payload": "payload"}),
                    edge("prepare_second", "merge_second"),
                    edge("extract_second", "merge_second", {"draft": "detailed"}),
                    edge("merge_second", "$output"),
                ]
            )
        task = asyncio.create_task(
            RecipeRunner(Unsupported(), models).run(
                candidate,
                material(),
                now_iso=route("detailed")["evaluated_at"],
                routing=route("detailed"),
            )
        )
        if outcome == "cancel":
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleanup == ["closed"]
            assert models.calls[0]["status"] == "cancelled"
        elif outcome in {"success", "two_agents"}:
            result = await task
            assert result["positions"] and result["execution"]["model_used"]
            assert models.calls[0]["usage"]["reported"]
            assert models.calls[0]["status"] == "success"
        else:
            with pytest.raises(RecipeError) as failure:
                await task
            assert failure.value.code == (
                "MODEL_PROVIDER_ERROR" if outcome == "provider_error" else "MODEL_TOKEN_LIMIT"
            )
            assert failure.value.execution["model_used"]
            assert models.calls[0]["error_code"] == failure.value.code
        assert requests == ["subflow-test/model"] * (2 if outcome == "two_agents" else 1)

    asyncio.run(scenario())
