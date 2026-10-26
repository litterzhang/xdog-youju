"""Focused flow-boundary checks; never call a real provider or run candidate code."""

import asyncio
import copy
import json
from dataclasses import replace
from importlib.resources import files

import pytest
import xdog.ai as ai
from test_onboarding import SyntheticAuthorStreamFactory
from xdog.agent.skills import load_packaged_skill
from xdog.ai.types import AssistantMessage, DoneEvent, Model, TextContent, ToolCall, Usage
from xdog.ai.utils.event_stream import EventStream
from xdog.flow.events import NodeFinished, NodeStarted

from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.recipes import (
    RecipeAuthor,
    RecipeError,
    RecipeRunner,
    candidate_digest,
    template_candidate,
)
from youju.onboarding.xdog import (
    AUTHOR_DIAGNOSTIC_LIMIT,
    AUTHOR_REPORT_MESSAGE_LIMIT,
    ModelError,
    XdogConfig,
    XdogModels,
)
from youju.positions.contracts import CONTRACT
from youju.positions.drafts import DRAFT_SCHEMA
from youju.positions.extraction import validate_output
from youju.positions.facts import PositionFacts
from youju.positions.parsing import extract_position_draft
from youju.recipe_runtime.contracts import RUNTIME_CONTRACT

NOW = "2026-10-11T12:00:00+08:00"


def bundle(deadline="2026-09-30"):
    texts = ["岗位名称", "招聘人数", "管理助理", "2", f"报名截止日期：{deadline}"]
    return {
        "id": "bundle-v1",
        "source_id": "source-1",
        "resource_id": "notice-1",
        "url": "https://example.org/notice",
        "status": "complete",
        "errors": [],
        "attachments": [],
        "page": {
            "format": "html",
            "status": "ok",
            "errors": [],
            "truncated": False,
            "title": "招聘公告",
            "blocks": [
                {"id": f"v1:b{i}", "text": text, "locator": f"p[{i}]"}
                for i, text in enumerate(texts)
            ],
            "tables": [
                {
                    "id": "v1:t0",
                    "locator": "table[0]",
                    "rows": [["岗位名称", "招聘人数"], ["管理助理", "2"]],
                    "cell_evidence": [["v1:b0", "v1:b1"], ["v1:b2", "v1:b3"]],
                }
            ],
            "links": [],
        },
    }


def cost_route(mode, *, category=None):
    """Deliberately fallible policy receipt: never an authority on position dates."""
    return {
        "category": category or ("fixed_expired" if mode == "quick" else "fixed_active"),
        "mode": mode,
        "reason": "Synthetic cost hint, not a fact",
        "policy_version": "test-v1",
        "evaluated_at": NOW,
    }


class FakeSandbox:
    """Only a callback stub: deliberately never eval/import any generated source."""

    def __init__(self):
        self.calls = []
        self.corrupt = False
        self.needs_detailed = False

    async def run_script(self, files, module, function, inputs):
        self.calls.append((copy.deepcopy(files), module, function, copy.deepcopy(inputs)))
        assert module in {"parser", "undated_parser"} and set(files) == {f"{module}.py"}
        assert (module, function) == ("parser", "parse")
        assert set(inputs) == {"bundle", "config"}
        value = extract_position_draft(inputs["bundle"], inputs["config"])
        if self.corrupt:
            value["positions"][0]["fields"]["title"]["tag"] = "unknown"
        if self.needs_detailed:
            value["coverage"]["needs_detailed"] = True
        return value


