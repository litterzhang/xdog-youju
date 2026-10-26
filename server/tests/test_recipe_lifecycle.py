"""Host-only lifecycle regressions. No SDK runtime, model calls or real callbacks."""

import hashlib
import sqlite3

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.onboarding.recipes import candidate_digest, template_candidate
from youju.onboarding.store import digest, json_bytes
from youju.recipe_service import RecipeService, ServiceBusy, ServiceError
from youju.recipe_service.models import InputRevision, Job, Outbox, Workspace


def stage(service, workspace_id, *, extra=None, objective="Extract evidence-backed positions"):
    work = service.workspace(workspace_id)
    payload = {"source_id": "source", "objective": objective, **(extra or {})}
    body = json_bytes(payload)
    (work.input / "payload.json").write_bytes(body)
    manifest = {"payload.json": hashlib.sha256(body).hexdigest()}
    (work.input / "manifest.json").write_bytes(json_bytes(manifest))
    return digest(manifest)


def finish_init(service, task_id, owner="init-owner"):
    task = service.status(task_id)
    with service._assignment(task["workspace_id"]):
        service.claim(task_id, owner)
        work = service.workspace(task["workspace_id"])
        (work.workspace / "AGENTS.md").write_text(
            "# Source Guidelines\n\nSynthetic init fixture.\n"
        )
        service.finish(task_id, owner, "succeeded", result={}, checkpoint={"fixture": "init"})


def finish_build(service, task_id, candidate=None, owner="build-owner"):
    candidate = candidate or template_candidate()
    task = service.status(task_id)
    with service._assignment(task["workspace_id"]):
        service.claim(task_id, owner)
        service.workspace(task["workspace_id"]).write_candidate(candidate)
        service.finish(
            task_id,
            owner,
            "candidate_ready",
            result={
                "candidate": candidate,
                "digest": candidate_digest(candidate),
            },
            checkpoint={"fixture": task_id},
        )
    return candidate_digest(candidate)


def test_v1_migration_preserves_workspace_jobs_events_and_files(tmp_path):
    root = tmp_path / "authoring"
    root.mkdir()
    workspace_id = digest({"source_id": "source"})
    path = root / workspace_id / "workspace"
    path.mkdir(parents=True)
    sentinel = path / "AGENTS.md"
    sentinel.write_text("# Existing file\nDo not overwrite or assume initialized.\n")
    original = sentinel.read_bytes()
    database = root / "service.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, source_id VARCHAR(128) UNIQUE NOT NULL);
CREATE TABLE jobs (
 id VARCHAR(32) PRIMARY KEY, workspace_id VARCHAR(64) NOT NULL,
 request_id VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL,
 input_digest VARCHAR(64) NOT NULL, context JSON NOT NULL,
 result_ref VARCHAR(64), error_code VARCHAR(64), owner VARCHAR(32),
 cancel_requested BOOLEAN NOT NULL, created_at VARCHAR(40) NOT NULL,
 updated_at VARCHAR(40) NOT NULL, UNIQUE (workspace_id, request_id)
);
CREATE TABLE job_events (id INTEGER PRIMARY KEY, job_id VARCHAR(32) NOT NULL,
 created_at VARCHAR(40) NOT NULL, data JSON NOT NULL);
