"""Host-state checks plus an opt-in real official-tool/bwrap boundary check."""

import asyncio
import hashlib
import json
import os
import shutil
import stat

import pytest
from xdog.agent.tools import create_bash_tool, create_filesystem_tool

from youju.onboarding.author_workspace import AuthorWorkspace, AuthorWorkspaceError
from youju.onboarding.sandbox import Sandbox

TEMPLATE = {
    "workflow": {"name": "synthetic", "nodes": []},
    "parser_py": "def parse(ctx, bundle, config):\n    return {}\n",
    "undated_parser_py": "def parse(ctx, bundle, config):\n    return {}\n",
    "config": {},
}


def prepare(workspace):
    return workspace.prepare(
        {"samples": [], "now_iso": "2026-10-11T00:00:00+08:00"},
        skill_text="Full upstream skill fixture",
        guide_text="Source guide",
        template=TEMPLATE,
    )


def text(result):
    return "\n".join(part.text for part in result.content)


def test_host_persistence_history_and_no_follow_files(tmp_path):
    workspace = AuthorWorkspace(tmp_path / "source")
    provenance = prepare(workspace)
    assert provenance["workspace"] == "/work" and not provenance["cwd_persists"]
    assert workspace.load_candidate() == TEMPLATE
    changed = {**TEMPLATE, "config": {"source": "modified"}}
    workspace.write_candidate(changed)
    prepare(workspace)
    assert workspace.load_candidate() == changed
    for number in range(1, 5):  # There is no fixed two-attempt cap.
        workspace.record_attempt({"status": "rejected", "errors": [{"code": f"E{number}"}]})
    old_attempt = (workspace.history / "attempts/1.json").read_bytes()
    workspace.record_run({"status": "provider_failure", "error": "synthetic failure"})
    state = json.loads((workspace.history / "status.json").read_bytes())
    assert state["latest_validation"]["sequence"] == 4
    assert state["errors"] == [{"code": "E4"}]
    assert state["latest_run"]["path"] == "/history/runs/1.json"
    assert (workspace.history / "attempts/1.json").read_bytes() == old_attempt
    workspace.record_run({"status": "cancelled"})
    state = json.loads((workspace.history / "status.json").read_bytes())
    assert state["errors"] == [{"code": "E4"}] and state["latest_run"]["sequence"] == 2
    reopened = AuthorWorkspace(workspace.root)
    prepare(reopened)
    assert reopened.load_candidate() == changed
    assert (workspace.history / "attempts/1.json").read_bytes() == old_attempt

    target = workspace.workspace / "workflow.json"
    target.write_text("{")
    prepare(workspace)  # Preserve an unfinished regular file for the next tool turn.
    assert target.read_text() == "{"
    with pytest.raises(AuthorWorkspaceError, match="invalid_author_json_file"):
        workspace.load_candidate()
    outside = tmp_path / "outside.json"
    outside.write_text('{"untouched": true}')
    target.unlink()
    target.symlink_to(outside)
    for action in (
        workspace.load_candidate,
        lambda: workspace.write_candidate(TEMPLATE),
        lambda: prepare(workspace),
    ):
        with pytest.raises(AuthorWorkspaceError):
            action()
    assert outside.read_text() == '{"untouched": true}'
    target.unlink()
    os.link(outside, target)
    with pytest.raises(AuthorWorkspaceError):
        workspace.load_candidate()
    target.unlink()
    os.mkfifo(target)
    with pytest.raises(AuthorWorkspaceError):
        workspace.load_candidate()
    target.unlink()
    workspace.write_candidate(TEMPLATE)
    runs = workspace.history / "runs"
    runs.rename(workspace.history / "old-runs")
    runs.symlink_to(tmp_path)
    with pytest.raises(AuthorWorkspaceError):
        workspace.record_run({"status": "success"})
    root_link = tmp_path / "linked-root"
    root_link.symlink_to(workspace.root)
    with pytest.raises(AuthorWorkspaceError):
        AuthorWorkspace(root_link)