def test_xdog_actual_sdk_submit_result_caps_and_offline_boundary(monkeypatch):
    requests = []
    constructions = []
    responses = []
    submission_sequence = 0

    def submission(value="ok", *, mixed=False, stop_reason="toolUse"):
        nonlocal submission_sequence
        submission_sequence += 1
        # SDK snapshots require globally unique IDs across corrected turns.
        calls = [
            ToolCall(
                id=f"answer-{submission_sequence}",
                name="submit_result",
                arguments={"result": {"answer": value}},
            )
        ]
        if mixed:
            calls.append(
                ToolCall(
                    id=f"bad-{submission_sequence}",
                    name="submit_result",
                    arguments={"result": {"answer": 4}},
                )
            )
        return AssistantMessage(
            content=tuple(calls), stop_reason=stop_reason, usage=Usage(input=10, output=2)
        )

    class Provider:
        def models(self):
            return (Model(id="test/model", provider="test", context_window=100_000),)

        def model(self, name):
            return self.models()[0]

        def stream(self, model, context, options):
            requests.append((model, context, options))
            assert {tool.name for tool in context.tools} == {"submit_result"}
            assert '"required": ["answer"]' in ai.system_prompt_text(context.system_prompt)
            assert options.parallel_tool_calls is False
            message = responses.pop(0) if responses else submission()
            if isinstance(message, Exception):

                async def failed_stream():
                    raise message
                    yield  # make this an async iterator

                return EventStream(iterator=failed_stream())
            stream = EventStream()

            async def push():
                await stream.send(DoneEvent(stop_reason=message.stop_reason, message=message))
                stream.set_result(message)
                await stream.close()

            asyncio.create_task(push())
            return stream

    def provider(name):
        constructions.append(name)
        return Provider()

    monkeypatch.setattr(ai, "provider", provider)
    schema = {
        "type": "object",
        "required": ["answer"],
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
    }

    async def scenario():
        with pytest.raises(ModelError, match="opt-in"):
            await XdogModels().complete("classify", {}, schema)
        assert not constructions
        config = XdogConfig(
            provider="test",
            small_model="test/model",
            large_model="test/model",
            author_model="test/model",
            live_enabled=True,
            max_output_tokens=64,
            max_tokens=500,
        )
        models = XdogModels(config)
        assert await models.complete("classify", {"text": "公告"}, schema) == {"answer": "ok"}
        assert requests and all(request[2].max_tokens == 64 for request in requests)
        assert models.calls[0]["usage"]["total_tokens"] >= 12
        assert models.calls[0]["usage"]["reported"]
        assert models.calls[0]["status"] == "success"
        assert "公告" not in str(models.calls)
        assert models.calls[0]["usage"]["requests"] == 1
        assert models.calls[0]["usage"]["blocked_requests"] == 0
        assert models.calls[0]["usage"]["local_completions"] == 1
        assert len(requests) == 1  # No provider request just to acknowledge acceptance.

        # Reproduce malformed -> corrected submission without a third network call.
        responses.extend([submission(4), submission()])
        corrected = XdogModels(replace(config, max_requests=2))
        assert await corrected.complete("classify", {}, schema) == {"answer": "ok"}
        assert corrected.calls[0]["usage"]["requests"] == 2
        assert corrected.calls[0]["usage"]["blocked_requests"] == 0
        assert corrected.calls[0]["usage"]["local_completions"] == 1
        assert not responses

        # A rejected first submission must exhaust the hard one-request limit.
        responses.append(submission(4))
        rejected = XdogModels(replace(config, max_requests=1))
        before_requests = len(requests)
        with pytest.raises(ModelError) as error:
            await rejected.complete("classify", {}, schema)
        assert error.value.code == "MODEL_REQUEST_LIMIT"
        assert rejected.calls[0]["status"] == "failed"
        assert rejected.calls[0]["usage"]["requests"] == 1
        assert rejected.calls[0]["usage"]["blocked_requests"] == 1
        assert len(requests) == before_requests + 1
        assert error.value.metadata == rejected.calls[0]

        # 2.2's sink already contains the valid member of this mixed batch.
        # Do not treat it as final: the second submission was rejected. Raw flow
        # can return that stale value after budget/stream failure; the host cannot.
        responses.append(submission(mixed=True))
        stale = XdogModels(replace(config, max_requests=1))
        with pytest.raises(ModelError) as error:
            await stale.complete("classify", {}, schema)
        assert error.value.code == "MODEL_REQUEST_LIMIT"
        assert stale.calls[0]["status"] == "failed"
        assert stale.calls[0]["usage"]["requests"] == 1
        assert stale.calls[0]["usage"]["blocked_requests"] == 1
        assert stale.calls[0]["usage"]["request_attempts"] == 2
        assert stale.calls[0]["usage"]["local_completions"] == 0

        for termination, code in (
            (AssistantMessage(stop_reason="aborted", usage=Usage(input=3)), "MODEL_ABORTED"),
            (RuntimeError("secret-provider-diagnostic"), "MODEL_PROVIDER_ERROR"),
        ):
            responses.extend([submission(mixed=True), termination])
            interrupted = XdogModels(config)
            with pytest.raises(ModelError) as error:
                await interrupted.complete("classify", {}, schema)
            assert error.value.code == code
            assert interrupted.calls[0]["status"] == "failed"
            assert interrupted.calls[0]["usage"]["requests"] == 2
            assert interrupted.calls[0]["usage"]["blocked_requests"] == 0
            assert interrupted.calls[0]["usage"]["reported"]
            assert "secret-provider-diagnostic" not in str(interrupted.calls)
            assert not responses

        responses.append(submission(stop_reason="length"))
        truncated = XdogModels(config)
        with pytest.raises(ModelError) as error:
            await truncated.complete("classify", {}, schema)
        assert error.value.code == "MODEL_OUTPUT_LIMIT"
        assert truncated.calls[0]["status"] == "failed"

        capped = XdogModels(replace(config, max_tokens=1))
        with pytest.raises(ModelError) as error:
            await capped.complete("classify", {}, schema)
        assert error.value.code == "MODEL_TOKEN_LIMIT"
        assert capped.calls[0]["failure_origin"] == "host_budget"

        # The old 24k host default rejected real, completed USTC responses.
        # Default runs now count usage without imposing a token/time budget.
        defaults = XdogConfig()
        assert defaults.max_tokens is defaults.max_output_tokens is defaults.timeout_seconds is None
        large_reply = submission()
        large_reply = replace(large_reply, usage=Usage(input=39_000, output=20))
        responses.append(large_reply)
        unbudgeted = XdogModels(
            replace(config, max_tokens=None, max_output_tokens=None, timeout_seconds=None)
        )
        assert await unbudgeted.complete("extract", {}, schema) == {"answer": "ok"}
        assert requests[-1][2].max_tokens is None
        assert unbudgeted.calls[0]["usage"]["total_tokens"] == 39_020
        assert unbudgeted.calls[0]["usage"]["input_tokens"] == 39_000
        assert unbudgeted.calls[0]["limits"]["total_tokens"] is None
        assert unbudgeted.calls[0]["usage"]["duration_seconds"] > 0

        before = len(constructions)
        offline = XdogModels(stub=lambda *_: {"answer": "stub"})
        assert await offline.complete("classify", {}, schema) == {"answer": "stub"}
        assert len(constructions) == before
        bad = XdogModels(stub=lambda *_: {"answer": 4})
        with pytest.raises(ModelError) as error:
            await bad.complete("classify", {}, schema)
        assert error.value.code == "OUTPUT_SCHEMA"
        limited = XdogModels(replace(config, max_payload_bytes=1))
        with pytest.raises(ModelError) as error:
            await limited.complete("classify", {}, schema)
        assert error.value.code == "MODEL_INPUT_LIMIT"

    asyncio.run(scenario())


