"""Immutable revisions and owner reviews, using only offline synthetic runtime data."""

import asyncio
import sqlite3

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_recipe_lifecycle import finish_build, finish_init, stage
from test_recipe_rpc import HEADERS, OTHER_HEADERS, _config, _post
from test_recipe_runtime import FakeRuntime, prepare

from youju import create_app
from youju.onboarding.recipes import candidate_digest, template_candidate
from youju.recipe_service import RecipeService, ServiceError
from youju.recipe_service.models import Job, Outbox, RecipeRevision, RevisionReview
from youju.recipe_service.task_worker import TaskWorker


@pytest.fixture
def service(tmp_path):
    value = RecipeService(tmp_path)
    try:
        yield value
    finally:
        value.close()


def initial_candidate(service, *, callback_target=None, owner_id="local"):
    ws = service.create_workspace("source", owner_id=owner_id)["workspace_id"]
    inputs = service.input_completed(
        ws, revision="v1", manifest_digest=stage(service, ws), request_id="input-1"
    )
    finish_init(service, inputs["init_task_id"])
    task = service.start_build(
        ws,
        input_revision="v1",
        prompt="Build",
        request_id="build-1",
        callback_target=callback_target,
    )
    finish_build(service, task["id"])
    return ws, service.status(task["id"])


def review(service, ws, task, decision, request_id):
    return service.review_revision(
        ws,
        revision_id=task["revision_id"],
        decision=decision,
        feedback={"audit_ref": "synthetic-full-source-audit", "passed": decision == "accept"},
        request_id=request_id,
    )


def fresh_repair(service, ws, target, sequence, *, explicit=True):
    revision = f"v{sequence}"
    service.input_completed(
        ws,
        revision=revision,
        manifest_digest=stage(service, ws, extra={"new_notice": sequence}),
        request_id=f"input-{sequence}",
    )
    return service.start_repair(
        ws,
        input_revision=revision,
        base_recipe_digest=target["artifact_digest"],
        **({"base_revision_id": target["revision_id"]} if explicit else {}),
        feedback={"errors": ["synthetic parse failure"]},
        request_id=f"repair-{sequence}",
    )


def changed_candidate(sequence):
    candidate = template_candidate()
    candidate["config"]["revision"] = sequence
    return candidate


def test_revision_pins_saved_files_input_profile_and_callback(service):
    ws, task = initial_candidate(service, callback_target="owner")
    saved = service.get_recipe_revision(task["revision_id"])
    assert saved["id"] == task["revision_id"]
    assert saved["workspace_id"] == ws and saved["task_id"] == task["id"]
    assert saved["input_revision_id"] == task["input_revision_id"]
    assert saved["input_digest"] == task["input_digest"]
    assert saved["profile_revision"] == service.workspace_info(ws)["profile_revision"]
    assert saved["agent_profile"] == service.workspace_info(ws)["agent_profile"]
    assert saved["base_revision_id"] is None
    assert saved["artifact_digest"] == candidate_digest(saved["candidate"])
    assert saved["sample_validation"] == []  # This host-only fixture did not run samples.
    assert service.workspace_info(ws)["accepted_revision_id"] is None
    with Session(service.engine) as session:
        callback = session.scalar(select(Outbox).where(Outbox.job_id == task["id"]))
        assert callback.payload["revision_id"] == saved["id"]
        assert callback.payload["recipe_digest"] == saved["artifact_digest"]
        assert session.scalar(select(func.count()).select_from(RecipeRevision)) == 1
    # Neither mutable files nor a caller-mutated returned object changes publication.
    service.workspace(ws).write_candidate(changed_candidate(99))
    saved["candidate"]["config"] = {"caller_mutated": True}
    persisted = service.get_recipe_revision(task["revision_id"])
    assert persisted["candidate"] == template_candidate()
    review(service, ws, task, "accept", "accept-1")
    assert service.get_recipe_revision(task["revision_id"]) == persisted
    reopened = RecipeService(service.storage)
    try:
        assert reopened.get_recipe_revision(task["revision_id"]) == persisted
        assert reopened.workspace_info(ws)["accepted_revision_id"] == task["revision_id"]
    finally:
        reopened.close()