def test_official_metadata_stub_boundary_and_cancellation(tmp_path):
    class StubSandbox:
        timeout = 25

        def __init__(self):
            self.calls = []
            self.started = None
            self.cancelled = False

        async def run_author_tool(self, name, args, **kwargs):
            assert set(kwargs) == {"tool_call_id", "workspace_fd", "input_fd", "history_fd"}
            for key in ("workspace_fd", "input_fd", "history_fd"):
                assert stat.S_ISDIR(os.fstat(kwargs[key]).st_mode)
            self.calls.append((name, args))
            if args.get("command") == "wait":
                self.started.set()
                try:
                    await asyncio.Future()
                finally:
                    self.cancelled = True
            return {"texts": ["synthetic tool result"], "details": None}

    stub = StubSandbox()
    workspace = AuthorWorkspace(tmp_path / "source", sandbox=stub)
    prepare(workspace)
    tools = workspace.tools()
    for actual, official in zip(tools, [create_bash_tool(), create_filesystem_tool()], strict=True):
        assert (actual.name, actual.description, actual.parameters, actual.label) == (
            official.name,
            official.description,
            official.parameters,
            official.label,
        )
    assert (
        text(
            asyncio.run(
                tools[0].execute(
                    "call-1", {"command": "pwd"}, ctx={"must_not_cross_boundary": "secret"}
                )
            )
        )
        == "synthetic tool result"
    )
    assert stub.calls == [("bash", {"command": "pwd"})]

    async def cancel_call():
        stub.started = asyncio.Event()
        cancel = asyncio.Event()
        task = asyncio.create_task(tools[0].execute("call-2", {"command": "wait"}, cancel=cancel))
        await stub.started.wait()
        with pytest.raises(AuthorWorkspaceError, match="author_workspace_busy"):
            workspace.load_candidate()
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stub.cancelled
        assert workspace.load_candidate() == TEMPLATE  # Host lock released after cancellation.

    asyncio.run(cancel_call())

    # The Responses adapter joins wire call_id and an opaque item ID. Keep the
    # original outside the child, and send a bounded execution reference inside.
    captured = []

    class RecordingSandbox(Sandbox):
        async def _run(self, payload, **kwargs):
            captured.append(payload)
            return {"texts": ["ok"], "details": None}

    long_id = "call-original|" + "opaque-item-" * 100
    bounded = AuthorWorkspace(tmp_path / "long-id", sandbox=RecordingSandbox())
    result = asyncio.run(bounded.tools()[0].execute(long_id, {"command": "pwd"}))
    assert text(result) == "ok"
    assert captured[0]["tool_call_id"] == hashlib.sha256(long_id.encode()).hexdigest()
    assert len(captured[0]["tool_call_id"]) == 64


