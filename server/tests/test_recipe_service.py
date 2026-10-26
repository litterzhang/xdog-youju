"""Managed service contracts: independent execution, persistence, fencing and CLI safety."""

import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from test_recipe_runtime import FakeRuntime, prepare

from youju.database import create_sqlite_engine, initialize_database
from youju.models import SourceRecipe
from youju.onboarding.recipes import candidate_digest, template_candidate
from youju.onboarding.store import digest, json_bytes
from youju.recipe_service import RecipeService, ServiceBusy, ServiceError
from youju.recipe_service.models import Job
from youju.recipe_service.task_worker import TaskWorker


def stage(service, location, *, revision, request_id):
    directory = Path(location["input_dir"])
    body = json_bytes(
        {
            "source_id": location["source_id"],
            "objective": "Build evidence-backed source recipes",
            "revision_note": revision,
        }
    )
    directory.joinpath("payload.json").write_bytes(body)
    manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
    directory.joinpath("manifest.json").write_bytes(json_bytes(manifest))
    return service.input_completed(
        location["workspace_id"],
        revision=revision,
        manifest_digest=digest(manifest),
        request_id=request_id,
    )


def test_submission_is_nonexecuting_and_independent_worker_never_opens_source_db(tmp_path):
    database = tmp_path / "source.sqlite3"
    engine = create_sqlite_engine(database)
    initialize_database(engine)
    service = RecipeService(tmp_path / "blobs")
    try:
        location, init_id = prepare(service)
        build = service.start_build(
            location["workspace_id"],
            input_revision="v1",
            prompt="Build saved material only.",
            request_id="build",
        )
        assert build["status"] == "waiting_init"
        assert service.status(init_id)["status"] == "queued"
        assert service.checkpoint(location["workspace_id"]) is None
        assert not Path(location["work_dir"]).joinpath("AGENTS.md").exists()
        prepared = {p.name: p.read_bytes() for p in Path(location["input_dir"]).iterdir()}
        # The sidecar owns only its metadata; business acceptance/audit is not a task outcome.
        assert "source_recipes" not in inspect(service.engine).get_table_names()
        code = """
import asyncio, json, sys
from test_recipe_runtime import FakeRuntime
from youju.recipe_service import RecipeService
from youju.recipe_service.task_worker import TaskWorker
service = RecipeService(sys.argv[1])
try:
    worker = TaskWorker(service, FakeRuntime)
    assert asyncio.run(worker.run_once(sys.argv[2]))['status'] == 'succeeded'
    print(json.dumps(asyncio.run(worker.run_once(sys.argv[3]))))
finally:
    service.close()
"""
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parent)}
        with database.with_suffix(".sqlite3.source.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.run(
                [sys.executable, "-c", code, str(service.storage), init_id, build["id"]],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
        assert child.returncode == 0, child.stderr
        completed = json.loads(child.stdout)
        assert completed["status"] == "candidate_ready" and completed["revision_id"]
        assert {e["type"] for e in completed["events"]} >= {
            "queued",
            "started",
            "turn",
            "candidate_ready",
        }
        assert prepared == {p.name: p.read_bytes() for p in Path(location["input_dir"]).iterdir()}
        assert service.workspace_info(location["workspace_id"])["accepted_recipe_digest"] is None
        with Session(engine) as session:
            assert session.scalar(select(SourceRecipe)) is None
    finally:
        service.close()
        engine.dispose()


def test_idempotency_cancel_live_owner_and_preserve_work_across_explicit_retry(tmp_path):
    service, other = RecipeService(tmp_path), RecipeService(tmp_path)
    try:
        location, init_id = prepare(service)
        ws = location["workspace_id"]
        assert service.create_workspace(location["source_id"]) == service.workspace_info(ws)
        note = Path(location["work_dir"]) / "note.txt"
        note.write_text("Keep prior debugging work")
        prepared = {p.name: p.read_bytes() for p in Path(location["input_dir"]).iterdir()}
        manifest_digest = digest(
            json.loads(Path(location["input_dir"], "manifest.json").read_text())
        )
        replay = other.input_completed(
            ws, revision="v1", manifest_digest=manifest_digest, request_id="input1"
        )
        assert replay["init_task_id"] == init_id
        build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
        assert (
            other.start_build(ws, input_revision="v1", prompt="Build", request_id="build")["id"]
            == build["id"]
        )
        with pytest.raises(ServiceBusy, match="pending"):
            other.start_build(ws, input_revision="v1", prompt="Other", request_id="conflict")

        async def cancel_live():
            started = asyncio.Event()

            class WaitingRuntime(FakeRuntime):
                async def execute_task(self, **kwargs):
                    started.set()
                    await asyncio.Future()

            runtime = WaitingRuntime()
            running = asyncio.create_task(
                TaskWorker(service, lambda: runtime, poll_seconds=0.01).run_once(init_id)
            )
            await asyncio.wait_for(started.wait(), 2)
            assert other.status(init_id)["status"] == "running"
            with pytest.raises(ServiceBusy, match="live owner"):
                other.recover(init_id)
            with pytest.raises(ServiceBusy):
                with other.assignment(ws):
                    pytest.fail("A second owner entered the live workspace")
            other.cancel(init_id)
            outcome = await asyncio.wait_for(running, 2)
            assert outcome["status"] == "cancelled" and runtime.closed

        asyncio.run(cancel_live())
        assert service.status(build["id"])["status"] == "blocked"
        assert prepared == {p.name: p.read_bytes() for p in Path(location["input_dir"]).iterdir()}
        retry = stage(other, location, revision="v2", request_id="retry")
        assert retry["init_task_id"] != init_id
        worker = TaskWorker(other, FakeRuntime)
        assert asyncio.run(worker.run_once())["status"] == "succeeded"
        next_build = other.start_build(ws, input_revision="v2", prompt="Build", request_id="resume")
        outcome = asyncio.run(worker.run_once())
        assert outcome["id"] == next_build["id"] and outcome["status"] == "candidate_ready"
        assert note.read_text() == "Keep prior debugging work"
        first = other.status(next_build["id"], limit=2)
        rest = other.status(next_build["id"], after=first["next_cursor"])
        events = first["events"] + rest["events"]
        assert all(e["sequence"] > first["next_cursor"] for e in rest["events"])
        assert len({e["sequence"] for e in events}) == len(events)
        assert {e["type"] for e in events} >= {"queued", "started", "candidate_ready"}
    finally:
        other.close()
        service.close()


def test_input_fingerprint_crash_recovery_stale_owner_and_cancel_before_start(tmp_path):
    service = RecipeService(tmp_path)
    try:
        location = service.create_workspace("source")
        directory = Path(location["input_dir"])
        body = json_bytes({"source_id": "source", "objective": "Extract saved evidence"})
        directory.joinpath("payload.json").write_bytes(body)
        manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
        directory.joinpath("manifest.json").write_bytes(json_bytes(manifest))
        directory.joinpath("payload.json").write_bytes(body + b" ")
        with pytest.raises(ServiceError, match="INPUT_CHANGED"):
            service.input_completed(
                location["workspace_id"],
                revision="bad",
                manifest_digest=digest(manifest),
                request_id="bad",
            )
        assert service.pending() == []
        receipt = stage(service, location, revision="v1", request_id="input")
        init_id = receipt["init_task_id"]
        # Simulate a dead process: the durable owner remains but its OS lock is released.
        with service.assignment(location["workspace_id"]):
            service.claim(init_id, "old-owner")
        assert service.recover(init_id)["status"] == "interrupted"
        with pytest.raises(ServiceError, match="Stale"):
            service.finish(init_id, "old-owner", "succeeded", result={})
        assert service.pending() == []  # Recovery does not silently replay models.
        retry = stage(service, location, revision="v2", request_id="retry")
        service.cancel(retry["init_task_id"])

        def forbidden():
            pytest.fail("Cancelled task constructed a runtime")

        assert asyncio.run(TaskWorker(service, forbidden).run_once()) is None
        assert digest({"source_id": "source"}) == location["workspace_id"]
    finally:
        service.close()


@pytest.mark.parametrize("tamper", ["context", "digest", "files"])
def test_changed_context_or_candidate_cannot_publish_revision(tmp_path, tamper):
    service = RecipeService(tmp_path)
    try:
        location, init_id = prepare(service)
        asyncio.run(TaskWorker(service, FakeRuntime).run_once(init_id))
        ws = location["workspace_id"]
        build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
        candidate = template_candidate()
        service.workspace(ws).write_candidate(candidate)
        result = {"candidate": candidate, "digest": candidate_digest(candidate)}
        with service.assignment(ws):
            service.claim(build["id"], "owner")
            if tamper == "context":
                with service._transaction() as session:
                    row = session.get(Job, build["id"])
                    row.context = {**row.context, "profile_revision": "wrong-owner"}
            elif tamper == "digest":
                result["digest"] = "0" * 64
            else:
                changed = template_candidate()
                changed["config"] = {"changed": True}
                service.workspace(ws).write_candidate(changed)
            with pytest.raises(ServiceError):
                service.finish(
                    build["id"],
                    "owner",
                    "candidate_ready",
                    result=result,
                    checkpoint={"opaque": "unaccepted"},
                )
        status = service.status(build["id"])
        assert status["revision_id"] is None and status["result_ref"] is None
        info = service.workspace_info(ws)
        assert info["latest_revision_id"] is None and info["accepted_recipe_digest"] is None
        assert info["session_revision"] == 1
        assert service.checkpoint(ws) == {"opaque": "test-session-after-init"}
    finally:
        service.close()


def test_cli_managed_commands_model_opt_in_and_removed_legacy_flags(tmp_path):
    from youju import create_app

    app = create_app({"TESTING": True})
    runner = app.test_cli_runner()
    root = str(tmp_path)
    created = runner.invoke(args=["recipe-workspace-create", "source", "--storage", root])
    assert created.exit_code == 0, created.output
    location = json.loads(created.output)
    assert Path(location["input_dir"]).is_dir()
    for args in (["recipe-worker"], ["recipe-workspace-create", "other", "--legacy"]):
        refused = runner.invoke(args=args)
        assert refused.exit_code != 0
        assert "No such" in refused.output
    refused = runner.invoke(args=["recipe-task-worker", "--storage", root])
    assert refused.exit_code != 0 and "explicit --enable-models" in refused.output
    app.config["RECIPE_RUNTIME_FACTORY"] = FakeRuntime
    refused = runner.invoke(args=["recipe-task-worker", "--storage", root])
    assert refused.exit_code != 0 and "explicit --enable-models" in refused.output
    # A configured test runtime is still explicitly authorized, without native/provider calls.
    with closing(RecipeService(tmp_path)) as service:
        receipt = stage(service, location, revision="v1", request_id="input")
    worked = runner.invoke(
        args=[
            "recipe-task-worker",
            "--storage",
            root,
            "--enable-models",
            "--job-id",
            receipt["init_task_id"],
        ]
    )
    assert worked.exit_code == 0, worked.output
    assert json.loads(worked.output)["status"] == "succeeded"
    help_text = runner.invoke(args=["recipe-task-worker", "--help"]).output
    for flag in ("--author-max-turns", "--max-turns", "--max-tokens", "--deadline"):
        assert flag not in help_text


def test_source_watch_releases_lock_between_passes(tmp_path, monkeypatch):
    from test_onboarding import setup_source

    from youju import create_app
    from youju.onboarding.cli import _store

    engine, store, _, _ = setup_source(tmp_path)
    runner = create_app({"TESTING": True}).test_cli_runner()
    sleeps = []

    class StubPipeline:
        def __init__(self, *args, **kwargs):
            pass

        async def step(self, *args, **kwargs):
            return {"stage": "authoring", "state": "running"}

    async def sleep(delay):
        with _store(tmp_path / "source.sqlite3", store.blobs.root):
            sleeps.append(delay)

    monkeypatch.setattr("youju.onboarding.pipeline.Pipeline", StubPipeline)
    monkeypatch.setattr("youju.onboarding.cli.asyncio.sleep", sleep)
    try:
        assert "--author-max-turns" not in runner.invoke(args=["source-step", "--help"]).output
        watched = runner.invoke(
            args=[
                "source-watch",
                "source",
                "--database",
                str(tmp_path / "source.sqlite3"),
                "--storage",
                str(store.blobs.root),
                "--cycles",
                "2",
                "--poll-seconds",
                "1",
            ]
        )
        assert watched.exit_code == 0, watched.output
        assert sleeps == [1]
    finally:
        engine.dispose()


def test_cli_revision_review_records_audit_without_implicit_repair(tmp_path):
    from youju import create_app

    app = create_app({"TESTING": True})
    runner = app.test_cli_runner()
    with closing(RecipeService(tmp_path)) as service:
        location, init_id = prepare(service)
        ws = location["workspace_id"]
        worker = TaskWorker(service, FakeRuntime)
        asyncio.run(worker.run_once(init_id))
        build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
        completed = asyncio.run(worker.run_once(build["id"]))
        revision_id = completed["revision_id"]
        checkpoint = service.checkpoint(ws)
        feedback = tmp_path / "audit.json"
        feedback.write_text(json.dumps({"audit": "synthetic-independent-audit-failed"}))
        args = [
            "recipe-review-revision",
            ws,
            "--revision-id",
            revision_id,
            "--decision",
            "reject",
            "--feedback",
            str(feedback),
            "--request-id",
            "reject",
            "--storage",
            str(tmp_path),
        ]
        reviewed = runner.invoke(args=args)
        assert reviewed.exit_code == 0, reviewed.output
        receipt = json.loads(reviewed.output)
        assert receipt["revision_id"] == revision_id and receipt["decision"] == "reject"
        assert receipt["feedback"] == {"audit": "synthetic-independent-audit-failed"}
        assert service.pending() == [] and service.checkpoint(ws) == checkpoint
        assert service.status(build["id"])["status"] == "candidate_ready"
        assert service.workspace_info(ws)["accepted_revision_id"] is None
        replay = runner.invoke(args=args)
        assert replay.exit_code == 0 and json.loads(replay.output) == receipt
        old = runner.invoke(args=["recipe-submit-feedback", "--help"])
        assert old.exit_code != 0 and "No such command" in old.output
        invalid = runner.invoke(
            args=[
                "recipe-review-revision",
                ws,
                "--revision-id",
                revision_id,
                "--decision",
                "repair",
                "--feedback",
                str(feedback),
                "--request-id",
                "repair",
                "--storage",
                str(tmp_path),
            ]
        )
        assert invalid.exit_code != 0 and "not one of" in invalid.output
        assert service.pending() == []


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}", b"x" * (256 * 1024 + 1)])
def test_cli_review_rejects_invalid_audit_before_opening_service(tmp_path, monkeypatch, body):
    from youju import create_app

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid audit reached service storage")

    monkeypatch.setattr("youju.recipe_service.cli.RecipeService", forbidden)
    feedback = tmp_path / "audit.json"
    feedback.write_bytes(body)
    runner = create_app({"TESTING": True}).test_cli_runner()
    result = runner.invoke(
        args=[
            "recipe-review-revision",
            "a" * 64,
            "--revision-id",
            "b" * 32,
            "--decision",
            "reject",
            "--feedback",
            str(feedback),
            "--request-id",
            "reject",
            "--storage",
            str(tmp_path),
        ]
    )
    assert result.exit_code != 0 and "Feedback" in result.output


def test_workspace_profile_conflict_requires_explicit_cutover_without_mutation(tmp_path):
    service = RecipeService(tmp_path)
    try:
        previous = service.create_workspace(
            "source", owner_id="source-server", agent_profile={"model": "old-model"}
        )
        key = previous["workspace_id"]
        with pytest.raises(ServiceError, match="^WORKSPACE_PROFILE_MIGRATION_REQUIRED$"):
            service.create_workspace(
                "source", owner_id="source-server", agent_profile={"model": "new-model"}
            )
        assert service.workspace_info(key) == previous
        assert service.pending() == []
        assert (
            service.create_workspace(
                "source", owner_id="source-server", agent_profile={"model": "old-model"}
            )
            == previous
        )
    finally:
        service.close()