def test_recipe_flow_branches_sandbox_boundary_and_hostile_artifacts(monkeypatch):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("offline flow opened a provider"))
    sandbox = FakeSandbox()
    model_calls = []

    def stub(task, payload, schema, tier):
        model_calls.append((task, tier))
        assert task == "extract_position_draft" and schema == DRAFT_SCHEMA
        assert "previous_draft" in payload and "config" not in payload
        return extract_position_draft(payload["bundle"])

    runner = RecipeRunner(sandbox, XdogModels(stub=stub))
    candidate = template_candidate()
    assert set(candidate) == {"workflow", "parser_py", "undated_parser_py", "config"}
    # Validation parses AST only; the trusted test double never executes this code.
    candidate["parser_py"] = (
        "raise AssertionError('parent import forbidden')\n" + candidate["parser_py"]
    )
    assert runner.validate(candidate) == []

    async def scenario():
        events = []
        expired = await runner.run(candidate, bundle(), now_iso=NOW, on_event=events.append)
        assert expired["routing"]["mode"] == "quick"
        assert expired["positions"][0]["deadline_on"] == {"value": "2026-09-30", "tag": "source"}
        for event_type in (NodeStarted, NodeFinished):
            assert [e.node_id for e in events if isinstance(e, event_type)] == [
                "parse",
                "detail",
                "finish",
            ]
        assert expired["schema_version"] == CONTRACT
        assert PositionFacts.from_dict(expired["positions"][0]).fields["headcount"].value == 2
        assert [item[1] for item in sandbox.calls] == ["parser"]
        assert not model_calls
        current = await runner.run(candidate, bundle("2026-10-31"), now_iso=NOW)
        assert current["routing"]["mode"] == "detailed"
        assert not model_calls  # Supported script needs no expensive model, even when active.
        snapshot = copy.deepcopy(current)
        # Old cached validity is never a job-fact input. Re-run the parser at every clock.
        cached = {
            **copy.deepcopy(current),
            "validity": {"status": "expired", "deadline": "1900-01-01"},
        }
        for clock in (
            "2026-10-12T12:00:00+08:00",
            "2026-10-31T23:59:59.999999+08:00",
            "2026-11-01T00:00:00+08:00",
        ):
            before = len(sandbox.calls)
            result = await runner.run(
                candidate, bundle("2026-10-31"), now_iso=clock, previous_result=cached
            )
            assert result["positions"] == current["positions"]
            assert [call[1] for call in sandbox.calls[before:]] == ["parser"]
            assert "validity" not in result and "quality_tier" not in result
        assert current == snapshot and cached["validity"]["deadline"] == "1900-01-01"
        # A wrong cost decision still cannot mutate known jobs, precise dates or provenance.
        for mode in ("quick", "detailed"):
            result = await runner.run(
                candidate, bundle("2026-10-31"), now_iso=NOW, routing=cost_route(mode)
            )
            assert result["positions"] == current["positions"]
        sandbox.needs_detailed = True
        quick = await runner.run(candidate, bundle(), now_iso=NOW, routing=cost_route("quick"))
        assert not quick["execution"]["model_used"] and not model_calls
        detailed = await runner.run(
            candidate, bundle(), now_iso=NOW, routing=cost_route("detailed")
        )
        assert detailed["positions"] == quick["positions"]
        assert detailed["execution"]["model_used"]
        assert model_calls == [("extract_position_draft", "flow")]
        sandbox.needs_detailed = False
        sandbox.corrupt = True
        with pytest.raises(RecipeError) as error:
            await runner.run(candidate, bundle(), now_iso=NOW)
        assert error.value.code == "DRAFT_INVALID"
        assert {e["code"] for e in error.value.errors} == {"POSITION_DRAFT_SCHEMA"}
        assert error.value.rejected_result["positions"][0]["fields"]["title"]["tag"] == "unknown"
        sandbox.corrupt = False
        hostile_changes = [
            ("root", {"tools": {"danger": "os:system"}}),
            ("root", {"schedule": {"mode": "timer", "every": "1s"}}),
            ("node", {"run": "os:system"}),
            ("node", {"code": "import os; os.system('false')"}),
            ("node", {"skills": ["unreviewed"]}),
            ("node", {"type": "agent", "backend": "codex-cli"}),
            ("node", {"type": "subflow", "subflow": "../external.json"}),
            ("edge", {"loop": {"max": 500}}),
            ("edge", {"map": ["invalid"]}),
        ]
        before = len(sandbox.calls)
        for target, change in hostile_changes:
            hostile = copy.deepcopy(candidate)
            location = hostile["workflow"]
            if target == "node":
                location = location["nodes"][0]
            elif target == "edge":
                location = location["edges"][0]
            location.update(change)
            with pytest.raises(RecipeError) as error:
                await runner.run(hostile, bundle(), now_iso=NOW)
            assert error.value.code == "RECIPE_INVALID"
        for code in (
            "async def parse(ctx, bundle, config):\n    return {}",
            "def parse(ctx, bundle, now_iso, config):\n    return {}",
        ):
            assert runner.validate({**candidate, "parser_py": code})[0]["code"] == "RECIPE_INVALID"
        disconnected = copy.deepcopy(candidate)
        disconnected["workflow"]["edges"] = [
            e for e in disconnected["workflow"]["edges"] if e["to"] != "detail"
        ]
        with pytest.raises(RecipeError) as error:
            await runner.run(disconnected, bundle(), now_iso=NOW)
        assert error.value.code == "RECIPE_INVALID"
        assert len(sandbox.calls) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("deadline", ["2026-09-30", "2026-10-31"])
def test_recipe_accepts_complete_tagged_facts_without_position_evidence(deadline):
    class TaggedSandbox(FakeSandbox):
        async def run_script(self, files, module, function, inputs):
            draft = await super().run_script(files, module, function, inputs)
            position = draft["positions"][0]
            position["fields"]["work_location"] = {"value": "合成推断地点", "tag": "ai_inferred"}
            position["conditions"] = [
                {
                    "id": "c1",
                    "type": "education",
                    "operator": "equals",
                    "scope": "position",
                    "value": {"level": "bachelor"},
                    "tag": "ai_inferred",
                }
            ]
            position["condition_logic"] = {"condition_id": "c1"}
            return draft

    models = XdogModels(stub=lambda *_: pytest.fail("Supported parser reached model"))
    result = asyncio.run(
        RecipeRunner(TaggedSandbox(), models).run(
            template_candidate(), bundle(deadline), now_iso=NOW
        )
    )
    assert result["schema_version"] == CONTRACT
    facts = PositionFacts.from_dict(result["positions"][0])
    assert facts.fields["title"].value == "管理助理"
    assert facts.fields["work_location"].tag == "ai_inferred"
    assert facts.conditions[0].fact.tag == "ai_inferred"
    assert facts.fields["employer"].as_dict() == {"value": None, "tag": "unknown"}
    assert facts.fields["deadline_on"].as_dict() == {"value": deadline, "tag": "source"}
    assert facts.as_dict() == result["positions"][0] and "evidence" not in result["positions"][0]
    assert "validity" not in result and "quality_tier" not in result
    assert not validate_output(bundle(deadline), result) and not models.calls