@pytest.mark.parametrize("table", ["recipe_revisions", "revision_reviews"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_revisions_and_reviews_reject_direct_sql_mutation(service, table, operation):
    ws, task = initial_candidate(service)
    review(service, ws, task, "accept", "accept")
    statement = (
        f"UPDATE {table} SET created_at='changed'"
        if operation == "UPDATE"
        else (f"DELETE FROM {table}")
    )
    with pytest.raises(IntegrityError, match="immutable"):
        with service.engine.begin() as connection:
            connection.exec_driver_sql(statement)
    with Session(service.engine) as session:
        assert session.scalar(select(func.count()).select_from(RecipeRevision)) == 1
        assert session.scalar(select(func.count()).select_from(RevisionReview)) == 1


def test_explicit_repair_rejected_first_candidate_without_accepted_version(service):
    ws, target = initial_candidate(service)
    before = service.pending()
    rejected = review(service, ws, target, "reject", "reject-first")
    assert rejected["decision"] == "reject" and service.pending() == before == []
    assert service.workspace_info(ws)["accepted_revision_id"] is None
    assert service.status(target["id"])["status"] == "candidate_ready"
    with pytest.raises(ServiceError, match="FRESH_INPUT_REQUIRED"):
        service.start_repair(
            ws,
            input_revision="v1",
            base_recipe_digest=target["artifact_digest"],
            base_revision_id=target["revision_id"],
            feedback={"failed": True},
            request_id="old",
        )
    repair = fresh_repair(service, ws, target, 2)
    assert repair["base_revision_id"] == target["revision_id"]
    assert repair["base_recipe_digest"] is None  # Session fence is NOT target digest.
    assert repair["accepted_base_revision_id"] is None
    assert (
        service.task_prompt(repair["id"])["feedback"]["recipe_digest"] == target["artifact_digest"]
    )
    finish_build(service, repair["id"], changed_candidate(2))
    repaired = service.status(repair["id"])
    revision = service.get_recipe_revision(repaired["revision_id"])
    assert revision["base_revision_id"] == target["revision_id"]
    assert revision["base_recipe_digest"] == target["artifact_digest"]
    assert revision["accepted_base_revision_id"] is None
    review(service, ws, repaired, "accept", "accept-repair")
    assert service.workspace_info(ws)["accepted_revision_id"] == repaired["revision_id"]


def test_rejected_repair_can_be_targeted_again_without_losing_accepted_base(service):
    ws, accepted = initial_candidate(service)
    review(service, ws, accepted, "accept", "accept-original")
    repair = fresh_repair(service, ws, accepted, 2)
    finish_build(service, repair["id"], changed_candidate(2))
    rejected = service.status(repair["id"])
    review(service, ws, rejected, "reject", "reject-repair")
    assert service.workspace_info(ws)["accepted_revision_id"] == accepted["revision_id"]
    retry = fresh_repair(service, ws, rejected, 3, explicit=False)
    assert retry["base_revision_id"] == rejected["revision_id"]
    assert retry["base_recipe_digest"] == accepted["artifact_digest"]
    assert retry["accepted_base_revision_id"] == accepted["revision_id"]
    with service.assignment(ws):
        service.claim(retry["id"], "retry-owner")
        with pytest.raises(ServiceError, match="REPAIR_UNCHANGED"):
            service.finish(
                retry["id"],
                "retry-owner",
                "candidate_ready",
                result={"candidate": changed_candidate(2), "digest": rejected["artifact_digest"]},
                checkpoint={"fixture": "unchanged"},
            )
        candidate = changed_candidate(3)
        service.workspace(ws).write_candidate(candidate)
        attempts = [{"status": "passed", "digest": candidate_digest(candidate), "errors": []}]
        service.finish(
            retry["id"],
            "retry-owner",
            "candidate_ready",
            result={
                "candidate": candidate,
                "digest": candidate_digest(candidate),
                "attempts": attempts,
                "sample_validation": "passed",
            },
            checkpoint={"fixture": "retry"},
        )
    retried = service.status(retry["id"])
    saved = service.get_recipe_revision(retried["revision_id"])
    assert saved["sample_validation"] == attempts
    assert saved["accepted_base_revision_id"] == accepted["revision_id"]
    assert saved["base_revision_id"] == rejected["revision_id"]
    review(service, ws, retried, "accept", "accept-retry")
    assert service.workspace_info(ws)["accepted_revision_id"] == retried["revision_id"]


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_review_is_independent_of_pending_execution_and_latest_candidate(
    service, running, decision
):
    ws, first = initial_candidate(service)
    second = service.start_build(ws, input_revision="v1", prompt="Build again", request_id="second")
    finish_build(service, second["id"], changed_candidate(2))
    # The first publication is no longer latest. A third task is pending/running.
    pending = service.start_build(ws, input_revision="v1", prompt="Again", request_id="pending")
    with service.assignment(ws):
        if running:
            service.claim(pending["id"], "pending-owner")
        before = service.status(pending["id"])
        receipt = review(service, ws, first, decision, "review")
        assert service.status(pending["id"]) == before
        assert service.status(first["id"])["status"] == "candidate_ready"
        assert review(service, ws, first, decision, "review") == receipt
        with pytest.raises(ServiceError, match="REQUEST_CONFLICT"):
            review(service, ws, first, "reject" if decision == "accept" else "accept", "review")
        with pytest.raises(ServiceError, match="REVISION_ALREADY_REVIEWED"):
            review(service, ws, first, decision, "another-review")
        if running:
            service.finish(pending["id"], "pending-owner", "failed", error_code="SYNTHETIC")
    service.cancel(pending["id"])
    if decision == "accept":
        assert service.workspace_info(ws)["accepted_revision_id"] == first["revision_id"]
        with pytest.raises(ServiceError, match="STALE_RECIPE_BASE"):
            review(service, ws, service.status(second["id"]), "accept", "stale")
    else:
        assert service.workspace_info(ws)["accepted_revision_id"] is None
    with Session(service.engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 4  # init + 3 builds


def test_foreign_or_mismatched_base_and_foreign_review_are_rejected(service):
    ws, target = initial_candidate(service)
    other = service.create_workspace("other")["workspace_id"]
    with pytest.raises(ServiceError, match="Unknown recipe revision"):
        review(service, other, target, "reject", "foreign")
    service.input_completed(
        ws,
        revision="v2",
        manifest_digest=stage(service, ws, extra={"new": True}),
        request_id="input-2",
    )
    for base_id, base_digest in (
        (target["revision_id"], "f" * 64),
        ("f" * 32, target["artifact_digest"]),
    ):
        with pytest.raises(ServiceError, match="STALE_RECIPE_FEEDBACK"):
            service.start_repair(
                ws,
                input_revision="v2",
                base_revision_id=base_id,
                base_recipe_digest=base_digest,
                feedback={"failed": True},
                request_id="bad-base",
            )
    assert not service.pending()


def test_public_worker_boundary_never_reads_private_service_or_blobs(service):
    _, init_id = prepare(service)

    class PublicBoundary:
        def __getattr__(self, key):
            if key.startswith("_") or key == "blobs":
                pytest.fail(f"Worker accessed private service boundary: {key}")
            return getattr(service, key)

    worker = TaskWorker(PublicBoundary(), FakeRuntime)
    assert asyncio.run(worker.run_once(init_id))["status"] == "succeeded"
    ws = service.status(init_id)["workspace_id"]
    task = service.start_build(ws, input_revision="v1", prompt="Build", request_id="build")
    result = asyncio.run(worker.run_once(task["id"]))
    assert result["revision_id"] and result["status"] == "candidate_ready"


def test_revision_rpc_auth_binding_strict_arguments_and_no_feedback_endpoint(tmp_path):
    config = _config(tmp_path)
    service = RecipeService(config["RECIPE_SERVICE_STORAGE"])
    try:
        ws, task = initial_candidate(service, owner_id="source-server")
        client = create_app(config).test_client()
        args = {"revision_id": task["revision_id"]}
        saved = _post(client, "GetRecipeRevision", args)
        assert saved.status_code == 200
        assert saved.json["result"]["id"] == task["revision_id"]
        assert saved.json["result"]["candidate"] == template_candidate()
        assert _post(client, "GetRecipeRevision", args, OTHER_HEADERS).status_code == 404
        assert _post(client, "GetRecipeRevision", {"revision_id": "0" * 32}).status_code == 404
        assert _post(client, "GetRecipeRevision", {**args, "path": "/tmp"}).status_code == 400
        feedback = {
            "workspace_id": ws,
            "revision_id": task["revision_id"],
            "decision": "reject",
            "feedback": {"full_source_audit": "failed"},
            "request_id": "reject",
        }
        assert _post(client, "ReviewRevision", feedback, OTHER_HEADERS).status_code == 404
        assert (
            _post(client, "ReviewRevision", {**feedback, "decision": "repair"}).status_code == 400
        )
        assert (
            _post(client, "ReviewRevision", {**feedback, "recipe_digest": "a" * 64}).status_code
            == 400
        )
        response = _post(client, "ReviewRevision", feedback, HEADERS)
        assert response.status_code == 200, response.json
        assert response.json["result"]["decision"] == "reject"
        assert _post(client, "ReviewRevision", feedback).json == response.json
        assert _post(client, "SubmitFeedback", feedback).status_code == 404
        assert (
            service.pending() == [] and service.workspace_info(ws)["accepted_revision_id"] is None
        )
    finally:
        service.close()


def test_unsupported_published_legacy_database_is_rejected_without_writes(tmp_path):
    root = tmp_path / "authoring"
    root.mkdir()
    database = root / "service.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE workspaces (id TEXT PRIMARY KEY, accepted_recipe_digest TEXT);
            INSERT INTO workspaces VALUES ('sentinel', 'old-accepted-digest');
            PRAGMA user_version=3;
        """)
        before = list(connection.iterdump())
    with pytest.raises(ValueError, match="fresh storage path"):
        RecipeService(tmp_path)
    with sqlite3.connect(database) as connection:
        assert list(connection.iterdump()) == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