@pytest.mark.skipif(
    os.getenv("YOUJU_TEST_AUTHOR_SANDBOX") != "1" or not shutil.which("bwrap"),
    reason="Opt in with YOUJU_TEST_AUTHOR_SANDBOX=1; Linux bwrap is required.",
)
def test_real_official_tools_persist_with_readonly_inputs_and_killed_children(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("YOUJU_HOST_SECRET", "must-not-enter-child")
    workspace = AuthorWorkspace(tmp_path / "source")
    prepare(workspace)
    workspace.record_attempt({"status": "rejected", "errors": [{"code": "SYNTHETIC"}]})
    tools = {tool.name: tool for tool in workspace.tools()}
    input_before = (workspace.input / "payload.json").read_bytes()
    history_before = (workspace.history / "status.json").read_bytes()
    outside = tmp_path / "host-secret"
    outside.write_text("not-mounted")
    net_namespace = os.stat("/proc/self/ns/net").st_ino
    host_pid = os.getpid()

    async def run():
        result = await tools["filesystem"].execute(
            "call-write|" + "opaque-item-" * 100,
            {"action": "write", "path": "/work/note.txt", "content": "persistent"},
        )
        assert "Error:" not in text(result), text(result)
        result = await tools["filesystem"].execute(
            "read", {"action": "read", "path": "/work/note.txt"}
        )
        assert "persistent" in text(result)
        result = await tools["filesystem"].execute(
            "history", {"action": "read", "path": "/history/status.json"}
        )
        assert "SYNTHETIC" in text(result)
        result = await tools["filesystem"].execute(
            "deny-write", {"action": "write", "path": "/input/payload.json", "content": "tampered"}
        )
        assert (workspace.input / "payload.json").read_bytes() == input_before, text(result)
        script = f"""import os, pathlib, resource, shutil
assert os.environ.get("YOUJU_AUTHOR_SANDBOX") == "1"
assert "YOUJU_HOST_SECRET" not in os.environ
assert not pathlib.Path({str(outside)!r}).exists()
assert not pathlib.Path("/proc/{host_pid}").exists()
assert os.stat("/proc/self/ns/net").st_ino != {net_namespace}
assert shutil.which("python") and shutil.which("xdog-flow")
assert resource.getrlimit(resource.RLIMIT_CPU)[0] == 15
assert resource.getrlimit(resource.RLIMIT_AS)[0] == 768*1024*1024
assert resource.getrlimit(resource.RLIMIT_FSIZE)[0] == 8*1024*1024
for path in ("/input/payload.json", "/history/status.json", "/app/youju/onboarding/sandbox.py"):
    try: pathlib.Path(path).write_text("bad")
    except OSError: pass
    else: raise AssertionError("writable protected file: " + path)
for process in pathlib.Path("/proc").iterdir():
    if process.name.isdigit():
        try: descriptors = list((process/"fd").iterdir())
        except OSError: continue
        for descriptor in descriptors:
            try: value = os.readlink(descriptor)
            except OSError: continue
            assert {str(workspace.root)!r} not in value, value
print("ISOLATION_OK")"""
        result = await tools["bash"].execute(
            "isolation", {"command": "python - <<'PY'\n" + script + "\nPY"}
        )
        assert "ISOLATION_OK" in text(result) and "[exit code:" not in text(result), text(result)
        result = await tools["bash"].execute("cwd-1", {"command": "mkdir -p sub; cd sub; pwd"})
        assert "/work/sub" in text(result), text(result)
        result = await tools["bash"].execute(
            "call-bash|" + "opaque-item-" * 100,
            {"command": "pwd; cat /work/note.txt"},
        )
        assert text(result).splitlines()[0] == "/work" and "persistent" in text(result)
        result = await tools["bash"].execute(
            "plant-link", {"command": "rm config.json; ln -s /input/payload.json config.json"}
        )
        assert "[exit code:" not in text(result), text(result)
        with pytest.raises(AuthorWorkspaceError):
            workspace.load_candidate()
        result = await tools["bash"].execute("unlink", {"command": "rm config.json"})
        assert "[exit code:" not in text(result), text(result)
        workspace.write_candidate(TEMPLATE)
        cancel = asyncio.Event()
        command = (
            "python -c 'import time,pathlib; time.sleep(1); "
            'pathlib.Path("/work/escaped").write_text("bad")'
            "' & "
            "echo ready > /work/started; sleep 20"
        )
        task = asyncio.create_task(
            tools["bash"].execute("cancel", {"command": command}, cancel=cancel)
        )
        for _ in range(100):
            if (workspace.workspace / "started").exists():
                break
            await asyncio.sleep(0.05)
        assert (workspace.workspace / "started").exists()
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(1.2)
        assert not (workspace.workspace / "escaped").exists()
        assert (workspace.input / "payload.json").read_bytes() == input_before
        assert (workspace.history / "status.json").read_bytes() == history_before

    asyncio.run(run())
