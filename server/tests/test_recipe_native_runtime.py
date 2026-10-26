"""Real xdog Agent/skills/snapshots/compaction; explicit provider/tool stubs only."""

import asyncio
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_xdog_recipes import NOW, FakeSandbox, bundle
from xdog.agent import CompactionConfig
from xdog.agent.skills import load_packaged_skill
from xdog.agent.snapshots import decode_snapshot
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall, ToolResultMessage, Usage
from xdog.ai.utils.event_stream import EventStream

from youju import create_app
from youju.onboarding.recipes import template_candidate
from youju.onboarding.sandbox import Sandbox
from youju.onboarding.store import digest, json_bytes
from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.recipe_service import RecipeService, ServiceError
from youju.recipe_service.callbacks import CallbackDispatcher
from youju.recipe_service.native_runtime import SYSTEM_PROMPT, NativeRuntimeError, XdogAgentRuntime
from youju.recipe_service.task_worker import TaskWorker


class ToolStub(FakeSandbox):
    """Trusted fixture filesystem executor; no candidate code eval or provider I/O."""

    async def run_script(self, files, module, function, inputs):
        from youju.positions.parsing import extract_position_draft

        assert (
            module in {"parser", "undated_parser"}
            and set(files) == {f"{module}.py"}
            and function == "parse"
        )
        self.calls.append((module, function))
        return extract_position_draft(inputs["bundle"], inputs["config"])

    async def run_author_tool(self, name, args, **fds):
        assert name == "filesystem"
        namespace, filename = args["path"].rsplit("/", 1)
        key = {"/work": "workspace_fd", "/input": "input_fd", "/history": "history_fd"}[namespace]
        root = Path(f"/proc/self/fd/{fds[key]}")
        if args["action"] == "write":
            assert namespace == "/work"
            (root / filename).write_text(args["content"])
            return {"texts": ["Written."], "details": None}
        return {"texts": [(root / filename).read_text()], "details": None}


class ScriptedStream:
    def __init__(self, actions, *, window=200_000):
        self.actions = list(actions)
        self.identity = uuid4().hex
        self.calls, self.summaries = [], []
        self.metadata = Model(
            id="gpt-6.1-sol",
            api="test",
            provider="test",
            context_window=window,
            max_tokens=1024,
            supports_tool_calls=True,
        )

    def models(self):
        return (self.metadata,)

    def resolve_model(self, name):
        return self.metadata if name == "gpt-6.1-sol" else None

    def __call__(self, model, context, options):
        if not context.tools:
            self.summaries.append(context)
            return EventStream.empty(
                AssistantMessage(
                    content=(
                        TextContent(
                            text="Source initialized; continue generation using the exact host "
                            "validation "
                            "contract. No missing material or failed tests are claimed successful."
                        ),
                    ),
                    usage=Usage(input=100, output=20),
                )
            )
        self.calls.append(context)
        assert self.actions, "Unexpected extra model request after an accepted submission"
        action = self.actions.pop(0)
        if callable(action):
            action = action(context)
        if isinstance(action, AssistantMessage):
            return EventStream.empty(action)
        name, arguments = action
        # Globally unique wire IDs survive process/runtime boundaries.
        identity = f"call-{self.identity}-{len(self.calls)}"
        return EventStream.empty(
            AssistantMessage(
                content=(ToolCall(id=identity, name=name, arguments=arguments),),
                stop_reason="toolUse",
                usage=Usage(input=20, output=8),
            )
        )


def write(path, content):
    return ("filesystem", {"action": "write", "path": path, "content": content})


def ack(kind):
    return (
        "submit_init" if kind == "init" else "submit_recipe",
        {"ready": True, "summary": f"Fixture {kind}; host verifies actual files."},
    )


def staged(service, workspace_id, *, revision="v1", request_id="input"):
    workspace = service.workspace(workspace_id)
    payload = {
        "source_id": "source",
        "objective": "Extract evidence-backed source recipes.",
        "samples": [bundle()],
        "now_iso": NOW,
    }
    body = json_bytes(payload)
    (workspace.input / "payload.json").write_bytes(body)
    manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
    (workspace.input / "manifest.json").write_bytes(json_bytes(manifest))
    return service.input_completed(
        workspace_id,
        revision=revision,
        manifest_digest=digest(manifest),
        request_id=request_id,
        callback_target="caller",
    )