@pytest.mark.parametrize("mode", ["quick", "detailed"])
@pytest.mark.parametrize(
    "fault",
    [
        "envelope_version",
        "position_version",
        "missing_key",
        "invalid_tag",
        "unknown_with_value",
        "orphan_logic",
        "duplicate_key",
        "old_final_output",
    ],
)
def test_recipe_rejects_invalid_drafts_on_both_routes(mode, fault):
    class InvalidSandbox(FakeSandbox):
        async def run_script(self, files, module, function, inputs):
            draft = await super().run_script(files, module, function, inputs)
            position = draft["positions"][0]
            if fault == "envelope_version":
                draft.pop("schema_version")
            elif fault == "position_version":
                position["schema_version"] = "position-facts-v2"
            elif fault == "missing_key":
                position.pop("key")
            elif fault == "invalid_tag":
                position["fields"]["title"]["tag"] = "verified"
            elif fault == "unknown_with_value":
                position["fields"]["title"]["tag"] = "unknown"
            elif fault == "orphan_logic":
                position["condition_logic"] = {"condition_id": "missing"}
            elif fault == "duplicate_key":
                draft["positions"].append(copy.deepcopy(position))
            elif fault == "old_final_output":
                draft = {
                    "schema_version": "position-facts-v2",
                    "status": "success",
                    "positions": [],
                    "errors": [],
                }
            return draft

    # Detailed failures are owned by the draft validator too, not an old final-output schema.
    models = XdogModels(stub=lambda *_: pytest.fail("Invalid script draft bought model fallback"))
    runner = RecipeRunner(InvalidSandbox(), models)
    with pytest.raises(RecipeError) as error:
        asyncio.run(
            runner.run(template_candidate(), bundle(), now_iso=NOW, routing=cost_route(mode))
        )
    assert error.value.code == "DRAFT_INVALID"
    assert error.value.rejected_result is not None
    assert {item["code"] for item in error.value.errors} == {"POSITION_DRAFT_SCHEMA"}
    assert error.value.rejected_origin == "script_draft"
    assert not models.calls
    assert "rejected_result" not in error.value.as_dict()


@pytest.mark.parametrize("mode", ["quick", "detailed"])
@pytest.mark.parametrize("field", ["deadline_on", "validity", "application_period"])
def test_recipe_rejects_job_fact_authority_in_cost_receipts(mode, field):
    sandbox = FakeSandbox()
    models = XdogModels(stub=lambda *_: pytest.fail("Invalid routing reached model"))
    with pytest.raises(RecipeError) as error:
        asyncio.run(
            RecipeRunner(sandbox, models).run(
                template_candidate(),
                bundle(),
                now_iso=NOW,
                routing={**cost_route(mode), field: "hostile"},
            )
        )
    assert error.value.code == "ROUTING_INVALID"
    assert not models.calls and not sandbox.calls


def terminating_author_stream(factory, submissions):
    """A scripted provider ending after rejected fixtures, not a host turn cap."""

    def create(payload, workspace):
        upstream = factory(payload, workspace)
        dispatched = 0

        def stream(model, context, options):
            nonlocal dispatched
            dispatched += 1
            if dispatched > submissions:
                return EventStream.empty(
                    AssistantMessage(
                        stop_reason="error", error_message="synthetic-provider-termination"
                    )
                )
            return upstream(model, context, options)

        return stream

    return create


