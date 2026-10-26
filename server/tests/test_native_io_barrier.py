"""Managed-native workspace threads settle before assignment or close is released."""

import ast
import asyncio
import inspect
import json
import threading

import pytest
from test_recipe_native_runtime import ScriptedStream, ToolStub, init_actions, staged

from youju.onboarding.author_workspace import AuthorWorkspace, AuthorWorkspaceError
from youju.onboarding.xdog import XdogModels
from youju.recipe_service import RecipeService, ServiceBusy
from youju.recipe_service.native_runtime import XdogAgentRuntime
from youju.recipe_service.task_inputs import TaskWorkspace
from youju.recipe_service.task_worker import TaskWorker


def models():
    return XdogModels(stub=lambda *_: pytest.fail("Unexpected extraction/model call"))


def gated(workspace, operation, entered, release, loop, *, failure=None):
    original = operation

    def run(*args, **kwargs):
        # Exercise the real, existing nonblocking workspace flock. The gate is
        # test synchronization only; production does not gain a lock/retry loop.
        with workspace._directories():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError("Test did not release the owned file thread")
            if failure is not None:
                raise failure
        return original(*args, **kwargs)

    return run


@pytest.mark.parametrize("operation", ["instructions", "record_run"])
@pytest.mark.parametrize("late_failure", [False, True], ids=["read-write", "late-error"])
def test_native_worker_keeps_assignment_until_cancelled_file_thread_finishes(
    monkeypatch, tmp_path, operation, late_failure
):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        service = RecipeService(tmp_path)
        running = None
        try:
            workspace_id = service.create_workspace("source")["workspace_id"]
            receipt = staged(service, workspace_id)
            sandbox = ToolStub()
            original_workspace = service.workspace
            monkeypatch.setattr(
                service,
                "workspace",
                lambda key, **kwargs: original_workspace(key, sandbox=sandbox),
            )
            original = getattr(TaskWorkspace, operation)
            failure = OSError("synthetic late file error") if late_failure else None

            def file_io(view, *args, **kwargs):
                return gated(
                    view,
                    lambda *a, **kw: original(view, *a, **kw),
                    entered,
                    release,
                    loop,
                    failure=failure,
                )(*args, **kwargs)

            monkeypatch.setattr(TaskWorkspace, operation, file_io)
            stream = ScriptedStream(init_actions())
            runtime = XdogAgentRuntime(models(), stream_factory=lambda *_: stream)
            worker = TaskWorker(service, lambda: runtime, poll_seconds=0.01)
            running = asyncio.create_task(worker.run_once(receipt["init_task_id"]))
            await asyncio.wait_for(entered.wait(), 5)
            view = runtime.view
            assert isinstance(view, TaskWorkspace) and view._host_io_tasks
            running.cancel("first-cancellation")
            await asyncio.sleep(0)
            running.cancel("repeated-cancellation")
            # Observe an intentionally closed gate, not a production latency bound.
            done, _ = await asyncio.wait({running}, timeout=0.05)
            assert not done and not runtime.closed
            assert view._host_io_tasks
            with pytest.raises(ServiceBusy):
                with service.assignment(workspace_id):
                    pytest.fail("Assignment escaped while the owned thread held flock")
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 5)
            assert runtime.closed and runtime.agent is None
            assert not view._host_io_tasks
            assert service.status(receipt["init_task_id"])["status"] == "interrupted"
            with service.assignment(workspace_id):
                view.record_audit({"status": "after-owned-thread-release"})
            assert not list((view.history / "rejected").glob("*.json"))
        finally:
            release.set()
            if running is not None:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("primary", ["agent_error", "cancellation", "owned_io_error"])
def test_native_close_drains_owned_io_and_preserves_the_primary_failure(
    monkeypatch, tmp_path, primary
):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release, agent_closed = asyncio.Event(), threading.Event(), asyncio.Event()
        base = AuthorWorkspace(tmp_path / "workspace", sandbox=ToolStub())
        view = TaskWorkspace(
            base,
            {"files": {"payload.json": json.dumps({"source_id": "source"})}},
            {"id": "task", "kind": "init", "prompt": "Synthetic close barrier"},
        )
        runtime = XdogAgentRuntime(models())
        runtime.view = view
        first = RuntimeError("synthetic primary close error")
        late = AuthorWorkspaceError("synthetic late owned thread error")
        secondary = OSError("synthetic secondary capability close error")
        monkeypatch.setattr(
            view,
            "record_run",
            gated(view, view.record_run, entered, release, loop, failure=late),
        )

        class Agent:
            async def aclose(self):
                agent_closed.set()
                if primary == "agent_error":
                    raise first

        class Capabilities:
            closed = False

            async def aclose(self):
                self.closed = True
                raise secondary

        runtime.agent = Agent()
        capabilities = runtime.capabilities = Capabilities()
        unhandled = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: unhandled.append(context))
        io = asyncio.create_task(view.run_io(view.record_run, {"status": "synthetic"}))
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(agent_closed.wait(), 5)
            done, _ = await asyncio.wait({closing}, timeout=0.05)
            assert not done and not runtime.closed
            if primary == "cancellation":
                closing.cancel("first-cancellation")
                await asyncio.sleep(0)
                closing.cancel("repeated-cancellation")
                await asyncio.sleep(0)
                assert not closing.done() and view._host_io_tasks
            release.set()
            expected = (
                asyncio.CancelledError
                if primary == "cancellation"
                else type(first if primary == "agent_error" else late)
            )
            with pytest.raises(expected) as caught:
                await asyncio.wait_for(closing, 5)
            if primary == "cancellation":
                assert caught.value.args == ("first-cancellation",)
            else:
                assert caught.value is (first if primary == "agent_error" else late)
            assert (await asyncio.gather(io, return_exceptions=True)) == [late]
            assert runtime.closed and runtime.agent is None and capabilities.closed
            assert not view._host_io_tasks
            view.record_audit({"status": "lock-released"})
            await asyncio.sleep(0)
            assert unhandled == []
        finally:
            release.set()
            if closing is not None:
                closing.cancel()
            await asyncio.gather(io, *([closing] if closing else []), return_exceptions=True)
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_native_workspace_io_has_no_untracked_to_thread_calls():
    tree = ast.parse(inspect.getsource(XdogAgentRuntime))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "to_thread" for call in calls
    )
    operations = {
        call.args[0].attr
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "run_io"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
    }
    assert operations == {
        "validation_progress",
        "archive",
        "instructions",
        "load_candidate",
        "load_test",
        "record_test",
        "record_attempt",
        "seed_candidate",
        "record_run",
    }