def run(service, task_id, stream, *, sandbox=None, compaction=None):
    created = []
    models = XdogModels(XdogConfig(), stub=lambda *_: pytest.fail("Expired sample invoked LLM"))

    def factory():
        runtime = XdogAgentRuntime(models, stream_factory=lambda *_: stream, compaction=compaction)
        created.append(runtime)
        return runtime

    if sandbox is not None:
        original = service.workspace
        service.workspace = lambda key, **kwargs: original(key, sandbox=sandbox)
    try:
        result = asyncio.run(asyncio.wait_for(TaskWorker(service, factory).run_once(task_id), 20))
    finally:
        if sandbox is not None:
            service.workspace = original
    assert all(r.closed and r.agent is None for r in created)
    return result


def init_actions():
    return [
        write(
            "/work/AGENTS.md",
            "# Source Guidelines\n\nRead immutable input and preserve evidence.\n",
        ),
        ack("init"),
    ]


def test_native_init_restore_build_callback_review_and_explicit_fresh_repair(tmp_path):
    service = RecipeService(tmp_path)
    ws = service.create_workspace("source")["workspace_id"]
    receipt = staged(service, ws)
    build = service.start_build(
        ws,
        input_revision="v1",
        prompt="Build source recipes.",
        request_id="build",
        callback_target="caller",
    )
    client = create_app(
        {
            "TESTING": True,
            "RECIPE_RPC_ENABLED": True,
            "RECIPE_SERVICE_STORAGE": str(tmp_path),
            "RECIPE_RPC_CALLERS": {
                "local": {
                    "token": "fixture-rpc-token",
                    "source_ids": ["source"],
                    "agent_profile": {},
                    "callback_target": "caller",
                }
            },
            "RECIPE_CALLBACK_TARGETS": {
                "caller": {"url": "https://caller.test/events", "token": "fixture-callback-token"}
            },
        }
    ).test_client()

    def rpc(method, args):
        response = client.post(
            "/internal/recipe/" + method,
            json=args,
            headers={"X-Recipe-Caller": "local", "Authorization": "Bearer fixture-rpc-token"},
        )
        assert response.status_code == 200, response.json
        return response.json["result"]

    assert rpc("GetWorkspace", {"workspace_id": ws})["status"] == "initializing"
    first = ScriptedStream(init_actions())
    assert run(service, receipt["init_task_id"], first, sandbox=ToolStub())["status"] == "succeeded"
    checkpoint = service.checkpoint(ws)
    snapshot = decode_snapshot(checkpoint["agent"])
    snapshot.require_resumable()
    assert len(snapshot.messages) >= 5 and snapshot.skills[0].slug == "flow"
    assert snapshot.base_system_prompt == SYSTEM_PROMPT
    assert load_packaged_skill("flow").content in str(first.calls[0].system_prompt)
    # Reopen service; no old in-memory runtime exists, and staging changes must not win.
    service.close()
    service = RecipeService(tmp_path)
    service.workspace(ws).input.joinpath("payload.json").write_text("staging is untrusted now")
    second = ScriptedStream(
        [
            ("filesystem", {"action": "read", "path": "/input/payload.json"}),
            ack("build"),
        ]
    )
    try:
        assert run(service, build["id"], second, sandbox=ToolStub())["status"] == "candidate_ready"
        result = rpc("GetTaskResult", {"task_id": build["id"]})
        assert result["sample_validation"] == "passed"
        assert result["attempts"][-1]["samples"][0]["position_count"] == 1
        assert any(
            isinstance(m, ToolResultMessage) and "Extract evidence-backed" in str(m.content)
            for m in second.calls[1].messages
        )
        assert any(
            isinstance(m, ToolResultMessage) and m.tool_name == "submit_init"
            for m in second.calls[0].messages
        )
        published = rpc(
            "GetRecipeRevision", {"revision_id": service.status(build["id"])["revision_id"]}
        )
        assert published["artifact_digest"] == result["digest"]
        assert published["candidate"] == result["candidate"]
        accepted = rpc(
            "ReviewRevision",
            {
                "workspace_id": ws,
                "revision_id": published["id"],
                "decision": "accept",
                "feedback": {"audit": "synthetic-independent-audit"},
                "request_id": "accept",
            },
        )
        assert service.pending() == []  # Review is not repair dispatch.
        fresh = staged(service, ws, revision="v2", request_id="repair-input")
        repair = rpc(
            "StartRepair",
            {
                "workspace_id": ws,
                "input_revision": "v2",
                "base_revision_id": published["id"],
                "base_recipe_digest": result["digest"],
                "feedback": {"error": "Change source field mapping"},
                "request_id": "repair",
            },
        )
        assert repair["input_revision_id"] == fresh["input_revision_id"]
        assert repair["input_revision_id"] != build["input_revision_id"]
        third = ScriptedStream(
            [
                ack("build"),  # Unchanged candidate must return rejection, not finish.
                write(
                    "/work/config.json",
                    json.dumps(
                        {
                            "runtime_contract": "recipe-runtime-v3",
                            "position_contract": "position-facts-v3",
                            "fixture_revision": 2,
                        }
                    ),
                ),
                ack("build"),
            ]
        )
        assert run(service, repair["id"], third, sandbox=ToolStub())["status"] == "candidate_ready"
        repaired = service.result(repair["id"])
        assert repaired["digest"] != accepted["artifact_digest"]
        assert service.workspace_info(ws)["accepted_recipe_digest"] == accepted["artifact_digest"]
        assert "REPAIR_UNCHANGED" in str(third.calls[1].messages[-1].content)
        service.review_revision(
            ws,
            revision_id=service.status(repair["id"])["revision_id"],
            decision="accept",
            feedback={"audit": "synthetic-repaired-source-audit"},
            request_id="accept-repair",
        )
        callbacks = []

        async def receive(request):
            callbacks.append(json.loads(request.content))
            return httpx.Response(200)

        dispatcher = CallbackDispatcher(
            service,
            {"caller": {"url": "https://caller.test/events", "token": "fixture-callback-token"}},
            transport=httpx.MockTransport(receive),
        )
        while asyncio.run(dispatcher.run_once()) is not None:
            pass
        assert len(callbacks) == 3 and len({c["event_id"] for c in callbacks}) == 3
        assert all("checkpoint" not in json.dumps(c) for c in callbacks)
        assert service.workspace_info(ws)["accepted_recipe_digest"] == repaired["digest"]
        assert service.status(build["id"])["checkpoint_ref"]
        assert service.workspace(ws).workspace.joinpath("AGENTS.md").is_file()
    finally:
        service.close()


