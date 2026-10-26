"""Offline cancellation ownership checks for host author-workspace I/O threads."""

import asyncio
import json
import threading

import pytest
import xdog.ai as ai
from test_onboarding import SyntheticAuthorStreamFactory
from test_xdog_recipes import NOW, FakeSandbox, bundle

from youju.onboarding.author_workspace import AuthorWorkspace, AuthorWorkspaceError
from youju.onboarding.recipes import RecipeAuthor, RecipeRunner, template_candidate
from youju.onboarding.xdog import XdogModels


def gated_io(workspace, operation, entered, release, loop, *, failure=None):
    original = getattr(workspace, operation)

    def gated(*args, **kwargs):
        # Keep the real nonblocking workspace flock held across cancellation.
        with workspace._directories():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError("test did not release its own I/O thread")
            if failure is not None:
                raise failure
        return original(*args, **kwargs)

    return gated


@pytest.mark.parametrize("operation", ["load_candidate", "record_attempt"])
def test_failed_progress_writer_waits_for_cancelled_host_io(monkeypatch, tmp_path, operation):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    failure = RuntimeError("private-first-persistence-failure")
    release = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        entered, cancelling = asyncio.Event(), asyncio.Event()
        workspace = AuthorWorkspace(tmp_path / "source")
        monkeypatch.setattr(
            workspace, operation, gated_io(workspace, operation, entered, release, loop)
        )
        models = XdogModels()

        async def fake_agent(*, submit_tool, on_update, **kwargs):
            models.calls.append({"status": "failed", "error_code": "AUTHOR_HOST_FAILURE"})
            submission = asyncio.create_task(
                submit_tool.execute("synthetic-submit", {"result": {"ready": True}})
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await on_update({"event": "agent_report"})
            finally:
                submission.cancel()
                cancelling.set()
                await asyncio.gather(submission, return_exceptions=True)

        monkeypatch.setattr(models, "run_author_agent", fake_agent)

        async def on_update(event):
            raise failure

        task = asyncio.create_task(
            RecipeAuthor(models, RecipeRunner(FakeSandbox(), models), workspace=workspace).author(
                [bundle()], now_iso=NOW, on_update=on_update
            )
        )
        try:
            await asyncio.wait_for(cancelling.wait(), 5)
            # This short wait observes an intentionally closed test gate, not an I/O SLA.
            done, _ = await asyncio.wait({task}, timeout=0.1)
            release.set()
            with pytest.raises(RuntimeError) as caught:
                await asyncio.wait_for(task, 5)
            assert caught.value is failure
            assert not done, "Cancellation escaped while the owned thread still held flock"
            run = json.loads((workspace.history / "runs/1.json").read_text())
            assert run["status"] == "failed" and run["error_code"] == "AUTHOR_HOST_FAILURE"
            assert "private-first-persistence-failure" not in json.dumps(run)
            workspace.record_audit({"status": "after_cleanup"})
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("late_failure", [False, True], ids=["write", "failed-write"])
def test_repeated_cancellation_joins_thread_and_retrieves_late_failure(
    monkeypatch, tmp_path, late_failure
):
    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        workspace = AuthorWorkspace(tmp_path / "source")
        failure = OSError("private-late-thread-error") if late_failure else None
        monkeypatch.setattr(
            workspace,
            "record_run",
            gated_io(workspace, "record_run", entered, release, loop, failure=failure),
        )
        unhandled = []
        loop.set_exception_handler(lambda _, context: unhandled.append(context))
        task = asyncio.create_task(workspace.run_io(workspace.record_run, {"status": "checkpoint"}))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel("first-cancellation")
            await asyncio.sleep(0)
            task.cancel("second-cancellation")
            await asyncio.sleep(0)
            assert not task.done()
            assert workspace._host_io_tasks
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await asyncio.wait_for(task, 5)
            assert caught.value.args == ("first-cancellation",)
            assert not workspace._host_io_tasks
            await workspace.wait_io()
            workspace.record_audit({"status": "lock_released"})
            await asyncio.sleep(0)
            assert unhandled == []
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("origin", ["progress", "attempt"])
def test_first_persistence_error_survives_failed_final_history(monkeypatch, tmp_path, origin):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    failure = RuntimeError("private-first-persistence-error")
    secondary = OSError("private-final-history-error")
    workspace = AuthorWorkspace(tmp_path / "source")
    final_calls = []

    def fail_final(record):
        final_calls.append(record)
        raise secondary

    def fail_attempt(attempt):
        raise failure

    monkeypatch.setattr(workspace, "record_run", fail_final)
    if origin == "attempt":
        monkeypatch.setattr(workspace, "record_attempt", fail_attempt)
    factory = SyntheticAuthorStreamFactory(lambda *_: template_candidate())
    models = XdogModels(author_stream_factory=factory)

    async def on_update(event):
        if origin == "progress" and event["event"] == "started":
            raise failure

    async def scenario():
        with pytest.raises(RuntimeError) as caught:
            await RecipeAuthor(
                models, RecipeRunner(FakeSandbox(), models), workspace=workspace
            ).author([bundle()], now_iso=NOW, on_update=on_update)
        assert caught.value is failure
        assert len(final_calls) == 1 and final_calls[0]["status"] == "failed"
        assert final_calls[0]["error_code"] == "AUTHOR_HOST_FAILURE"
        assert not workspace._host_io_tasks
        assert "private-" not in json.dumps([models.calls, final_calls])

    asyncio.run(scenario())


def test_final_history_failure_cannot_return_accepted_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    failure = OSError("synthetic final-history failure")
    workspace = AuthorWorkspace(tmp_path / "source")

    def fail_final(record):
        raise failure

    monkeypatch.setattr(workspace, "record_run", fail_final)
    models = XdogModels(
        author_stream_factory=SyntheticAuthorStreamFactory(lambda *_: template_candidate())
    )

    async def scenario():
        with pytest.raises(OSError) as caught:
            await RecipeAuthor(
                models, RecipeRunner(FakeSandbox(), models), workspace=workspace
            ).author([bundle()], now_iso=NOW)
        assert caught.value is failure
        assert not workspace._host_io_tasks
        assert not (workspace.history / "runs/1.json").exists()

    asyncio.run(scenario())


def test_external_workspace_owner_is_still_rejected_without_lock_retry(tmp_path):
    async def scenario():
        workspace = AuthorWorkspace(tmp_path / "source")
        other = AuthorWorkspace(workspace.root)
        task = None
        try:
            with other._directories():
                task = asyncio.create_task(
                    workspace.run_io(workspace.record_run, {"status": "test"})
                )
                done, _ = await asyncio.wait({task}, timeout=1)
                assert done, "Host I/O must not wait/retry an unrelated owner's flock"
                with pytest.raises(AuthorWorkspaceError, match="^author_workspace_busy$"):
                    await task
            assert not workspace._host_io_tasks
            workspace.record_run({"status": "after_external_release"})
        finally:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_final_status_waits_for_owned_io_after_interrupted_agent_close(monkeypatch, tmp_path):
    """Match the SDK's shielded close: parent cancellation can interrupt the close waiter."""
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    release = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        entered, closing = asyncio.Event(), asyncio.Event()
        workspace = AuthorWorkspace(tmp_path / "source")
        monkeypatch.setattr(
            workspace,
            "load_candidate",
            gated_io(workspace, "load_candidate", entered, release, loop),
        )
        models = XdogModels()
        cleanup = None

        async def fake_agent(*, submit_tool, **kwargs):
            nonlocal cleanup
            models.calls.append({"status": "cancelled", "error_code": "MODEL_CANCELLED"})
            submission = asyncio.create_task(
                submit_tool.execute("synthetic-submit", {"result": {"ready": True}})
            )
            await asyncio.wait_for(entered.wait(), 5)
            submission.cancel()

            async def close():
                await asyncio.gather(submission, return_exceptions=True)

            cleanup = asyncio.create_task(close())
            closing.set()
            await asyncio.shield(cleanup)
            raise asyncio.CancelledError

        monkeypatch.setattr(models, "run_author_agent", fake_agent)
        task = asyncio.create_task(
            RecipeAuthor(models, RecipeRunner(FakeSandbox(), models), workspace=workspace).author(
                [bundle()], now_iso=NOW
            )
        )
        try:
            await asyncio.wait_for(closing.wait(), 5)
            task.cancel("interrupted-close")
            await asyncio.sleep(0)
            task.cancel("repeated-cancellation")
            done, _ = await asyncio.wait({task}, timeout=0.1)
            assert not done
            assert not (workspace.history / "runs/1.json").exists()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            await asyncio.wait_for(cleanup, 5)
            assert not workspace._host_io_tasks
            run = json.loads((workspace.history / "runs/1.json").read_text())
            assert run["status"] == "cancelled" and run["error_code"] == "MODEL_CANCELLED"
            workspace.record_audit({"status": "after_close"})
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, *([cleanup] if cleanup else []), return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
