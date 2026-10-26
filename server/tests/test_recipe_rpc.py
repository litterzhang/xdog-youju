"""Offline RPC boundary tests; the service double never executes an Agent."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from youju import create_app
from youju.recipe_service import rpc
from youju.recipe_service.service import ServiceBusy, ServiceError

TOKEN = "synthetic-source-server-token"
OTHER_TOKEN = "synthetic-other-server-token"
HEADERS = {"X-Recipe-Caller": "source-server", "Authorization": f"Bearer {TOKEN}"}
OTHER_HEADERS = {"X-Recipe-Caller": "other-server", "Authorization": f"Bearer {OTHER_TOKEN}"}
DIGEST = "d" * 64
JOB = "a" * 32


def _config(tmp_path):
    return {
        "TESTING": True,
        "RECIPE_RPC_ENABLED": True,
        "RECIPE_SERVICE_STORAGE": str(tmp_path / "storage"),
        "RECIPE_RPC_CALLERS": {
            "source-server": {
                "token": TOKEN,
                "source_ids": ["source", "other", "legacy"],
                "agent_profile": {"system_prompt_version": "profile-v1", "skills": ["xdog-flow"]},
                "callback_target": "source-events",
            },
            "other-server": {
                "token": OTHER_TOKEN,
                "source_ids": ["source", "other", "legacy"],
                "agent_profile": {"system_prompt_version": "other-profile"},
                "callback_target": "other-events",
            },
        },
        "RECIPE_CALLBACK_TARGETS": {
            "source-events": {"url": "http://127.0.0.1:9010/events", "token": "synthetic-callback"},
            "other-events": {"url": "https://receiver.invalid/events", "token": "synthetic-other"},
        },
    }


class StubService:
    """Only implements the documented control-plane signatures, not their business logic."""

    def __init__(self):
        self.workspaces = {}
        self.jobs = {}
        self.calls = []
        self.closed = 0

    def close(self):
        self.closed += 1

    def create_workspace(self, source_id, *, owner_id, agent_profile, lifecycle):
        self.calls.append(("create", source_id, owner_id, agent_profile, lifecycle))
        key = hashlib.sha256(source_id.encode()).hexdigest()
        self.workspaces.setdefault(
            key,
            {
                "workspace_id": key,
                "source_id": source_id,
                "owner_id": owner_id,
                "lifecycle": lifecycle,
                "agent_profile": deepcopy(agent_profile),
                "directory": f"/assigned/{key}",
                "input_dir": f"/assigned/{key}/input",
            },
        )
        return self.workspaces[key]

    def workspace_info(self, ws):
        if ws not in self.workspaces:
            raise ServiceError("Unknown workspace: synthetic-private-path")
        return self.workspaces[ws]

    def input_completed(self, ws, *, revision, manifest_digest, request_id, callback_target=None):
        self.calls.append(("input", ws, revision, manifest_digest, request_id, callback_target))
        return {"init_task_id": JOB, "status": "queued"}

    def start_build(self, ws, *, input_revision, prompt, request_id, callback_target=None):
        self.calls.append(("build", ws, input_revision, prompt, request_id, callback_target))
        self.jobs[JOB] = {"id": JOB, "workspace_id": ws, "status": "queued"}
        return self.jobs[JOB]

    def get_recipe_revision(self, revision_id):
        if revision_id != JOB or JOB not in self.jobs:
            raise ServiceError("Unknown revision")
        return {"revision_id": JOB, "workspace_id": self.jobs[JOB]["workspace_id"]}

    def review_revision(self, workspace_id, *, revision_id, decision, feedback, request_id):
        self.calls.append(("review", workspace_id, revision_id, decision, feedback, request_id))
        return {"decision": decision}

    def status(self, job_id, *, after=0, limit=100):
        if job_id not in self.jobs:
            raise ServiceError("Unknown job: synthetic-private-detail")
        return {**self.jobs[job_id], "events": [], "next_cursor": after, "limit": limit}

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))
        self.jobs[job_id]["status"] = "cancelled"
        return self.jobs[job_id]


@pytest.fixture
def api(tmp_path, monkeypatch):
    service = StubService()
    locations = []

    def factory(storage):
        locations.append(storage)
        return service

    monkeypatch.setattr(rpc, "RecipeService", factory)
    config = _config(tmp_path)
    app = create_app(config)
    assert locations == []  # Even enabled app startup neither opens DB nor runs work.
    return app.test_client(), service, config, locations


def _post(client, method, args, headers=HEADERS):
    return client.post(f"/internal/recipe/{method}", json=args, headers=headers)


def _workspace(client):
    response = _post(client, "CreateWorkspace", {"source_id": "source"})
    assert response.status_code == 200
    return response.json["result"]["workspace_id"]


def test_disabled_by_default_and_no_storage_or_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "RecipeService", lambda *_: pytest.fail("Opened disabled service"))
    app = create_app({"TESTING": True, "RECIPE_SERVICE_STORAGE": str(tmp_path / "unused")})
    assert app.config["RECIPE_RPC_ENABLED"] is False
    assert _post(app.test_client(), "CreateWorkspace", {"source_id": "source"}).status_code == 404
    assert not (tmp_path / "unused").exists()


def test_configuration_is_operator_owned_and_fail_closed(tmp_path):
    changes = [
        {"RECIPE_SERVICE_STORAGE": "relative/path"},
        {"RECIPE_RPC_ENABLED": "true"},
        {"RECIPE_RPC_CALLERS": {}},
        {"RECIPE_CALLBACK_TARGETS": {}},
        {"RECIPE_RPC_MAX_BODY_BYTES": True},
        {"RECIPE_RPC_MAX_BODY_BYTES": 1024 * 1024 + 1},
    ]
    for change in changes:
        with pytest.raises(ValueError):
            create_app({**_config(tmp_path), **change})
    assert not (tmp_path / "storage").exists()


def test_authentication_constant_time_checks_every_configured_caller(api, monkeypatch):
    client, service, _, locations = api
    comparisons = []
    compare = rpc.hmac.compare_digest

    def tracked(left, right):
        comparisons.append(1)
        return compare(left, right)

    monkeypatch.setattr(rpc.hmac, "compare_digest", tracked)
    for headers in (
        {},
        {**HEADERS, "Authorization": "Bearer wrong"},
        {**HEADERS, "X-Recipe-Caller": "unknown"},
        {**HEADERS, "Authorization": f"Bearer {OTHER_TOKEN}"},
        {**HEADERS, "Authorization": "Bearer " + "x" * 513},
        {**HEADERS, "Authorization": "Basic synthetic"},
    ):
        comparisons.clear()
        response = _post(client, "CreateWorkspace", {"source_id": "source"}, headers)
        assert response.status_code == 401
        assert response.json["error"]["code"] == "UNAUTHORIZED"
        assert len(comparisons) == 4
        assert TOKEN.encode() not in response.data
    assert not locations and not service.calls


def test_methods_forward_only_configured_identity_profile_and_callback(api):
    client, service, config, locations = api
    ws = _workspace(client)
    assert service.calls[-1] == (
        "create",
        "source",
        "source-server",
        {"system_prompt_version": "profile-v1", "skills": ["xdog-flow"]},
        "managed",
    )
    # The app takes a snapshot; mutating the supplied dict does not change grants.
    config["RECIPE_RPC_CALLERS"]["source-server"]["agent_profile"]["system_prompt_version"] = (
        "changed"
    )
    assert (
        _post(client, "GetWorkspace", {"workspace_id": ws}).json["result"]["owner_id"]
        == "source-server"
    )
    response = _post(
        client,
        "InputCompleted",
        {
            "workspace_id": ws,
            "revision": "input-v1",
            "manifest_digest": DIGEST,
            "request_id": "input-1",
        },
    )
    assert response.status_code == 200 and response.json["result"]["status"] == "queued"
    assert service.calls[-1] == ("input", ws, "input-v1", DIGEST, "input-1", "source-events")
    response = _post(
        client,
        "StartBuild",
        {
            "workspace_id": ws,
            "input_revision": "input-v1",
            "prompt": "Build the recipe.",
            "request_id": "build-1",
        },
    )
    assert response.json["result"]["id"] == JOB
    assert service.calls[-1][-1] == "source-events"
    response = _post(client, "GetTask", {"task_id": JOB, "after": 2, "limit": 3})
    assert response.json["result"]["next_cursor"] == 2
    assert response.json["result"]["limit"] == 3
    response = _post(
        client,
        "ReviewRevision",
        {
            "workspace_id": ws,
            "revision_id": JOB,
            "decision": "reject",
            "feedback": {"errors": ["Missing deadline evidence"]},
            "request_id": "feedback-1",
        },
    )
    assert response.status_code == 200
    assert service.calls[-1][-1] == "feedback-1"
    assert _post(client, "CancelTask", {"task_id": JOB}).json["result"]["status"] == "cancelled"
    assert set(locations) == {str(Path(config["RECIPE_SERVICE_STORAGE"]))}
    assert service.closed == len(locations)


def test_owned_workspace_and_job_checks_forbid_foreign_legacy_and_revoked_sources(api):
    client, service, _, _ = api
    ws = _workspace(client)
    service.jobs[JOB] = {"id": JOB, "workspace_id": ws, "status": "queued"}
    calls = list(service.calls)
    methods = {
        "GetWorkspace": {"workspace_id": ws},
        "InputCompleted": {
            "workspace_id": ws,
            "revision": "v1",
            "manifest_digest": DIGEST,
            "request_id": "r1",
        },
        "StartBuild": {
            "workspace_id": ws,
            "input_revision": "v1",
            "prompt": "Build",
            "request_id": "r2",
        },
        "GetTask": {"task_id": JOB},
        "CancelTask": {"task_id": JOB},
        "ReviewRevision": {
            "workspace_id": ws,
            "revision_id": JOB,
            "decision": "accept",
            "feedback": {"passed": True},
            "request_id": "r3",
        },
    }
    for method, body in methods.items():
        assert _post(client, method, body, OTHER_HEADERS).status_code == 404
    assert service.calls == calls
    for field, value in (("lifecycle", "legacy"), ("source_id", "not-allowlisted")):
        previous = service.workspaces[ws][field]
        service.workspaces[ws][field] = value
        for method, body in methods.items():
            assert _post(client, method, body).status_code == 404
        service.workspaces[ws][field] = previous
    assert service.calls == calls
    assert _post(client, "GetTask", {"task_id": "f" * 32}).status_code == 404
    assert _post(client, "GetWorkspace", {"workspace_id": "f" * 64}).status_code == 404


def test_feedback_cannot_bind_task_from_another_owned_workspace(api):
    client, service, _, _ = api
    ws = _workspace(client)
    other = _post(client, "CreateWorkspace", {"source_id": "other"}).json["result"]["workspace_id"]
    service.jobs[JOB] = {"id": JOB, "workspace_id": other, "status": "queued"}
    response = _post(
        client,
        "ReviewRevision",
        {
            "workspace_id": ws,
            "revision_id": JOB,
            "decision": "accept",
            "feedback": {"passed": True},
            "request_id": "accept-1",
        },
    )
    assert response.status_code == 404
    assert not any(call[0] == "review" for call in service.calls)


def test_strict_arguments_body_limits_and_no_client_paths_urls_or_owners(api):
    client, service, _, _ = api
    for key, value in (
        ("owner_id", "other-server"),
        ("directory", "/tmp/arbitrary"),
        ("storage", "/tmp/arbitrary"),
        ("callback_url", "http://169.254.169.254/"),
        ("callback_target", "other-events"),
        ("agent_profile", {"id": "unapproved"}),
        ("lifecycle", "legacy"),
    ):
        assert (
            _post(client, "CreateWorkspace", {"source_id": "source", key: value}).status_code == 400
        )
    assert _post(client, "CreateWorkspace", {"source_id": "not-allowed"}).status_code == 403
    assert _post(client, "CreateWorkspace", {"source_id": "../../etc"}).status_code == 400
    assert not service.calls
    url = "/internal/recipe/CreateWorkspace"
    for body in ("[]", "{", '{"source_id":"source","source_id":"other"}', '{"source_id":NaN}'):
        assert (
            client.post(
                url, data=body, content_type="application/json", headers=HEADERS
            ).status_code
            == 400
        )
    response = client.post(
        url, data="x" * (64 * 1024 + 1), content_type="application/json", headers=HEADERS
    )
    assert response.status_code == 413
    assert client.post(url, data="source", headers=HEADERS).status_code == 415
    assert client.get(url, headers=HEADERS).status_code == 405
    assert client.open(url, method="OPTIONS", headers=HEADERS).status_code == 405
    assert _post(client, "UnknownMethod", {}).status_code == 404
    for body in (
        {"task_id": JOB, "limit": True},
        {"task_id": JOB, "after": -1},
        {"task_id": JOB, "limit": 501},
        {"task_id": JOB, "after": 2**63},
    ):
        assert _post(client, "GetTask", body).status_code == 400


@pytest.mark.parametrize(
    "exception,status", [(ServiceError, 400), (ServiceBusy, 409), (OSError, 500)]
)
def test_service_errors_are_redacted_and_storage_is_closed(api, monkeypatch, exception, status):
    client, service, _, locations = api

    def broken(*args, **kwargs):
        raise exception("synthetic-secret /private/input https://secret.invalid/token")

    monkeypatch.setattr(service, "create_workspace", broken)
    response = _post(client, "CreateWorkspace", {"source_id": "source"})
    assert response.status_code == status
    assert b"synthetic-secret" not in response.data and b"/private/input" not in response.data
    assert service.closed == len(locations)


def test_actual_service_input_build_feedback_and_terminal_outbox(tmp_path):
    """Real control-plane integration; fixture completions substitute for an SDK runtime."""
    import json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from youju.onboarding.recipes import candidate_digest, template_candidate
    from youju.onboarding.store import digest, json_bytes
    from youju.recipe_service import RecipeService
    from youju.recipe_service.models import Outbox

    config = _config(tmp_path)
    client = create_app(config).test_client()
    ws = _workspace(client)
    info = _post(client, "GetWorkspace", {"workspace_id": ws}).json["result"]
    assert info["status"] == "waiting_input"
    assert info["owner_id"] == "source-server" and info["lifecycle"] == "managed"
    assert Path(info["directory"]).is_relative_to(Path(config["RECIPE_SERVICE_STORAGE"]))
    assert _workspace(client) == ws
    build_body = {
        "workspace_id": ws,
        "input_revision": "v1",
        "prompt": "Build using saved samples.",
        "request_id": "build-1",
    }
    assert _post(client, "StartBuild", build_body).status_code == 409
    payload = json_bytes({"source_id": "source", "objective": "Extract evidence-backed positions."})
    manifest = {"payload.json": hashlib.sha256(payload).hexdigest()}
    inputs = Path(info["input_dir"])
    (inputs / "payload.json").write_bytes(payload)
    (inputs / "manifest.json").write_bytes(json_bytes(manifest))
    input_body = {
        "workspace_id": ws,
        "revision": "v1",
        "manifest_digest": digest(manifest),
        "request_id": "input-1",
    }
    receipt = _post(client, "InputCompleted", input_body)
    assert receipt.status_code == 200, receipt.json
    receipt = receipt.json["result"]
    init = _post(client, "GetTask", {"task_id": receipt["init_task_id"]}).json["result"]
    assert init["kind"] == "init" and init["status"] == "queued"
    assert init["callback_target"] == "source-events"
    assert init["input_digest"] == receipt["input_digest"]
    (inputs / "payload.json").write_text("changed after input commit", encoding="utf-8")
    assert _post(client, "InputCompleted", input_body).json["result"] == receipt
    build = _post(client, "StartBuild", build_body).json["result"]
    assert build["status"] == "waiting_init"
    assert build["depends_on"] == init["id"] and build["callback_target"] == "source-events"
    assert _post(client, "StartBuild", build_body).json["result"]["id"] == build["id"]
    assert _post(client, "GetTask", {"task_id": build["id"]}, OTHER_HEADERS).status_code == 404
    assert (
        _post(client, "GetTaskResult", {"task_id": build["id"]}, OTHER_HEADERS).status_code == 404
    )
    unavailable = _post(client, "GetTaskResult", {"task_id": build["id"]})
    assert unavailable.status_code == 409
    assert unavailable.json["error"]["code"] == "RESULT_NOT_READY"
    assert _post(client, "CancelTask", {"task_id": build["id"]}, OTHER_HEADERS).status_code == 404
    with pytest.raises(ValueError):
        # Profiles are checked at configuration time, not injected by a request.
        bad = _config(tmp_path)
        bad["RECIPE_RPC_CALLERS"]["source-server"]["agent_profile"] = {"arbitrary": "invalid"}
        create_app(bad)
    service = RecipeService(config["RECIPE_SERVICE_STORAGE"])
    try:
        snapshot = service.input_snapshot(receipt["input_revision_id"])
        assert snapshot["files"]["payload.json"] == payload.decode()
        workspace = service.workspace(ws)
        with Session(service.engine) as session:
            assert session.scalar(select(Outbox)) is None
        # These are explicitly synthetic host completions, not generated Agent files.
        workspace.workspace.joinpath("AGENTS.md").write_text(
            "# Fixture instructions\nExtract only evidence-backed positions.\n", encoding="utf-8"
        )
        with service._assignment(ws):
            service.claim(receipt["init_task_id"], "fixture-init")
            service.finish(
                receipt["init_task_id"],
                "fixture-init",
                "succeeded",
                result={"initialized": True},
                checkpoint={"fixture": "init"},
            )
        assert (
            _post(client, "GetWorkspace", {"workspace_id": ws}).json["result"]["status"]
            == "prepared"
        )
        assert (
            _post(client, "GetTask", {"task_id": build["id"]}).json["result"]["status"] == "queued"
        )
        candidate = template_candidate()
        recipe_digest = candidate_digest(candidate)
        workspace.write_candidate(candidate)
        with service._assignment(ws):
            service.claim(build["id"], "fixture-build")
            service.finish(
                build["id"],
                "fixture-build",
                "candidate_ready",
                result={"candidate": candidate, "digest": recipe_digest},
                checkpoint={"fixture": "build"},
            )
        saved_result = _post(client, "GetTaskResult", {"task_id": build["id"]})
        assert saved_result.status_code == 200
        assert saved_result.json["result"] == {"candidate": candidate, "digest": recipe_digest}
        # Mutable files are not the callback consumer's published result source.
        changed_candidate = template_candidate()
        changed_candidate["config"] = {"revision": "not-published"}
        workspace.write_candidate(changed_candidate)
        assert _post(client, "GetTaskResult", {"task_id": build["id"]}).json == saved_result.json
        assert "checkpoint" not in saved_result.json["result"]
        assert (
            _post(client, "GetTaskResult", {"task_id": build["id"]}, OTHER_HEADERS).status_code
            == 404
        )
        assert _post(client, "GetTaskResult", {"task_id": "0" * 32}).status_code == 404
        assert (
            _post(
                client, "GetTaskResult", {"task_id": build["id"], "path": "/tmp/private"}
            ).status_code
            == 400
        )
        feedback = {
            "workspace_id": ws,
            "revision_id": service.status(build["id"])["revision_id"],
            "decision": "accept",
            "feedback": {"summary": "Synthetic fixture review only"},
            "request_id": "accept-1",
        }
        stale = _post(client, "ReviewRevision", {**feedback, "revision_id": "0" * 32})
        assert stale.status_code == 404
        accepted = _post(client, "ReviewRevision", feedback)
        assert accepted.status_code == 200, accepted.json
        assert _post(client, "ReviewRevision", feedback).json == accepted.json
        assert (
            _post(client, "GetWorkspace", {"workspace_id": ws}).json["result"][
                "accepted_recipe_digest"
            ]
            == recipe_digest
        )
        # Review never starts a repair. Only StartRepair with a fresh input can.
        old_endpoint = _post(client, "SubmitFeedback", feedback)
        assert old_endpoint.status_code == 404
        payload2 = json_bytes(
            {
                "source_id": "source",
                "objective": "Extract evidence-backed positions.",
                "new_notice": True,
            }
        )
        manifest2 = {"payload.json": hashlib.sha256(payload2).hexdigest()}
        (inputs / "payload.json").write_bytes(payload2)
        (inputs / "manifest.json").write_bytes(json_bytes(manifest2))
        assert (
            _post(
                client,
                "InputCompleted",
                {
                    **input_body,
                    "revision": "v2",
                    "request_id": "input-2",
                    "manifest_digest": digest(manifest2),
                },
            ).status_code
            == 200
        )
        repair = _post(
            client,
            "StartRepair",
            {
                "workspace_id": ws,
                "input_revision": "v2",
                "base_recipe_digest": recipe_digest,
                "base_revision_id": feedback["revision_id"],
                "feedback": {"errors": ["fixture"]},
                "request_id": "repair-1",
            },
        )
        assert repair.status_code == 200, repair.json
        repair_id = repair.json["result"]["id"]
        cancelled = _post(client, "CancelTask", {"task_id": repair_id})
        assert cancelled.json["result"]["status"] == "cancelled"
        assert _post(client, "CancelTask", {"task_id": repair_id}).status_code == 200
        with Session(service.engine) as session:
            events = session.scalars(select(Outbox)).all()
            assert len(events) == 3  # Init, candidate, cancel; idempotent replay adds none.
            assert {event.job_id for event in events} == {
                receipt["init_task_id"],
                build["id"],
                repair_id,
            }
            assert all(
                event.target_id == "source-events" and event.status == "pending" for event in events
            )
            assert "synthetic-private" not in json.dumps([event.payload for event in events])
        with pytest.raises(ServiceError, match="lifecycle"):
            service.create_workspace("legacy", lifecycle="legacy")
    finally:
        service.close()


def test_only_exact_known_service_error_codes_cross_rpc_boundary(api, monkeypatch):
    client, service, _, _ = api
    expected = {
        "WORKSPACE_PROFILE_MIGRATION_REQUIRED": 409,
        "RESULT_NOT_READY": 409,
        "INPUT_NOT_READY": 409,
        "INIT_FAILED": 409,
        "INIT_NOT_READY": 409,
        "REQUEST_CONFLICT": 409,
        "INPUT_REVISION_EXISTS": 409,
        "REINITIALIZATION_REQUIRED": 409,
        "STALE_RECIPE_FEEDBACK": 409,
        "MANIFEST_CHANGED": 409,
        "INPUT_CHANGED": 409,
        "INPUT_FILES_INVALID": 400,
    }
    message = ""

    def broken(*args, **kwargs):
        raise ServiceError(message)

    monkeypatch.setattr(service, "create_workspace", broken)
    for message, status in expected.items():
        response = _post(client, "CreateWorkspace", {"source_id": "source"})
        assert response.status_code == status
        assert response.json["error"]["code"] == message
    for message in ("INPUT_CHANGED /private/input", "prefix REQUEST_CONFLICT", "UNKNOWN_CODE"):
        response = _post(client, "CreateWorkspace", {"source_id": "source"})
        assert response.status_code == 400
        assert response.json["error"]["code"] == "REQUEST_REJECTED"
        assert message.encode() not in response.data


def test_start_repair_uses_authenticated_workspace_new_input_and_base(api, monkeypatch):
    client, service, _, _ = api
    ws = _workspace(client)
    calls = []

    def start_repair(workspace_id, **kwargs):
        calls.append((workspace_id, kwargs))
        return {"id": JOB, "kind": "repair", "status": "queued"}

    monkeypatch.setattr(service, "start_repair", start_repair, raising=False)
    args = {
        "workspace_id": ws,
        "input_revision": "incremental-2",
        "base_recipe_digest": DIGEST,
        "feedback": {"origin": "incremental_parse_failure"},
        "request_id": "repair-2",
    }
    result = _post(client, "StartRepair", args)
    assert result.status_code == 200 and result.json["result"]["kind"] == "repair"
    assert calls == [
        (
            ws,
            {key: args[key] for key in args if key != "workspace_id"}
            | {"callback_target": "source-events"},
        )
    ]
    assert _post(client, "StartRepair", args, OTHER_HEADERS).status_code == 404
    assert _post(client, "StartRepair", {**args, "base_recipe_digest": "bad"}).status_code == 400
    assert _post(client, "StartRepair", {**args, "feedback": "instructions"}).status_code == 400
    assert _post(client, "StartRepair", {**args, "physical_path": "/tmp"}).status_code == 400
    assert len(calls) == 1