def test_author_native_loop_callbacks_and_provider_termination(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("author contacted a provider"))
    calls = []
    persisted = []
    candidate = template_candidate()
    broken = copy.deepcopy(candidate)
    broken["workflow"]["nodes"][0]["run"] = "unsafe:execute"

    def stub(task, payload, schema, tier):
        calls.append((task, payload, tier))
        assert len(payload["samples"]) == 1
        assert tier == "author"
        assert "extraction" in payload["schemas"]
        if len(calls) == 1:
            return broken
        assert payload["feedback"][0]["code"] == "RECIPE_INVALID"
        assert payload["previous_candidate"] == broken
        assert len(persisted) == 1
        return candidate

    async def scenario():
        factory = SyntheticAuthorStreamFactory(stub)
        models = XdogModels(stub=factory.non_author_stub, author_stream_factory=factory)
        sandbox = FakeSandbox()
        workspace = AuthorWorkspace(tmp_path / "success", sandbox=sandbox)
        runner = RecipeRunner(sandbox, models)

        async def on_attempt(attempt):
            await asyncio.sleep(0)
            assert attempt["number"] == len(calls)
            persisted.append(copy.deepcopy(attempt))

        result = await RecipeAuthor(models, runner, workspace=workspace).author(
            [bundle()], now_iso=NOW, on_attempt=on_attempt
        )
        assert persisted == result["attempts"]
        assert result["candidate"] == candidate
        assert result["digest"] == candidate_digest(candidate)
        assert len(result["attempts"]) == 2
        assert result["attempts"][0]["errors"] and not result["attempts"][1]["errors"]
        assert [item[0] for item in calls] == ["author_source_recipe", "repair_source_recipe"]
        assert len(models.calls) == 1  # One SDK run, not one model.complete per candidate.
        assert models.calls[0]["engine"] == "xdog-agent-sdk"
        assert models.calls[0]["usage"]["turns"] == 2
        assert models.calls[0]["tool_calls"]["submit_result"] == 2
        assert workspace.load_candidate() == candidate
        assert {path.name for path in workspace.workspace.iterdir() if path.is_file()} == {
            "workflow.json",
            "parser.py",
            "undated_parser.py",
            "config.json",
        }
        assert workspace.workspace.joinpath("parser.py").read_text() == candidate["parser_py"]
        assert json.loads((workspace.history / "runs/1.json").read_text())["status"] == "success"
        assert candidate["config"] == {
            "runtime_contract": RUNTIME_CONTRACT,
            "position_contract": CONTRACT,
        }

        failing_factory = SyntheticAuthorStreamFactory(lambda *_: broken)
        failing_models = XdogModels(
            stub=failing_factory.non_author_stub,
            author_stream_factory=terminating_author_stream(failing_factory, 2),
        )
        failing_workspace = AuthorWorkspace(tmp_path / "failed", sandbox=FakeSandbox())
        failing = RecipeAuthor(
            failing_models, RecipeRunner(FakeSandbox(), failing_models), workspace=failing_workspace
        )
        with pytest.raises(RecipeError) as error:
            await failing.author([bundle()], now_iso=NOW)
        assert error.value.code == "MODEL_PROVIDER_ERROR"
        assert len(error.value.attempts) == len(failing_factory.calls) == 2
        assert len(failing_models.calls) == 1
        assert failing_models.calls[0]["usage"]["turns"] == 3
        assert all(attempt["errors"] for attempt in error.value.attempts)
        run = json.loads((failing_workspace.history / "runs/1.json").read_text())
        assert run["status"] == "failed" and run["usage"]["turns"] == 3
        assert len(list((failing_workspace.history / "attempts").glob("*.json"))) == 2
        with pytest.raises(RecipeError) as error:
            await failing.author([bundle()] * 4, now_iso=NOW)
        assert error.value.code == "AUTHORING_SAMPLES"
        assert len(failing_models.calls) == 1 and len(failing_factory.calls) == 2

        callback_failure = RuntimeError("attempt persistence failed")
        callback_rounds = []

        async def fail_persistence(attempt):
            callback_rounds.append(attempt["number"])
            raise callback_failure

        interrupted_factory = SyntheticAuthorStreamFactory(lambda *_: broken)
        interrupted_models = XdogModels(
            stub=interrupted_factory.non_author_stub, author_stream_factory=interrupted_factory
        )
        interrupted_workspace = AuthorWorkspace(tmp_path / "interrupted", sandbox=FakeSandbox())
        interrupted = RecipeAuthor(
            interrupted_models,
            RecipeRunner(FakeSandbox(), interrupted_models),
            workspace=interrupted_workspace,
        )
        with pytest.raises(RuntimeError) as error:
            await interrupted.author([bundle()], now_iso=NOW, on_attempt=fail_persistence)
        assert error.value is callback_failure
        assert callback_rounds == [1]
        assert len(interrupted_models.calls) == len(interrupted_factory.calls) == 1
        attempt = json.loads((interrupted_workspace.history / "attempts/1.json").read_text())
        assert attempt["candidate"] == broken and attempt["errors"]
        run = json.loads((interrupted_workspace.history / "runs/1.json").read_text())
        assert run["status"] == "failed" and run["error_code"] == "AUTHOR_HOST_FAILURE"

        # Unsupported, valid drafts take the configured detailed fallback through actual Flow.
        def current_stub(task, payload, *_):
            if task == "extract_position_draft":
                return extract_position_draft(payload["bundle"])
            return candidate

        current_factory = SyntheticAuthorStreamFactory(current_stub)
        current_models = XdogModels(
            stub=current_factory.non_author_stub,
            author_stream_factory=terminating_author_stream(current_factory, 2),
        )
        needs_detail = FakeSandbox()
        needs_detail.needs_detailed = True
        current_workspace = AuthorWorkspace(tmp_path / "current", sandbox=needs_detail)
        current = await RecipeAuthor(
            current_models, RecipeRunner(needs_detail, current_models), workspace=current_workspace
        ).author([bundle("2026-10-31")], now_iso=NOW)
        assert not current["attempts"][0]["errors"]
        assert current["attempts"][0]["samples"][0]["workflow_tested"]
        assert [call[1] for call in needs_detail.calls] == ["parser"]
        assert any(call["task"] == "extract_position_draft" for call in current_models.calls)

    asyncio.run(scenario())