""")
        connection.execute("INSERT INTO workspaces VALUES (?,?)", (workspace_id, "source"))
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old-job",
                workspace_id,
                "old-request",
                "candidate_ready",
                "a" * 64,
                '{"legacy":true}',
                "b" * 64,
                None,
                None,
                False,
                "old-created",
                "old-updated",
            ),
        )
        connection.execute(
            "INSERT INTO job_events VALUES (?,?,?,?)",
            (
                7,
                "old-job",
                "old-created",
                '{"type":"candidate_ready"}',
            ),
        )
    for _ in range(2):
        service = RecipeService(tmp_path)
        try:
            workspace = service.workspace_info(workspace_id)
            assert workspace["physical_path"] == str(path.parent)
            assert workspace["lifecycle"] == "legacy" and workspace["status"] == "waiting_input"
            assert workspace["checkpoint_ref"] is None and workspace["session_revision"] == 0
            old = service.status("old-job")
            assert old["result_ref"] == "b" * 64 and old["context"] == {"legacy": True}
            assert old["kind"] == "legacy_build" and old["created_at"] == "old-created"
            assert old["events"][0]["sequence"] == 7
            with pytest.raises(ServiceError, match="^WORKSPACE_PROFILE_MIGRATION_REQUIRED$"):
                service.create_workspace("source")
        finally:
            service.close()
        assert sentinel.read_bytes() == original
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "prior_context_ref" in {
            row[1] for row in connection.execute("PRAGMA table_info(workspaces)")
        }
        assert connection.execute("SELECT prior_context_ref FROM workspaces").fetchone()[0] is None
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="newer"):
        RecipeService(tmp_path)
    assert sentinel.read_bytes() == original


def test_input_commit_idempotency_init_dependencies_and_cancel(tmp_path):
    service = RecipeService(tmp_path)
    try:
        info = service.create_workspace("source")
        ws = info["workspace_id"]
        assert info["status"] == "waiting_input" and service.pending() == []
        assert service.create_workspace("source") == info
        with pytest.raises(ServiceError, match="different caller"):
            service.create_workspace("source", owner_id="other")
        with pytest.raises(ServiceError, match="INPUT_NOT_READY"):
            service.start_build(ws, input_revision="v1", prompt="Build", request_id="early")
        manifest = stage(service, ws)
        with pytest.raises(ServiceError, match="MANIFEST_CHANGED"):
            service.input_completed(ws, revision="v1", manifest_digest="0" * 64, request_id="bad")
        assert service.pending() == []
        receipt = service.input_completed(
            ws,
            revision="v1",
            manifest_digest=manifest,
            request_id="input-1",
            callback_target="caller",
        )
        assert service.workspace_info(ws)["status"] == "initializing"
        work = service.workspace(ws)
        assert not (work.workspace / "AGENTS.md").exists()  # The host did not impersonate init.
        work.input.joinpath("payload.json").write_text("mutable staging changed")
        duplicate = service.input_completed(
            ws,
            revision="v1",
            manifest_digest=manifest,
            request_id="input-1",
            callback_target="caller",
        )
        assert duplicate == receipt
        frozen = service.input_snapshot(receipt["input_revision_id"])
        assert "Extract evidence-backed" in frozen["files"]["payload.json"]
        with pytest.raises(ServiceError, match="REQUEST_CONFLICT"):
            service.input_completed(
                ws, revision="different", manifest_digest=manifest, request_id="input-1"
            )
        build = service.start_build(
            ws, input_revision="v1", prompt="Build", request_id="build-1", callback_target="caller"
        )
        assert build["status"] == "waiting_init" and build["depends_on"] == receipt["init_task_id"]
        assert (
            service.start_build(
                ws,
                input_revision="v1",
                prompt="Build",
                request_id="build-1",
                callback_target="caller",
            )["id"]
            == build["id"]
        )
        with pytest.raises(ServiceBusy):
            service.start_build(ws, input_revision="v1", prompt="Other", request_id="build-2")
        service.cancel(receipt["init_task_id"])
        assert service.workspace_info(ws)["status"] == "init_failed"
        assert service.status(build["id"])["status"] == "blocked"
        service.cancel(receipt["init_task_id"])  # No duplicate final callback.
        with Session(service.engine) as session:
            assert session.scalar(select(func.count()).select_from(Outbox)) == 2
            assert session.scalar(select(func.count()).select_from(InputRevision)) == 1
        manifest = stage(service, ws)
        retry = service.input_completed(
            ws, revision="v2", manifest_digest=manifest, request_id="input-2"
        )
        finish_init(service, retry["init_task_id"])
        assert service.workspace_info(ws)["status"] == "prepared"
        with Session(service.engine) as session:
            init_count = session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "init")
            )
        manifest = stage(service, ws, extra={"samples": [{"id": "new"}]})
        next_input = service.input_completed(
            ws, revision="v3", manifest_digest=manifest, request_id="input-3"
        )
        assert next_input["init_task_id"] == retry["init_task_id"]
        with Session(service.engine) as session:
            assert (
                session.scalar(select(func.count()).select_from(Job).where(Job.kind == "init"))
                == init_count
            )
        changed = stage(service, ws, objective="Different source contract")
        with pytest.raises(ServiceError, match="REINITIALIZATION_REQUIRED"):
            service.input_completed(
                ws, revision="v4", manifest_digest=changed, request_id="input-4"
            )
    finally:
        service.close()


def test_accept_feedback_repair_and_stale_submission_fences(tmp_path):
    service = RecipeService(tmp_path)
    try:
        ws = service.create_workspace("source")["workspace_id"]
        receipt = service.input_completed(
            ws, revision="v1", manifest_digest=stage(service, ws), request_id="input"
        )
        build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
        finish_init(service, receipt["init_task_id"])
        assert service.status(build["id"])["status"] == "queued"
        original = finish_build(service, build["id"])
        accepted = service.review_revision(
            ws,
            revision_id=service.status(build["id"])["revision_id"],
            decision="accept",
            feedback={"audit_ref": "fixture-audit"},
            request_id="accept",
        )
        assert accepted["decision"] == "accept"
        assert service.workspace_info(ws)["accepted_recipe_digest"] == original
        service.input_completed(
            ws,
            revision="v2",
            manifest_digest=stage(service, ws, extra={"new": True}),
            request_id="repair-input",
        )
        task = service.start_repair(
            ws,
            input_revision="v2",
            base_recipe_digest=original,
            feedback={"errors": ["fixture-field-error"]},
            request_id="repair",
        )
        assert task["kind"] == "repair" and task["base_recipe_digest"] == original
        with service._assignment(ws):
            service.claim(task["id"], "repair-owner")
            with pytest.raises(ServiceError, match="REPAIR_UNCHANGED"):
                service.finish(
                    task["id"],
                    "repair-owner",
                    "candidate_ready",
                    result={
                        "candidate": template_candidate(),
                        "digest": original,
                    },
                    checkpoint={"fixture": "unchanged"},
                )
            candidate = template_candidate()
            candidate["config"]["revision"] = 2
            service.workspace(ws).write_candidate(candidate)
            new_digest = candidate_digest(candidate)
            service.finish(
                task["id"],
                "repair-owner",
                "candidate_ready",
                result={
                    "candidate": candidate,
                    "digest": new_digest,
                },
                checkpoint={"fixture": "repaired"},
            )
        assert service.workspace_info(ws)["accepted_recipe_digest"] == original
        with pytest.raises(ServiceError, match="Stale"):
            service.finish(task["id"], "repair-owner", "failed")
        with pytest.raises(ServiceError, match="REVISION_ALREADY_REVIEWED"):
            service.review_revision(
                ws,
                revision_id=service.status(build["id"])["revision_id"],
                decision="accept",
                feedback={"passed": True},
                request_id="stale-accept",
            )
        service.review_revision(
            ws,
            revision_id=service.status(task["id"])["revision_id"],
            decision="accept",
            feedback={"passed": True},
            request_id="accept-new",
        )
        assert service.workspace_info(ws)["accepted_recipe_digest"] == new_digest
        assert service.workspace_info(ws)["session_revision"] == 3
        assert service.checkpoint(ws) == {"fixture": "repaired"}
    finally:
        service.close()


def test_checkpoint_safe_and_recovery_fences_cancellation_and_v2_migration(tmp_path):
    service = RecipeService(tmp_path)
    try:
        ws = service.create_workspace("source")["workspace_id"]
        receipt = service.input_completed(
            ws, revision="v1", manifest_digest=stage(service, ws), request_id="input"
        )
        task_id = receipt["init_task_id"]
        with service._assignment(ws):
            service.claim(task_id, "owner")
            safe = {"opaque": "private-session-content"}
            ref = service.save_checkpoint(task_id, "owner", safe)
            assert service.checkpoint(ws) == safe
            assert service.workspace_info(ws)["session_revision"] == 1
            assert service.status(task_id)["base_session_revision"] == 1
            recovery = {"opaque": "unfinished-tool-state"}
            recovery_ref = service.save_checkpoint(task_id, "owner", recovery, recovery=True)
            assert service.status(task_id)["recovery_ref"] == recovery_ref
            assert service.checkpoint(ws) == safe
            assert service.workspace_info(ws)["session_revision"] == 1
            for invalid in (None, [], {}, {"value": float("nan")}):
                with pytest.raises(ServiceError):
                    service.save_checkpoint(task_id, "owner", invalid)
            with pytest.raises(ServiceError, match="Stale job owner"):
                service.save_checkpoint(task_id, "wrong-owner", {"opaque": "wrong"})
            with service._transaction() as session:
                session.get(Workspace, ws).session_revision += 1
            with pytest.raises(ServiceError, match="Stale session revision"):
                service.save_checkpoint(task_id, "owner", {"opaque": "stale"})
            with service._transaction() as session:
                session.get(Workspace, ws).session_revision -= 1
            for field, message in (
                ("profile_revision", "Agent profile changed"),
                ("accepted_recipe_digest", "Stale recipe base"),
                ("init_task_id", "Stale init task"),
            ):
                with service._transaction() as session:
                    row = session.get(Workspace, ws)
                    original = getattr(row, field)
                    setattr(row, field, "changed")
                with pytest.raises(ServiceError, match=message):
                    service.save_checkpoint(task_id, "owner", {"opaque": "stale"})
                with service._transaction() as session:
                    setattr(session.get(Workspace, ws), field, original)
            service.cancel(task_id)
            service.finish(task_id, "owner", "cancelled")
            assert service.checkpoint(ws) == safe
            assert service.status(task_id)["checkpoint_ref"] == ref
            assert b"private-session-content" not in json_bytes(service.status(task_id))
            assert b"unfinished-tool-state" not in json_bytes(service.status(task_id))
        with pytest.raises(ServiceError, match="Stale job owner"):
            service.save_checkpoint(task_id, "owner", safe)
        database = service.root / "service.sqlite3"
    finally:
        service.close()
    # Emulate the previous schema, retaining all rows and safe checkpoint blobs.
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE jobs DROP COLUMN recovery_ref")
        connection.execute("PRAGMA user_version=2")
    reopened = RecipeService(tmp_path)
    try:
        assert reopened.status(task_id)["checkpoint_ref"] == ref
        assert reopened.status(task_id)["recovery_ref"] is None
        assert reopened.checkpoint(ws) == safe
    finally:
        reopened.close()


def test_large_checkpoint_final_cas_and_interrupted_no_autoreplay(tmp_path):
    service = RecipeService(tmp_path)
    try:
        ws = service.create_workspace("source")["workspace_id"]
        receipt = service.input_completed(
            ws, revision="v1", manifest_digest=stage(service, ws), request_id="input"
        )
        task_id = receipt["init_task_id"]
        with service._assignment(ws):
            service.claim(task_id, "owner")
            checkpoint = {"opaque": "x" * (9 * 1024 * 1024)}
            service.save_checkpoint(task_id, "owner", checkpoint)
            with pytest.raises(ServiceError, match="size limit"):
                service.save_checkpoint(task_id, "owner", {"opaque": "x" * (16 * 1024 * 1024)})
            service.workspace(ws).workspace.joinpath("AGENTS.md").write_text(
                "# Guidelines\n\nSynthetic initialized source.\n"
            )
            service.finish(task_id, "owner", "succeeded", result={}, checkpoint=checkpoint)
        assert service.workspace_info(ws)["session_revision"] == 2
        build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
        with service._assignment(ws):
            service.claim(build["id"], "new-owner")
            saved = {"opaque": "safe-build-progress"}
            service.save_checkpoint(build["id"], "new-owner", saved)
            service.save_checkpoint(
                build["id"], "new-owner", {"opaque": "unsafe-tool"}, recovery=True
            )
        assert service.recover(build["id"])["status"] == "interrupted"
        assert service.pending() == []
        assert service.checkpoint(ws) == saved
    finally:
        service.close()


def test_start_repair_pins_new_input_and_accepted_base_without_turn_limit(tmp_path):
    service = RecipeService(tmp_path)
    try:
        ws = service.create_workspace("source")["workspace_id"]
        initial = service.input_completed(
            ws, revision="v1", manifest_digest=stage(service, ws), request_id="input-1"
        )
        finish_init(service, initial["init_task_id"])
        build = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
        base = finish_build(service, build["id"])
        service.review_revision(
            ws,
            revision_id=service.status(build["id"])["revision_id"],
            decision="accept",
            feedback={"passed": True},
            request_id="accept",
        )
        fresh = service.input_completed(
            ws,
            revision="v2",
            manifest_digest=stage(service, ws, extra={"new_notice": True}),
            request_id="input-2",
        )
        args = {
            "input_revision": "v2",
            "base_recipe_digest": base,
            "feedback": {"origin": "incremental_parse_failure"},
            "request_id": "repair-1",
        }
        repair = service.start_repair(ws, **args)
        assert repair["kind"] == "repair"
        assert repair["input_revision_id"] == fresh["input_revision_id"]
        assert repair["input_revision_id"] != build["input_revision_id"]
        assert repair["base_recipe_digest"] == base
        assert service.start_repair(ws, **args)["id"] == repair["id"]
        with pytest.raises(ServiceError, match="REQUEST_CONFLICT"):
            service.start_repair(ws, **{**args, "base_recipe_digest": "f" * 64})
        with pytest.raises(ServiceError, match="STALE_RECIPE_FEEDBACK"):
            service.start_repair(
                ws, **{**args, "request_id": "stale", "base_recipe_digest": "f" * 64}
            )
        with pytest.raises(ServiceBusy):
            service.start_repair(ws, **{**args, "request_id": "other"})
        assert service.workspace_info(ws)["accepted_recipe_digest"] == base
        profile = service.workspace_info(ws)["agent_profile"]
        assert profile["auto_compaction"] and profile["model"] == "gpt-6.1-sol"
        assert not any("turn" in key or "token" in key for key in profile)
    finally:
        service.close()
