"""Managed worker contracts with a test-only runtime; no SDK/model calls or engines."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from youju.onboarding.recipes import candidate_digest, template_candidate
from youju.onboarding.store import digest, json_bytes
from youju.recipe_service import RecipeService, ServiceBusy, ServiceError
from youju.recipe_service.runtime import TaskOutcome
from youju.recipe_service.task_worker import MANAGED_KINDS, TaskWorker


@pytest.fixture
def service(tmp_path):
    value = RecipeService(tmp_path)
    try:
        yield value
    finally:
        value.close()


def prepare(service, *, revision="v1", request_id="input1", extra=None):
    location = service.create_workspace("ustc-runtime-test")
    directory = Path(location["input_dir"])
    payload = json_bytes(
        {
            "source_id": "ustc-runtime-test",
            "objective": "Build evidence-backed source recipes",
            **(extra or {}),
        }
    )
    manifest = {"payload.json": hashlib.sha256(payload).hexdigest()}
    (directory / "payload.json").write_bytes(payload)
    (directory / "manifest.json").write_bytes(json_bytes(manifest))
    submitted = service.input_completed(
        location["workspace_id"],
        revision=revision,
        manifest_digest=digest(manifest),
        request_id=request_id,
    )
    return location, submitted["init_task_id"]


class FakeRuntime:
    """Only pass opaque test state through the boundary; do not imitate an SDK."""

    def __init__(self):
        self.calls = []
        self.closed = False

    async def execute_task(self, **kwargs):
        self.calls.append(kwargs)
        await kwargs["on_event"](
            {
                "event": "turn",
                "turn": 1,
                "sequence": 12,
                "arguments": {"raw": "private diagnostic"},
                "response": "private diagnostic",
                "message": "private diagnostic",
            }
        )
        task, workspace = kwargs["task"], kwargs["workspace"]
        if task["kind"] == "init":
            workspace.workspace.joinpath("AGENTS.md").write_text(
                "# Source Instructions\n\nUse frozen input and evidence-backed output.\n",
                encoding="utf-8",
            )
            return TaskOutcome(
                "succeeded", result={}, checkpoint={"opaque": "test-session-after-init"}
            )
        candidate = template_candidate()
        workspace.write_candidate(candidate)
        return TaskOutcome(
            "candidate_ready",
            result={"candidate": candidate, "digest": candidate_digest(candidate)},
            checkpoint={"opaque": "test-session-after-build"},
        )

    async def aclose(self):
        self.closed = True
        self.calls = []  # Tests keep explicit references, not a live runtime context.


def test_missing_runtime_never_claims_or_changes_queue(service, monkeypatch):
    _, init_id = prepare(service)
    before = service.status(init_id)

    def unexpected(*args, **kwargs):
        pytest.fail("An unconfigured worker accessed its queue or started a runtime")

    with monkeypatch.context() as patch:
        patch.setattr(service, "pending", unexpected)
        patch.setattr(service, "claim", unexpected)
        patch.setattr(service, "status", unexpected)
        worker = TaskWorker(service)
        assert asyncio.run(worker.run_once(init_id)) == {
            "status": "runtime_unavailable",
            "error_code": "RUNTIME_UNAVAILABLE",
            "job_id": init_id,
        }
        assert asyncio.run(worker.run_once())["status"] == "runtime_unavailable"
    assert service.status(init_id) == before


def test_init_checkpoint_fresh_runtime_build_and_frozen_inputs(service, tmp_path):
    location, init_id = prepare(service)
    workspace_id = location["workspace_id"]
    build = service.start_build(
        workspace_id,
        input_revision="v1",
        prompt="Create the recipe using the submitted material.",
        request_id="build1",
        callback_target=None,
    )
    assert build["status"] == "waiting_init"
    # The runtime must receive the submitted snapshot, not this mutable staging file.
    Path(location["input_dir"]).joinpath("payload.json").write_text('{"objective":"changed"}')
    instances, calls = [], []

    class RecordingRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            calls.append(kwargs)
            return await super().execute_task(**kwargs)

    def factory():
        runtime = RecordingRuntime()
        instances.append(runtime)
        return runtime

    initialized = asyncio.run(TaskWorker(service, factory).run_once())
    assert initialized["id"] == init_id and initialized["status"] == "succeeded"
    assert service.workspace_info(workspace_id)["status"] == "prepared"
    assert instances[0].closed
    assert calls[0]["checkpoint"] is None
    checkpoint = service.checkpoint(workspace_id)
    assert checkpoint == {"opaque": "test-session-after-init"}
    assert service.workspace_info(workspace_id)["session_revision"] == 1
    # Recreate the service too: the test checkpoint lives on disk, not in an Agent.
    service.close()
    reopened = RecipeService(tmp_path)
    try:
        completed = asyncio.run(TaskWorker(reopened, factory).run_once())
        assert completed["id"] == build["id"] and completed["status"] == "candidate_ready"
        assert len(instances) == 2 and instances[0] is not instances[1]
        assert all(runtime.closed for runtime in instances)
        assert calls[1]["checkpoint"] == checkpoint
        assert calls[1]["task"]["prompt"] == "Create the recipe using the submitted material."
        assert calls[1]["agent_profile"]["runtime"] == "xdog-session-v1"
        assert calls[1]["agent_profile"]["skills"] == ["flow"]
        for call in calls:
            assert json.loads(call["inputs"]["files"]["payload.json"])["objective"] == (
                "Build evidence-backed source recipes"
            )
        assert reopened.workspace_info(workspace_id)["session_revision"] == 2
        assert reopened.checkpoint(workspace_id) == {"opaque": "test-session-after-build"}
        # Do not equate candidate generation with sample audit or business acceptance.
        assert reopened.workspace_info(workspace_id)["accepted_recipe_digest"] is None
        events = reopened.status(build["id"])["events"]
        turn = next(event for event in events if event["type"] == "turn")
        assert turn["agent_sequence"] == 12 and turn["untrusted"]
        assert "private diagnostic" not in json.dumps(events)
    finally:
        reopened.close()


@pytest.mark.parametrize("kind", ["unrecognized", "legacy_build"])
def test_worker_rejects_unsupported_task_kinds_even_for_explicit_id(service, monkeypatch, kind):
    seen = []

    def pending(*, kinds):
        seen.append(kinds)
        return ["unsupported"]

    def unexpected(*args, **kwargs):
        pytest.fail("Worker attempted to claim an unsupported task")

    monkeypatch.setattr(service, "pending", pending)
    monkeypatch.setattr(
        service,
        "status",
        lambda _: {"kind": kind, "status": "queued", "workspace_id": "unsupported"},
    )
    monkeypatch.setattr(service, "claim", unexpected)
    worker = TaskWorker(service, unexpected)
    assert asyncio.run(worker.run_once()) is None
    assert seen == [MANAGED_KINDS]
    assert asyncio.run(worker.run_once("unsupported")) is None


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("execute", "RUNTIME_TASK_FAILED"),
        ("close", "RUNTIME_CLOSE_FAILED"),
        ("checkpoint", "RUNTIME_CHECKPOINT_REQUIRED"),
        ("empty_checkpoint", "RUNTIME_CHECKPOINT_REQUIRED"),
        ("wrong_status", "RUNTIME_INVALID_OUTCOME"),
        ("missing_files", "RUNTIME_RESULT_REJECTED"),
    ],
)
def test_failed_runtime_closes_and_cannot_prepare_workspace(service, failure, code):
    location, init_id = prepare(service)

    class BrokenRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            if failure == "execute":
                raise RuntimeError("private provider diagnostic")
            if failure == "missing_files":
                return TaskOutcome("succeeded", checkpoint={"opaque": "unaccepted"})
            result = await super().execute_task(**kwargs)
            if failure in {"checkpoint", "empty_checkpoint"}:
                return TaskOutcome(
                    "succeeded", checkpoint={} if failure == "empty_checkpoint" else None
                )
            if failure == "wrong_status":
                return TaskOutcome("candidate_ready", checkpoint={"opaque": "wrong-kind"})
            return result

        async def aclose(self):
            await super().aclose()
            if failure == "close":
                raise RuntimeError("private provider diagnostic")

    runtime = BrokenRuntime()
    with pytest.raises(ServiceError, match=code):
        asyncio.run(TaskWorker(service, lambda: runtime).run_once(init_id))
    assert runtime.closed
    state = service.status(init_id)
    assert state["status"] == "failed" and state["error_code"] == code
    assert "private provider diagnostic" not in json.dumps(state)
    assert service.workspace_info(location["workspace_id"])["status"] != "prepared"
    assert service.checkpoint(location["workspace_id"]) is None
    with service.assignment(location["workspace_id"]):
        pass


@pytest.mark.parametrize("via_service", [True, False])
def test_cancellation_holds_assignment_until_close_and_leaks_no_tasks(service, via_service):
    location, init_id = prepare(service)

    async def scenario():
        baseline = asyncio.all_tasks()
        executing, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class WaitingRuntime(FakeRuntime):
            async def execute_task(self, **kwargs):
                executing.set()
                await asyncio.Future()

            async def aclose(self):
                closing.set()
                await release.wait()
                await super().aclose()

        runtime = WaitingRuntime()
        worker = TaskWorker(service, lambda: runtime, poll_seconds=0.01)
        running = asyncio.create_task(worker.run_once(init_id))
        await asyncio.wait_for(executing.wait(), 2)
        if via_service:
            service.cancel(init_id)
        else:
            running.cancel()
        await asyncio.wait_for(closing.wait(), 2)
        with pytest.raises(ServiceBusy):
            with service.assignment(location["workspace_id"]):
                pytest.fail("Released the assignment while the runtime was closing")
        running.cancel()  # A second cancellation must not interrupt cleanup.
        await asyncio.sleep(0)
        assert not running.done()
        release.set()
        if via_service:
            completed = await asyncio.wait_for(running, 2)
            assert completed["status"] == "cancelled"
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 2)
        assert runtime.closed
        assert service.status(init_id)["status"] == ("cancelled" if via_service else "interrupted")
        assert service.checkpoint(location["workspace_id"]) is None
        with service.assignment(location["workspace_id"]):
            pass
        assert not (asyncio.all_tasks() - baseline)

    asyncio.run(scenario())


@pytest.mark.parametrize("through_report", [True, False])
def test_status_persistence_failure_stops_and_closes_runtime(service, monkeypatch, through_report):
    _, init_id = prepare(service)
    original = service.progress
    calls = 0

    def progress(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("private storage diagnostic")
        return original(*args, **kwargs)

    class ReportingRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            if through_report:
                await kwargs["on_event"]({"type": "turn", "turn": 1})
            await asyncio.Future()

    runtime = ReportingRuntime()
    monkeypatch.setattr(service, "progress", progress)
    with pytest.raises(ServiceError, match="RUNTIME_TASK_FAILED"):
        asyncio.run(TaskWorker(service, lambda: runtime, poll_seconds=0.01).run_once(init_id))
    assert runtime.closed and service.status(init_id)["status"] == "failed"
    assert "private storage diagnostic" not in json.dumps(service.status(init_id))


def test_checkpoint_events_persist_before_next_step_and_never_leak(service):
    location, init_id = prepare(service)
    safe = {"opaque": "private-sdk-history"}

    class CheckpointRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            await kwargs["on_event"]({"type": "checkpoint", "checkpoint": safe})
            assert service.checkpoint(location["workspace_id"]) == safe
            assert service.status(init_id)["base_session_revision"] == 1
            await kwargs["on_event"](
                {"type": "checkpoint", "checkpoint": {"opaque": "unsafe-tool"}, "recovery": True}
            )
            assert service.checkpoint(location["workspace_id"]) == safe
            return await super().execute_task(**kwargs)

    runtime = CheckpointRuntime()
    result = asyncio.run(TaskWorker(service, lambda: runtime).run_once(init_id))
    assert result["status"] == "succeeded"
    assert runtime.closed and result["recovery_ref"]
    assert service.workspace_info(location["workspace_id"])["session_revision"] == 2
    saved = [e for e in result["events"] if e["type"] == "checkpoint_saved"]
    assert [e["recovery"] for e in saved] == [False, True]
    assert "private-sdk-history" not in json.dumps(result)
    assert "unsafe-tool" not in json.dumps(result)


def test_checkpoint_persistence_failure_stops_runtime(service, monkeypatch):
    _, init_id = prepare(service)
    continued = False

    def fail(*args, **kwargs):
        raise OSError("private checkpoint storage error")

    class CheckpointRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            nonlocal continued
            await kwargs["on_event"]({"type": "checkpoint", "checkpoint": {"opaque": "state"}})
            continued = True
            return await super().execute_task(**kwargs)

    runtime = CheckpointRuntime()
    monkeypatch.setattr(service, "save_checkpoint", fail)
    with pytest.raises(ServiceError, match="RUNTIME_TASK_FAILED"):
        asyncio.run(TaskWorker(service, lambda: runtime).run_once(init_id))
    assert not continued and runtime.closed
    assert service.status(init_id)["status"] == "failed"
    assert "private checkpoint storage error" not in json.dumps(service.status(init_id))


def test_cancellation_keeps_safe_checkpoint_and_workspace_work(service):
    location, init_id = prepare(service)

    class CancelRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            kwargs["workspace"].workspace.joinpath("notes.txt").write_text("work in progress")
            await kwargs["on_event"](
                {"type": "checkpoint", "checkpoint": {"opaque": "safe-before-cancel"}}
            )
            service.cancel(init_id)
            await kwargs["on_event"]({"type": "turn", "turn": 2})
            pytest.fail("Runtime continued after cancellation")

    runtime = CancelRuntime()
    result = asyncio.run(TaskWorker(service, lambda: runtime).run_once(init_id))
    assert result["status"] == "cancelled" and runtime.closed
    assert service.checkpoint(location["workspace_id"]) == {"opaque": "safe-before-cancel"}
    assert Path(location["work_dir"]).joinpath("notes.txt").read_text() == "work in progress"
    assert service.pending() == []


def test_progress_usage_metadata_bounded_and_no_untrusted_exception_codes():
    from youju.recipe_service.task_worker import _failure_code, _progress_metadata

    metadata = _progress_metadata(
        {
            "type": "usage",
            "requests": 3,
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
            "input": True,
            "output": -1,
            "total": 2**64,
            "message": "private report text",
        }
    )
    assert metadata == {
        "type": "usage",
        "untrusted": True,
        "requests": 3,
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
    }
    unrelated = RuntimeError("NATIVE_CHECKPOINT_MISMATCH")
    unrelated.code = "NATIVE_CHECKPOINT_MISMATCH"
    assert _failure_code(unrelated) == "RUNTIME_TASK_FAILED"


def test_native_host_error_code_preserved_not_provider_text(service):
    from youju.recipe_service.native_runtime import NativeRuntimeError

    _, init_id = prepare(service)

    class NativeFailureRuntime(FakeRuntime):
        async def execute_task(self, **kwargs):
            raise NativeRuntimeError("NATIVE_CHECKPOINT_MISMATCH")

    runtime = NativeFailureRuntime()
    with pytest.raises(ServiceError, match="NATIVE_CHECKPOINT_MISMATCH"):
        asyncio.run(TaskWorker(service, lambda: runtime).run_once(init_id))
    assert service.status(init_id)["error_code"] == "NATIVE_CHECKPOINT_MISMATCH"
    assert runtime.closed


class ChangedCandidateRuntime(FakeRuntime):
    async def execute_task(self, **kwargs):
        outcome = await super().execute_task(**kwargs)
        if kwargs["task"]["kind"] == "init":
            return outcome
        candidate = template_candidate()
        candidate["config"]["structure_notes"] = "Synthetic repaired layout, revision 2"
        kwargs["workspace"].write_candidate(candidate)
        return TaskOutcome(
            "candidate_ready",
            result={"candidate": candidate, "digest": candidate_digest(candidate)},
            checkpoint={"opaque": "test-session-after-repair"},
        )


def test_reject_review_is_nonexecuting_and_explicit_repair_requires_fresh_input(service):
    location, init_id = prepare(service)
    ws = location["workspace_id"]
    worker = TaskWorker(service, FakeRuntime)
    assert asyncio.run(worker.run_once(init_id))["status"] == "succeeded"
    build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
    completed = asyncio.run(worker.run_once(build["id"]))
    revision = service.get_recipe_revision(completed["revision_id"])
    checkpoint = service.checkpoint(ws)
    review_args = {
        "revision_id": revision["id"],
        "decision": "reject",
        "feedback": {"audit": "synthetic-source-regression", "errors": ["field mapping"]},
        "request_id": "reject",
    }
    rejected = service.review_revision(ws, **review_args)
    assert rejected["decision"] == "reject"
    assert service.review_revision(ws, **review_args) == rejected
    assert service.pending() == []
    assert service.checkpoint(ws) == checkpoint
    assert service.status(build["id"])["status"] == "candidate_ready"
    assert service.workspace_info(ws)["accepted_revision_id"] is None
    repair_args = {
        "base_revision_id": revision["id"],
        "base_recipe_digest": revision["artifact_digest"],
        "feedback": {"audit": "synthetic-source-regression", "errors": ["field mapping"]},
        "request_id": "repair",
    }
    with pytest.raises(ServiceError, match="FRESH_INPUT_REQUIRED"):
        service.start_repair(ws, input_revision="v1", **repair_args)
    assert service.pending() == []
    prepare(service, revision="v2", request_id="input2", extra={"new_material": True})
    repair = service.start_repair(ws, input_revision="v2", **repair_args)
    assert repair["kind"] == "repair" and repair["status"] == "queued"
    assert repair["base_revision_id"] == revision["id"]
    assert repair["input_revision_id"] != build["input_revision_id"]
    assert (
        "new_material"
        in service.input_snapshot(repair["input_revision_id"])["files"]["payload.json"]
    )
    prompt = service.task_prompt(repair["id"])
    assert prompt["feedback"]["details"] == repair_args["feedback"]
    assert service.workspace_info(ws)["accepted_revision_id"] is None
    completed = asyncio.run(TaskWorker(service, ChangedCandidateRuntime).run_once(repair["id"]))
    assert completed["status"] == "candidate_ready"
    repaired = service.get_recipe_revision(completed["revision_id"])
    assert repaired["base_revision_id"] == revision["id"]
    assert repaired["artifact_digest"] != revision["artifact_digest"]
    assert service.get_recipe_revision(revision["id"]) == revision
    accepted = service.review_revision(
        ws,
        revision_id=repaired["id"],
        decision="accept",
        feedback={"audit": "synthetic-repaired-source-passed"},
        request_id="accept",
    )
    assert accepted["artifact_digest"] == repaired["artifact_digest"]
    assert service.workspace_info(ws)["accepted_revision_id"] == repaired["id"]
    assert service.pending() == []


def test_revision_review_rejects_stale_base_not_merely_an_older_candidate(service):
    location, init_id = prepare(service)
    ws = location["workspace_id"]
    worker = TaskWorker(service, FakeRuntime)
    asyncio.run(worker.run_once(init_id))
    first = service.start_build(ws, input_revision="v1", prompt="Build", request_id="first")
    older = asyncio.run(worker.run_once(first["id"]))
    old_revision = service.get_recipe_revision(older["revision_id"])
    second = service.start_build(ws, input_revision="v1", prompt="Rebuild", request_id="second")
    newer = asyncio.run(TaskWorker(service, ChangedCandidateRuntime).run_once(second["id"]))
    # Newer execution does not make a saved older candidate stale by itself.
    service.review_revision(
        ws,
        revision_id=older["revision_id"],
        decision="accept",
        feedback={"audit": "independent-older-candidate-passed"},
        request_id="accept-older",
    )
    with pytest.raises(ServiceError, match="STALE_RECIPE_BASE"):
        service.review_revision(
            ws,
            revision_id=newer["revision_id"],
            decision="accept",
            feedback={"audit": "cannot-overwrite-changed-base"},
            request_id="stale-accept",
        )
    assert service.get_recipe_revision(older["revision_id"]) == old_revision
    assert service.workspace_info(ws)["accepted_revision_id"] == older["revision_id"]
    assert service.status(second["id"])["status"] == "candidate_ready"
    assert service.pending() == []