def test_author_more_than_two_candidates_system_skill_and_persistent_recovery(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("author contacted a provider"))
    official_skill = files("xdog.flow").joinpath("skills/flow/SKILL.md").read_text()
    candidate = template_candidate()
    broken = copy.deepcopy(candidate)
    broken["workflow"]["nodes"][0]["run"] = "unsafe:execute"
    config = XdogConfig(
        timeout_seconds=1e-9,
        max_tokens=1,
        max_output_tokens=1,
        max_requests=1,
        max_payload_bytes=1,
    )
    assert config.author_model == "gpt-6.1-sol"
    assert XdogConfig().max_requests == 30
    assert not hasattr(config, "author_max_turns")

    async def scenario():
        planned = [broken, broken, candidate]
        factory = SyntheticAuthorStreamFactory(lambda *_: planned.pop(0))
        models = XdogModels(config, stub=factory.non_author_stub, author_stream_factory=factory)
        workspace = AuthorWorkspace(tmp_path / "three-candidates", sandbox=FakeSandbox())
        result = await RecipeAuthor(
            models, RecipeRunner(FakeSandbox(), models), workspace=workspace
        ).author([bundle()], now_iso=NOW)
        assert len(result["attempts"]) == len(factory.calls) == 3
        assert [bool(item["errors"]) for item in result["attempts"]] == [True, True, False]
        assert len(models.calls) == 1
        record = models.calls[0]
        assert record["model"] == "gpt-6.1-sol"
        assert record["usage"]["turns"] == 3 and record["usage"]["blocked_requests"] == 0
        assert record["usage"]["local_completions"] == 1
        assert record["usage"]["total_tokens"] > config.max_tokens
        assert record["tool_calls"] == {"submit_result": 3}
        for call in factory.calls:
            assert call["model"] == "gpt-6.1-sol"
            assert call["options"].max_tokens is None
            system = ai.system_prompt_text(call["context"].system_prompt)
            assert system.count(load_packaged_skill("flow").content) == 1
            assert "<official_xdog_flow_skill>" not in system
            assert "<progress_reporting>" not in system
            assert "remaining turns" not in system
            assert {tool.name for tool in call["context"].tools} == {
                "bash",
                "filesystem",
                "flow",
                "submit_result",
            }
            for message in call["context"].messages:
                if message.role == "user":
                    assert official_skill not in str(message.content)
        assert (workspace.input / "SKILL.md").read_text() == official_skill
        assert len(list((workspace.history / "attempts").glob("*.json"))) == 3

        # An explicit new run can recover a provider failure; work/history persist.
        retry_factory = SyntheticAuthorStreamFactory(lambda *_: broken)
        retry_models = XdogModels(
            config,
            stub=retry_factory.non_author_stub,
            author_stream_factory=terminating_author_stream(retry_factory, 2),
        )
        root = tmp_path / "recoverable-source"
        retry_workspace = AuthorWorkspace(root, sandbox=FakeSandbox())
        with pytest.raises(RecipeError) as error:
            await RecipeAuthor(
                retry_models, RecipeRunner(FakeSandbox(), retry_models), workspace=retry_workspace
            ).author([bundle()], now_iso=NOW)
        assert error.value.code == "MODEL_PROVIDER_ERROR" and len(error.value.attempts) == 2
        prior_attempts = {
            path.name: path.read_bytes()
            for path in (retry_workspace.history / "attempts").glob("*.json")
        }
        prior_run = (retry_workspace.history / "runs/1.json").read_bytes()
        assert retry_workspace.load_candidate() == broken
        (retry_workspace.workspace / "notes.txt").write_text("Keep these source-specific notes.")

        repaired_factory = SyntheticAuthorStreamFactory(lambda *_: candidate)
        repaired_models = XdogModels(
            config,
            stub=repaired_factory.non_author_stub,
            author_stream_factory=repaired_factory,
        )
        reopened = AuthorWorkspace(root, sandbox=FakeSandbox())
        result = await RecipeAuthor(
            repaired_models, RecipeRunner(FakeSandbox(), repaired_models), workspace=reopened
        ).author([bundle()], now_iso=NOW)
        assert len(result["attempts"]) == 1 and not result["attempts"][0]["errors"]
        assert repaired_factory.runs[0]["initial_candidate"] == broken
        assert repaired_factory.runs[0]["initial_status"]["latest_run"]["sequence"] == 1
        assert repaired_factory.calls[0]["task"] == "repair_source_recipe"
        assert repaired_factory.calls[0]["payload"]["previous_candidate"] == broken
        assert repaired_factory.calls[0]["payload"]["feedback"]
        assert repaired_models.calls[0]["usage"]["turns"] == 1
        assert reopened.load_candidate() == candidate
        assert (reopened.workspace / "notes.txt").read_text() == "Keep these source-specific notes."
        for name, content in prior_attempts.items():
            assert (reopened.history / "attempts" / name).read_bytes() == content
        assert (reopened.history / "runs/1.json").read_bytes() == prior_run
        assert len(list((reopened.history / "attempts").glob("*.json"))) == 3
        runs = [
            json.loads(path.read_text())
            for path in sorted((reopened.history / "runs").glob("*.json"))
        ]
        assert [run["status"] for run in runs] == ["failed", "success"]
        assert [run["usage"]["turns"] for run in runs] == [3, 1]

        # A prose-only reply is not completion. Continue the same SDK Agent and
        # turn counter; do not silently start a fresh run or accept existing files.
        for eventually_submit in (True, False):
            fixture = SyntheticAuthorStreamFactory(lambda *_: candidate)
            seen = []

            def prose_first(payload, work, _fixture=fixture, _seen=seen, _submit=eventually_submit):
                submit_stream = _fixture(payload, work)

                def stream(model, context, options):
                    _seen.append(context)
                    if len(_seen) > 1:
                        if _submit:
                            return submit_stream(model, context, options)
                        return EventStream.empty(
                            AssistantMessage(stop_reason="error", error_message="synthetic end")
                        )
                    return EventStream.empty(
                        AssistantMessage(
                            content=(TextContent(text="I have not submitted the files yet."),),
                            stop_reason="stop",
                        )
                    )

                return stream

            prose_models = XdogModels(
                config,
                stub=fixture.non_author_stub,
                author_stream_factory=prose_first,
            )
            prose_workspace = AuthorWorkspace(tmp_path / f"prose-{eventually_submit}")
            author = RecipeAuthor(
                prose_models, RecipeRunner(FakeSandbox(), prose_models), workspace=prose_workspace
            )
            if eventually_submit:
                assert (await author.author([bundle()], now_iso=NOW))["candidate"] == candidate
            else:
                with pytest.raises(RecipeError) as error:
                    await author.author([bundle()], now_iso=NOW)
                assert error.value.code == "MODEL_PROVIDER_ERROR" and error.value.attempts == []
            assert len(seen) == prose_models.calls[0]["usage"]["turns"] == 2
            assert len(prose_models.calls) == 1
            assert prose_models.calls[0]["agent_note"]["untrusted"]

    asyncio.run(scenario())