def test_native_auto_compaction_archives_restores_and_no_turn_cap(tmp_path):
    service = RecipeService(tmp_path)
    ws = service.create_workspace("source")["workspace_id"]
    receipt = staged(service, ws)
    assert (
        run(service, receipt["init_task_id"], ScriptedStream(init_actions()), sandbox=ToolStub())[
            "status"
        ]
        == "succeeded"
    )
    build = service.start_build(
        ws, input_revision="v1", prompt="Build and preserve audit evidence.", request_id="build"
    )
    noise = [
        AssistantMessage(content=(TextContent(text="Work context " * 2000),)) for _ in range(36)
    ]
    stream = ScriptedStream(noise + [ack("build")], window=60_000)
    try:
        result = run(
            service,
            build["id"],
            stream,
            sandbox=ToolStub(),
            compaction=CompactionConfig(auto=True, safety_margin=512, output_reserve=1024),
        )
        assert result["status"] == "candidate_ready" and len(stream.calls) == 37
        assert stream.summaries
        checkpoint = service.checkpoint(ws)
        saved = decode_snapshot(checkpoint["agent"])
        assert saved.compaction.generation > 0
        assert list((service.workspace(ws).root / ".session-archive").glob("*.json"))
        next_build = service.start_build(
            ws, input_revision="v1", prompt="Recheck the same recipe.", request_id="build-2"
        )
        next_stream = ScriptedStream([ack("build")])
        assert (
            run(service, next_build["id"], next_stream, sandbox=ToolStub())["status"]
            == "candidate_ready"
        )
        assert "conversation_summary" in str(next_stream.calls[0].messages[0].content)
        assert decode_snapshot(service.checkpoint(ws)["agent"]).compaction.generation > 0
    finally:
        service.close()


