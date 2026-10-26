"""Official capability registration/restore and networkless Flow CLI execution."""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from test_author_workspace import prepare, text
from test_recipe_native_runtime import (
    ScriptedStream,
    ToolStub,
    ack,
    init_actions,
    run,
    staged,
)
from xdog.flow.capability import FlowCapability

from youju.onboarding.author_workspace import AuthorWorkspace
from youju.onboarding.flow_capability import FLOW_CAPABILITY_PROFILE, SandboxedFlowCapabilities
from youju.recipe_service import RecipeService


def test_official_capability_supplies_schema_but_never_executes_on_host(tmp_path, monkeypatch):
    class Transport:
        async def run_author_tool(self, name, args, **fds):
            assert name == "flow" and args == {"arguments": ["--help"]}
            assert all(
                Path(f"/proc/self/fd/{fd}").is_dir()
                for key, fd in fds.items()
                if key.endswith("_fd")
            )
            return {"texts": ['{"exit_code": 0, "stdout": "child only", "stderr": ""}']}

    async def forbidden(*args, **kwargs):
        pytest.fail("The raw host Flow executor must never execute")

    monkeypatch.setattr(FlowCapability, "run", forbidden)
    workspace = AuthorWorkspace(tmp_path / "work", sandbox=Transport())
    prepare(workspace)

    async def scenario():
        capabilities = SandboxedFlowCapabilities()
        try:
            await capabilities.start()
            original = capabilities.assembly.tools[0]
            tool = workspace.isolate_tools([original])[0]
            assert (tool.name, tool.description, tool.parameters) == (
                original.name,
                original.description,
                original.parameters,
            )
            assert tool.execute is not original.execute
            result = await tool.execute("flow-call", {"arguments": ["--help"]})
            assert json.loads(text(result))["stdout"] == "child only"
        finally:
            await capabilities.aclose()
        assert capabilities.closed

    asyncio.run(scenario())


def test_native_restored_author_receives_capability_and_skill_without_model_cost(tmp_path):
    class FlowTransport(ToolStub):
        async def run_author_tool(self, name, args, **fds):
            if name == "flow":
                assert args["arguments"] == ["--help"]
                return {"texts": ['{"exit_code":0,"stdout":"Flow fixture","stderr":""}']}
            return await super().run_author_tool(name, args, **fds)

    service = RecipeService(tmp_path / "service")
    try:
        key = service.create_workspace("source")["workspace_id"]
        prepared = staged(service, key)
        flow = ("flow", {"arguments": ["--help"]})
        init = ScriptedStream([flow, *init_actions()])
        assert (
            run(service, prepared["init_task_id"], init, sandbox=FlowTransport())["status"]
            == "succeeded"
        )
        assert "flow" in {tool.name for tool in init.calls[0].tools}
        checkpoint = service.checkpoint(key)
        assert checkpoint["bindings"]["capability_profile"] == FLOW_CAPABILITY_PROFILE
        assert service.workspace_info(key)["agent_profile"]["model"] == "gpt-6.1-sol"
        build = service.start_build(key, input_revision="v1", request_id="build", prompt="Build")
        restored = ScriptedStream([flow, ack("build")])
        assert (
            run(service, build["id"], restored, sandbox=FlowTransport())["status"]
            == "candidate_ready"
        )
        assert "flow" in {tool.name for tool in restored.calls[0].tools}
        assert service.workspace_info(key)["accepted_revision_id"] is None
    finally:
        service.close()


@pytest.mark.skipif(
    os.getenv("YOUJU_TEST_AUTHOR_SANDBOX") != "1" or not shutil.which("bwrap"),
    reason="Opt in to real sandbox Flow CLI; no providers are called",
)
def test_real_flow_capability_cli_is_sandboxed_and_scheduling_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUJU_HOST_SECRET", "not-visible-to-flow")
    workspace = AuthorWorkspace(tmp_path / "workspace")
    prepare(workspace)
    outside = tmp_path / "host-private"
    outside.write_text("not-mounted")
    before = (workspace.input / "payload.json").read_bytes()
    workflow = {
        "name": "sandbox-capability-probe",
        "nodes": [
            {
                "id": "probe",
                "type": "script",
                "inputs": [],
                "code": (
                    "def probe(ctx):\n"
                    "    import os, pathlib\n"
                    "    assert 'YOUJU_HOST_SECRET' not in os.environ\n"
                    f"    assert not pathlib.Path({str(outside)!r}).exists()\n"
                    "    try: pathlib.Path('/input/payload.json').write_text('tampered')\n"
                    "    except OSError: pass\n"
                    "    else: raise AssertionError('writable immutable input')\n"
                    "    return True\n"
                ),
                "outputs": [{"name": "safe", "schema": {"type": "boolean"}}],
            }
        ],
        "edges": [{"from": "probe", "to": "$output", "map": {"safe": "safe"}}],
    }
    (workspace.workspace / "probe.json").write_text(json.dumps(workflow))
    # The official capability launches `python -m xdog.flow`; the mutable cwd
    # must not shadow that installed package in the CLI subprocess.
    planted = workspace.workspace / "xdog"
    planted.mkdir()
    (planted / "__init__.py").write_text("raise AssertionError('cwd package shadowed SDK')")

    async def forbidden(*args, **kwargs):
        pytest.fail("Flow executed on host instead of in the OS namespace")

    monkeypatch.setattr(FlowCapability, "run", forbidden)

    async def scenario():
        capabilities = SandboxedFlowCapabilities()
        try:
            await capabilities.start()
            tool = workspace.isolate_tools(capabilities.assembly.tools)[0]
            help_result = json.loads(text(await tool.execute("help", {"arguments": ["--help"]})))
            assert help_result["exit_code"] == 0, help_result
            assert "validate" in help_result["stdout"]
            result = json.loads(
                text(await tool.execute("run", {"arguments": ["run", "/work/probe.json"]}))
            )
            assert result["exit_code"] == 0, result
            assert "true" in result["stdout"].lower(), result
            denied = text(await tool.execute("denied", {"arguments": ["scheduling", "--help"]}))
            assert "flow_operation_not_authorized" in denied
        finally:
            await capabilities.aclose()

    asyncio.run(scenario())
    assert outside.read_text() == "not-mounted"
    assert (workspace.input / "payload.json").read_bytes() == before