def test_author_unlimited_turns_bounded_progress_and_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    candidate = template_candidate()
    broken = copy.deepcopy(candidate)
    broken["workflow"]["nodes"][0]["run"] = "unsafe:execute"
    planned = [broken, candidate]
    fixture = SyntheticAuthorStreamFactory(lambda *_: planned.pop(0))
    dispatched, updates = [], []
    secrets = (
        "sk-provider-private",
        "private.example",
        "RAW-PROSE",
        "UNTRUSTED-TOOL",
        "RAW-CALL-ID",
    )
    report_message = (
        "Inspecting inputs. api_key=sk-provider-private "
        "https://private.example/path?token=hidden\n" + "Checking materials. " * 100
    )

    def factory(payload, workspace):
        submit = fixture(payload, workspace)

        def stream(model, context, options):
            dispatched.append(options)
            index = len(dispatched)
            tools = {tool.name for tool in context.tools}
            assert tools == {"bash", "filesystem", "flow", "submit_result", "report_progress"}
            assert "<progress_reporting>" in ai.system_prompt_text(context.system_prompt)
            if index == 1:
                call = ToolCall(
                    id="RAW-CALL-ID",
                    name="report_progress",
                    arguments={"phase": "success", "message": "I approve myself."},
                )
            elif index == 2:
                call = ToolCall(
                    id="RAW-CALL-ID",
                    name="report_progress",
                    arguments={"phase": "reading", "message": report_message},
                )
            elif index == 3:
                # Tool-start telemetry sees malformed args before SDK validation.
                call = ToolCall(
                    id="RAW-CALL-ID",
                    name="filesystem",
                    arguments={"action": "read", "path": ["RAW-PROSE"]},
                )
            elif index <= AUTHOR_DIAGNOSTIC_LIMIT + 4:
                # Distinct, untrusted tool names must not grow the counts map or
                # leak raw argument/error text into persistence callbacks.
                call = ToolCall(
                    id="RAW-CALL-ID",
                    name=f"UNTRUSTED-TOOL-{index}",
                    arguments={"command": "RAW-PROSE", "api_key": "sk-provider-private"},
                )
            elif index == AUTHOR_DIAGNOSTIC_LIMIT + 5:
                return EventStream.empty(
                    AssistantMessage(content=(TextContent(text="RAW-PROSE" * 1000),))
                )
            else:
                return submit(model, context, options)
            return EventStream.empty(AssistantMessage(content=(call,), stop_reason="toolUse"))

        return stream

    async def scenario():
        callback_running = False

        async def on_update(event):
            nonlocal callback_running
            assert not callback_running, "Progress callbacks must be ordered, never concurrent"
            callback_running = True
            await asyncio.sleep(0)
            encoded = json.dumps(event)
            assert len(encoded.encode()) < 2000
            assert not any(secret in encoded for secret in secrets)
            assert all(key not in event for key in ("args", "content", "system_prompt"))
            updates.append(copy.deepcopy(event))
            # The callback owns its dict; mutating it cannot corrupt run metadata.
            if "usage" in event:
                event["usage"]["turns"] = -1
            if event["event"] == "agent_report":
                event["message"] = "callback mutation"
            callback_running = False

        config = XdogConfig(
            timeout_seconds=1e-9,
            max_tokens=1,
            max_output_tokens=1,
            max_requests=1,
            max_payload_bytes=1,
        )
        models = XdogModels(config, author_stream_factory=factory)
        workspace = AuthorWorkspace(tmp_path / "source")
        result = await RecipeAuthor(
            models, RecipeRunner(FakeSandbox(), models), workspace=workspace
        ).author([bundle()], now_iso=NOW, on_update=on_update)
        assert result["candidate"] == candidate and len(result["attempts"]) == 2
        record = models.calls[0]
        assert record["usage"]["turns"] == len(dispatched) == AUTHOR_DIAGNOSTIC_LIMIT + 7
        assert record["usage"]["turns"] > 30
        assert record["usage"]["blocked_requests"] == 0
        assert record["usage"]["total_tokens"] > config.max_tokens
        assert all(options.max_tokens is None for options in dispatched)
        assert record["tool_error_count"] > AUTHOR_DIAGNOSTIC_LIMIT
        assert len(record["tool_activity"]) == len(record["tool_errors"]) == AUTHOR_DIAGNOSTIC_LIMIT
        assert record["tool_calls"] == {
            "report_progress": 2,
            "filesystem": 1,
            "unknown": AUTHOR_DIAGNOSTIC_LIMIT + 1,
            "submit_result": 2,
        }
        assert not any(secret in json.dumps(record) for secret in secrets)
        assert record["agent_note"]["characters"] == len("RAW-PROSE" * 1000)
        assert [event["sequence"] for event in updates] == list(range(1, len(updates) + 1))
        assert {event["event"] for event in updates} == {
            "started",
            "turn",
            "tool_start",
            "tool_end",
            "agent_report",
            "final",
        }
        assert updates[0]["event"] == "started"
        assert updates[-1]["event"] == "final" and updates[-1]["status"] == "success"
        assert updates[-1]["scope"] == "candidate_validation"
        assert updates[-1]["usage"]["turns"] == len(dispatched)
        reports = [event for event in updates if event["event"] == "agent_report"]
        assert len(reports) == 1
        assert reports[0]["phase"] == "reading" and reports[0]["untrusted"]
        assert "status" not in reports[0]
        assert len(reports[0]["message"]) == AUTHOR_REPORT_MESSAGE_LIMIT
        assert record["agent_report"]["message"] == reports[0]["message"]
        assert reports[0]["message"].startswith("Inspecting inputs. [redacted credential]")
        assert "[redacted URL]" in reports[0]["message"]
        report_index = updates.index(reports[0])
        assert updates[report_index - 1]["event"] == "tool_end"
        run = json.loads((workspace.history / "runs/1.json").read_text())
        assert run["status"] == "success" and run["usage"]["turns"] == len(dispatched)

    asyncio.run(scenario())