@pytest.mark.skipif(
    os.getenv("YOUJU_TEST_AUTHOR_SANDBOX") != "1" or not shutil.which("bwrap"),
    reason="Real Linux sandbox test requires explicit opt-in",
)
def test_native_official_tools_and_frozen_mount_in_real_bwrap(tmp_path):
    service = RecipeService(tmp_path)
    ws = service.create_workspace("source")["workspace_id"]
    receipt = staged(service, ws)
    service.workspace(ws).input.joinpath("payload.json").write_text("not the committed input")
    stream = ScriptedStream(
        [
            ("filesystem", {"action": "read", "path": "/input/payload.json"}),
            *init_actions(),
        ]
    )
    try:
        assert (
            run(service, receipt["init_task_id"], stream, sandbox=Sandbox())["status"]
            == "succeeded"
        )
        assert "Extract evidence-backed" in str(stream.calls[1].messages[-1].content)
        assert (
            service.workspace(ws).workspace.joinpath("AGENTS.md").read_text().startswith("# Source")
        )
        build = service.start_build(
            ws,
            input_revision="v1",
            prompt="Validate the saved sample.",
            request_id="real-sandbox-build",
        )
        edited = template_candidate()["workflow"]
        edited["nodes"][0]["subflow"]["name"] = "source-adapted-parse"
        for edge in edited["nodes"][0]["subflow"]["edges"]:
            if edge["from"] == "review" and edge["to"] in {"general", "undated"}:
                edge["when"] = {
                    "equals": {"value": "yes", "text": "yes" if edge["to"] == "undated" else "no"}
                }
        assert all(node["type"] == "subflow" for node in edited["nodes"])
        actions = [write("/work/workflow.json", json.dumps(edited)), ack("build")]
        assert (
            run(service, build["id"], ScriptedStream(actions), sandbox=Sandbox())["status"]
            == "candidate_ready"
        )
        result = service.result(build["id"])
        assert result["candidate"]["workflow"] == edited
        assert result["attempts"][0]["samples"][0]["position_count"] == 1
        from youju.extraction_service import RecipeRunner

        actual = asyncio.run(
            RecipeRunner(Sandbox()).run(result["candidate"], bundle(), now_iso=NOW)
        )
        assert actual["execution"]["parser"] == "undated_parser.py"
        assert not actual["execution"]["model_used"]

    finally:
        service.close()