@pytest.mark.skipif(
    os.getenv("YOUJU_TEST_AUTHOR_SANDBOX") != "1" or not shutil.which("bwrap"),
    reason="Opt in to real generic-template coarse/full routing; no model calls",
)
def test_actual_template_unsupported_zero_rows_archives_only_host_expired_material(tmp_path):
    from test_recipe_accuracy import NOW, material

    from youju.onboarding.recipes import RecipeAuthor, RecipeError, RecipeRunner, template_candidate
    from youju.onboarding.sandbox import Sandbox

    async def scenario():
        candidate = template_candidate(accuracy=True)
        saved = material("2026-10-22")
        saved["page"]["tables"] = []
        # Keep literal application timing, but no recognizable position table.
        # This reproduces a saved USTC source-wide replay defect, not a synthetic
        # success envelope with positions merely deleted after parsing a table.
        runner = RecipeRunner(Sandbox())
        output = await runner.run(candidate, saved, now_iso=NOW)
        assert output["execution"]["path"] == "quick" and output["status"] == "review"
        assert output["positions"] == []
        assert output["errors"] == [
            {"code": "UNSUPPORTED_POSITION_LAYOUT", "message": "No recognizable position table"}
        ]
        saved["status"] = "partial"
        saved["attachments"] = [
            {
                "format": "docx",
                "status": "failed",
                "blocks": [],
                "tables": [],
                "errors": [{"code": "http_404", "message": "Missing application form"}],
            }
        ]
        partial = await runner.run(candidate, saved, now_iso=NOW)
        assert partial["execution"]["path"] == "quick" and partial["status"] == "review"
        assert partial["positions"] == [] and "validity" not in partial
        assert partial["errors"]  # Capture warnings persist without becoming parse failure.
        saved["status"] = "complete"
        saved["attachments"] = []
        validation = await RecipeAuthor(None, runner).validate_candidate(candidate, [saved], NOW, 1)
        assert not validation["errors"]
        opened = material("2026-10-31")
        opened["page"]["tables"] = []
        with pytest.raises(RecipeError) as failure:
            await runner.run(candidate, opened, now_iso=NOW)
        assert failure.value.code == "MODEL_DISABLED"

    asyncio.run(scenario())


def test_generic_template_zero_rows_never_hides_failures_or_other_review_reasons():
    from copy import deepcopy

    from youju.onboarding.recipes import RecipeError, RecipeRunner, template_candidate
    from youju.positions.drafts import ExtractionDraft

    class Script:
        broken = False
        draft = None

        async def run_script(self, files, module, function, inputs):
            if self.broken:
                raise ValueError("Synthetic source execution failure")
            assert module == "parser" and function == "parse"
            return deepcopy(self.draft)

    async def scenario():
        script = Script()
        routing = {
            "category": "fixed_expired",
            "mode": "quick",
            "reason": "Cost only",
            "policy_version": "fixture",
            "evaluated_at": "2026-10-24T12:00:00+08:00",
        }
        runner = RecipeRunner(script)
        for issues in (
            [{"code": "UNSUPPORTED_POSITION_LAYOUT", "message": "No recognized rows"}],
            [{"code": "INCOMPLETE_CAPTURE", "message": "Partial saved input"}],
            [{"code": "CONFLICT", "message": "Unresolved source content"}],
            [],
        ):
            script.draft = ExtractionDraft(
                coverage={"supported": False, "complete": False, "needs_detailed": True},
                issues=issues,
            ).as_dict()
            result = await runner.run(
                template_candidate(), {"page": {}}, now_iso=routing["evaluated_at"], routing=routing
            )
            assert result["positions"] == [] and result["status"] == "review"
            assert all(issue in result["errors"] for issue in issues)
            assert {issue["code"] for issue in result["errors"]} <= {
                *[issue["code"] for issue in issues],
                "INCOMPLETE_CAPTURE",
            }  # Capture diagnostics are recomputed, never a swallowed script error.
        script.broken = True
        with pytest.raises(RecipeError) as failure:
            await runner.run(
                template_candidate(), {"page": {}}, now_iso=routing["evaluated_at"], routing=routing
            )
        assert failure.value.code == "SCRIPT_PARSE_FAILED"

    asyncio.run(scenario())