def test_author_blocked_report_stops_without_acceptance(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    requests, updates = [], []

    def factory(*_):
        def stream(model, context, options):
            requests.append(options)
            assert len(requests) == 1, "A blocked report must not buy another provider turn"
            return EventStream.empty(
                AssistantMessage(
                    content=(
                        ToolCall(
                            id="blocked",
                            name="report_progress",
                            arguments={"phase": "blocked", "message": "Host schema needs review."},
                        ),
                        ToolCall(
                            id="late", name="submit_result", arguments={"result": {"ready": True}}
                        ),
                    ),
                    stop_reason="toolUse",
                )
            )

        return stream

    async def scenario():
        async def on_update(event):
            updates.append(event)

        models = XdogModels(author_stream_factory=factory)
        workspace = AuthorWorkspace(tmp_path / "source")
        with pytest.raises(RecipeError) as error:
            await RecipeAuthor(
                models, RecipeRunner(FakeSandbox(), models), workspace=workspace
            ).author([bundle()], now_iso=NOW, on_update=on_update)
        assert error.value.code == "AUTHOR_REPORTED_BLOCKER" and error.value.attempts == []
        assert len(requests) == models.calls[0]["usage"]["turns"] == 1
        assert models.calls[0]["agent_report"]["phase"] == "blocked"
        assert workspace.load_candidate() == template_candidate()
        assert not list((workspace.history / "attempts").glob("*.json"))
        assert updates[-1]["event"] == "final" and updates[-1]["status"] == "failed"
        assert updates[-1]["error_code"] == "AUTHOR_REPORTED_BLOCKER"
        assert all(event.get("status") != "success" for event in updates)

    asyncio.run(scenario())


def test_author_task_cancellation_aborts_provider_and_reports_final(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))

    async def scenario():
        dispatched, aborted, updates = asyncio.Event(), asyncio.Event(), []

        def factory(*_):
            def stream(model, context, options):
                final = asyncio.get_running_loop().create_future()

                async def wait_for_abort():
                    dispatched.set()
                    await options.cancel.wait()
                    aborted.set()
                    message = AssistantMessage(stop_reason="aborted")
                    final.set_result(message)
                    yield DoneEvent(stop_reason="aborted", message=message)

                return EventStream(iterator=wait_for_abort(), result_future=final)

            return stream

        async def on_update(event):
            updates.append(event)

        models = XdogModels(author_stream_factory=factory)
        workspace = AuthorWorkspace(tmp_path / "source")
        task = asyncio.create_task(
            RecipeAuthor(models, RecipeRunner(FakeSandbox(), models), workspace=workspace).author(
                [bundle()], now_iso=NOW, on_update=on_update
            )
        )
        try:
            await asyncio.wait_for(dispatched.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert aborted.is_set()
        assert models.calls[0]["status"] == "cancelled"
        assert models.calls[0]["usage"]["turns"] == 1
        assert updates[-1]["event"] == "final" and updates[-1]["status"] == "cancelled"
        assert updates[-1]["error_code"] == "MODEL_CANCELLED"
        run = json.loads((workspace.history / "runs/1.json").read_text())
        assert run["status"] == "cancelled"
        assert not list((workspace.history / "attempts").glob("*.json"))

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_at", ["started", "tool_start", "agent_report", "final"])
def test_author_update_persistence_failure_is_fatal(monkeypatch, tmp_path, fail_at):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    fixture = SyntheticAuthorStreamFactory(lambda *_: template_candidate())
    updates = []
    failure = RuntimeError("private-persistence-diagnostic")

    def factory(payload, workspace):
        submit = fixture(payload, workspace)
        reported = False

        def stream(model, context, options):
            nonlocal reported
            if not reported:
                reported = True
                return EventStream.empty(
                    AssistantMessage(
                        content=(
                            ToolCall(
                                id="progress",
                                name="report_progress",
                                arguments={"phase": "testing", "message": "Checking the workflow."},
                            ),
                        ),
                        stop_reason="toolUse",
                    )
                )
            return submit(model, context, options)

        return stream

    async def scenario():
        async def on_update(event):
            updates.append(event)
            if event["event"] == fail_at:
                raise failure

        models = XdogModels(author_stream_factory=factory)
        workspace = AuthorWorkspace(tmp_path / "source")
        with pytest.raises(RuntimeError) as error:
            await RecipeAuthor(
                models, RecipeRunner(FakeSandbox(), models), workspace=workspace
            ).author([bundle()], now_iso=NOW, on_update=on_update)
        assert error.value is failure
        record = models.calls[0]
        assert record["status"] == "failed" and record["error_code"] == "AUTHOR_HOST_FAILURE"
        assert record["update_error_code"] == "AUTHOR_UPDATE_FAILURE"
        assert updates[-1]["event"] == fail_at  # No repeated calls to a failed writer.
        assert "private-persistence-diagnostic" not in json.dumps(record)
        run = json.loads((workspace.history / "runs/1.json").read_text())
        assert run["status"] == "failed" and run["error_code"] == "AUTHOR_HOST_FAILURE"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "termination, code",
    [
        ("error", "MODEL_PROVIDER_ERROR"),
        ("length", "MODEL_OUTPUT_LIMIT"),
        ("aborted", "MODEL_ABORTED"),
        ("exception", "MODEL_PROVIDER_ERROR"),
    ],
)
def test_author_provider_failure_remains_redacted_in_updates(
    monkeypatch, tmp_path, termination, code
):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    updates = []

    def factory(*_):
        def stream(*_):
            if termination == "exception":
                raise RuntimeError("private-provider-diagnostic")
            return EventStream.empty(
                AssistantMessage(
                    stop_reason=termination,
                    content=(TextContent(text="private-provider-diagnostic"),),
                )
            )

        return stream

    async def scenario():
        async def on_update(event):
            updates.append(event)
            if event["event"] == "final" and termination == "exception":
                raise RuntimeError("private-persistence-diagnostic")

        models = XdogModels(author_stream_factory=factory)
        workspace = AuthorWorkspace(tmp_path / "source")
        with pytest.raises(RecipeError) as error:
            await RecipeAuthor(
                models, RecipeRunner(FakeSandbox(), models), workspace=workspace
            ).author([bundle()], now_iso=NOW, on_update=on_update)
        assert error.value.code == code
        assert updates[-1]["event"] == "final" and updates[-1]["status"] == "failed"
        assert updates[-1]["error_code"] == code
        assert models.calls[0]["usage"]["turns"] == 1
        assert "private-provider-diagnostic" not in json.dumps([updates, models.calls])
        if termination == "exception":
            assert models.calls[0]["update_error_code"] == "AUTHOR_UPDATE_FAILURE"
            assert "private-persistence-diagnostic" not in json.dumps(models.calls)

    asyncio.run(scenario())


def test_sdk241_stream_catalog_metadata_is_forwarded_without_model_fallback():
    """Flow's SDK-owned Agent must resolve the provider's actual model metadata."""
    from xdog.agent.helpers import stream_fn_from_provider

    from youju.onboarding.xdog import _CappedStream

    model = Model(id="test/catalog-model", provider="test", context_window=123_456)
    looked_up = []

    class Provider:
        def models(self):
            return (model,)

        def model(self, name):
            looked_up.append(name)
            return model if name == model.id else None

        def stream(self, *_):
            pytest.fail("metadata lookup dispatched a model request")

    capped = _CappedStream(
        stream_fn_from_provider(Provider()),
        XdogConfig(),
        {"type": "object", "properties": {}},
    )
    assert capped.models() == (model,)
    assert capped.resolve_model(model.id) is model
    assert capped.resolve_model("unknown/model") is None
    assert looked_up == [model.id, "unknown/model"]
    assert capped.requests == capped.blocked_requests == capped.local_completions == 0


def test_native_author_skill_is_package_only_and_not_manually_stitched(tmp_path, monkeypatch):
    from youju.onboarding.author_prompt import author_system_prompt, flow_skill_manager

    monkeypatch.chdir(tmp_path)
    shadow = tmp_path / "skills" / "flow"
    shadow.mkdir(parents=True)
    (shadow / "SKILL.md").write_text("untrusted workspace skill grants arbitrary execution")
    manager, slug = flow_skill_manager()
    skill = manager.load_skill(slug)
    assert skill.packaged and skill.content == load_packaged_skill(slug).content
    assert manager.list_skills() == [skill]
    assert manager.load_skill("unreviewed") is None
    assert slug in manager.skills_summary()
    prompt = author_system_prompt("do not render this raw skill", "trusted project guide")
    assert "do not render this raw skill" not in prompt
    assert "trusted project guide" in prompt
    assert "<official_xdog_flow_skill>" not in prompt