def test_native_provider_failure_and_checkpoint_rejection_stop_without_replay(
    tmp_path, monkeypatch
):
    service = RecipeService(tmp_path)
    ws = service.create_workspace("source")["workspace_id"]
    receipt = staged(service, ws)
    failing = ScriptedStream(
        [AssistantMessage(stop_reason="error", error_message="fixture failure")]
    )
    try:
        with pytest.raises(ServiceError, match="NATIVE_PROVIDER_ERROR"):
            run(service, receipt["init_task_id"], failing, sandbox=ToolStub())
        assert len(failing.calls) == 1
        assert service.status(receipt["init_task_id"])["status"] == "failed"
        assert service.workspace_info(ws)["status"] == "init_failed"
        # A new submitted input triggers explicit retry, retaining history/work.
        workspace = service.workspace(ws)
        body = json_bytes(
            {
                "source_id": "source",
                "objective": "Extract evidence-backed source recipes.",
                "samples": [bundle()],
                "now_iso": NOW,
            }
        )
        workspace.input.joinpath("payload.json").write_bytes(body)
        manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
        workspace.input.joinpath("manifest.json").write_bytes(json_bytes(manifest))
        retry = service.input_completed(
            ws, revision="v2", manifest_digest=digest(manifest), request_id="retry"
        )
        before = service.workspace_info(ws)["checkpoint_ref"]
        stream = ScriptedStream(init_actions())
        original = service.save_checkpoint

        def reject(*args, **kwargs):
            if not kwargs.get("recovery"):
                raise RuntimeError("fixture persistence failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "save_checkpoint", reject)
        with pytest.raises(ServiceError):
            run(service, retry["init_task_id"], stream, sandbox=ToolStub())
        # First filesystem write settled; persistence barrier stopped submit and
        # no extra tool or provider call was permitted after checkpoint rejection.
        assert len(stream.calls) == 1
        assert service.workspace_info(ws)["checkpoint_ref"] == before
        assert service.status(retry["init_task_id"])["status"] == "failed"
    finally:
        service.close()


def test_native_restore_rejects_changed_provider_binding_before_dispatch(tmp_path):
    service = RecipeService(tmp_path)
    ws = service.create_workspace("source")["workspace_id"]
    receipt = staged(service, ws)
    assert (
        run(service, receipt["init_task_id"], ScriptedStream(init_actions()), sandbox=ToolStub())[
            "status"
        ]
        == "succeeded"
    )
    build = service.start_build(
        ws, input_revision="v1", prompt="Build source recipes.", request_id="build"
    )
    # A corrupt/different vendor envelope cannot silently reuse the same bare model alias.
    saved = service.checkpoint(ws)
    saved["bindings"]["provider"] = "different-provider"
    models = XdogModels(XdogConfig(), stub=lambda *_: pytest.fail("Provider dispatch forbidden"))
    stream = ScriptedStream([ack("build")])
    runtime = XdogAgentRuntime(models, stream_factory=lambda *_: stream)
    task = service.status(build["id"])
    task["prompt"] = "Build source recipes."

    async def rejected():
        try:
            with pytest.raises(NativeRuntimeError, match="NATIVE_CHECKPOINT_MISMATCH"):
                await runtime.execute_task(
                    task=task,
                    workspace=service.workspace(ws, sandbox=ToolStub()),
                    inputs=service.input_snapshot(task["input_revision_id"]),
                    agent_profile=service.workspace_info(ws)["agent_profile"],
                    checkpoint=saved,
                    on_event=lambda *_: pytest.fail("Dispatch forbidden"),
                )
        finally:
            await runtime.aclose()

    try:
        asyncio.run(rejected())
        assert not stream.calls and runtime.closed
        assert service.status(build["id"])["status"] == "queued"
    finally:
        service.close()


def test_explicit_guidance_upgrade_preserves_session_and_other_identity_pins(tmp_path):
    service = RecipeService(tmp_path)
    ws = service.create_workspace("source")["workspace_id"]
    receipt = staged(service, ws)
    assert (
        run(service, receipt["init_task_id"], ScriptedStream(init_actions()), sandbox=ToolStub())[
            "status"
        ]
        == "succeeded"
    )
    build = service.start_build(
        ws, input_revision="v1", prompt="Build under updated guidance.", request_id="build"
    )
    saved = service.checkpoint(ws)
    session_id = saved["agent"]["session_id"]
    saved["bindings"]["guide_digest"] = "0" * 64
    task = service.status(build["id"])
    task["prompt"] = "Build under updated guidance."
    models = XdogModels(XdogConfig(), stub=lambda *_: pytest.fail("Expired sample invoked LLM"))

    async def scenario():
        denied = XdogAgentRuntime(models, stream_factory=lambda *_: ScriptedStream([ack("build")]))
        try:
            with pytest.raises(NativeRuntimeError, match="NATIVE_CHECKPOINT_MISMATCH"):
                await denied.execute_task(
                    task=task,
                    workspace=service.workspace(ws, sandbox=ToolStub()),
                    inputs=service.input_snapshot(task["input_revision_id"]),
                    agent_profile=service.workspace_info(ws)["agent_profile"],
                    checkpoint=saved,
                    on_event=lambda *_: pytest.fail("Dispatch before approval"),
                )
        finally:
            await denied.aclose()
        events = []

        async def progress(value):
            events.append(value)

        upgraded = XdogAgentRuntime(
            models,
            stream_factory=lambda *_: ScriptedStream([ack("build")]),
            allow_guidance_update=True,
        )
        try:
            result = await upgraded.execute_task(
                task=task,
                workspace=service.workspace(ws, sandbox=ToolStub()),
                inputs=service.input_snapshot(task["input_revision_id"]),
                agent_profile=service.workspace_info(ws)["agent_profile"],
                checkpoint=saved,
                on_event=progress,
            )
            assert result.status == "candidate_ready"
            assert result.checkpoint["agent"]["session_id"] == session_id
            assert result.checkpoint["bindings"]["guide_digest"] != "0" * 64
            assert events[0]["type"] == "guidance_updated" and events[1]["type"] == "checkpoint"
        finally:
            await upgraded.aclose()
        wrong_provider = copy.deepcopy(saved)
        wrong_provider["bindings"]["provider"] = "different-provider"
        rejected = XdogAgentRuntime(
            models,
            stream_factory=lambda *_: ScriptedStream([ack("build")]),
            allow_guidance_update=True,
        )
        try:
            with pytest.raises(NativeRuntimeError, match="NATIVE_CHECKPOINT_MISMATCH"):
                await rejected.execute_task(
                    task=task,
                    workspace=service.workspace(ws, sandbox=ToolStub()),
                    inputs=service.input_snapshot(task["input_revision_id"]),
                    agent_profile=service.workspace_info(ws)["agent_profile"],
                    checkpoint=wrong_provider,
                    on_event=progress,
                )
        finally:
            await rejected.aclose()

    try:
        asyncio.run(scenario())
        assert service.status(build["id"])["status"] == "queued"
    finally:
        service.close()
