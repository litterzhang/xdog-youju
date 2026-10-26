"""Audit history and native-author plumbing; synthetic data, no provider or database."""

import asyncio
import copy
import fcntl
import json
import os
from unittest.mock import AsyncMock

import pytest
import xdog.ai as ai
from test_author_workspace import prepare
from test_onboarding import SyntheticAuthorStreamFactory
from test_xdog_recipes import NOW, FakeSandbox, bundle, terminating_author_stream
from xdog.agent.skills import load_packaged_skill
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream

from youju.onboarding.author_workspace import MAX_RECORD, AuthorWorkspace, AuthorWorkspaceError
from youju.onboarding.recipes import (
    RecipeAuthor,
    RecipeError,
    RecipeRunner,
    candidate_digest,
    template_candidate,
)
from youju.onboarding.xdog import XdogModels


def test_audits_append_separately_and_survive_prepare(tmp_path):
    workspace = AuthorWorkspace(tmp_path / "source")
    prepare(workspace)
    assert json.loads((workspace.history / "status.json").read_bytes())["latest_audit"] is None
    workspace.record_attempt({"status": "rejected", "errors": [{"code": "SYNTHETIC"}]})
    before = workspace.record_run({"status": "failed"})
    feedback = {"status": "completed", "audit_id": "synthetic-1", "metadata": {"version": "v1"}}
    first = workspace.record_audit(feedback)
    original = {path: path.read_bytes() for path in workspace.history.glob("*/*.json")}
    feedback["metadata"]["version"] = "v2"
    reopened = AuthorWorkspace(workspace.root)
    prepare(reopened)
    latest = reopened.record_audit(feedback)
    assert first["latest_audit"] == {
        "sequence": 1,
        "path": "/history/audits/1.json",
        "status": "completed",
    }
    assert latest["latest_audit"] == {
        "sequence": 2,
        "path": "/history/audits/2.json",
        "status": "completed",
    }
    assert json.loads((workspace.history / "audits/2.json").read_bytes()) == feedback
    for field in ("latest_validation", "latest_run", "errors"):
        assert latest[field] == first[field] == before[field]
    for path, body in original.items():
        assert path.read_bytes() == body
    reopened.record_attempt({"status": "validated", "errors": []})
    assert reopened.record_run({"status": "success"})["latest_audit"] == latest["latest_audit"]


@pytest.mark.parametrize("hazard", ["audit_symlink", "status_symlink", "hardlink", "fifo", "lock"])
def test_audit_history_reuses_safe_file_checks(tmp_path, hazard):
    workspace = AuthorWorkspace(tmp_path / "source")
    prepare(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_text("{}")
    status = workspace.history / "status.json"
    if hazard == "audit_symlink":
        (workspace.history / "audits").symlink_to(outside)
    elif hazard != "lock":
        status.unlink()
        if hazard == "status_symlink":
            status.symlink_to(sentinel)
        elif hazard == "hardlink":
            os.link(sentinel, status)
        else:
            os.mkfifo(status)
    with (workspace.root / ".host.lock").open("rb") as lock:
        if hazard == "lock":
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AuthorWorkspaceError):
            workspace.record_audit({"status": "completed"})
    assert sentinel.read_text() == "{}"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.json"]
    if hazard != "audit_symlink":
        assert not list((workspace.history / "audits").glob("*.json"))


@pytest.mark.parametrize("feedback", [None, {}, {"status": "completed", "metadata": {"id": "v1"}}])
def test_native_author_receives_cloned_feedback_saved_audit_and_system_skill(
    tmp_path, monkeypatch, feedback
):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    expected = copy.deepcopy(feedback)
    factory = SyntheticAuthorStreamFactory(lambda *_: template_candidate())
    workspace = AuthorWorkspace(tmp_path / "source")

    def stream_factory(payload, work):
        assert payload["repair_feedback"] == expected
        status = json.loads((work.history / "status.json").read_bytes())
        if feedback is not None:
            feedback.setdefault("metadata", {})["changed_by_caller"] = True
            assert payload["repair_feedback"] == expected
            assert status["latest_audit"]["path"] == "/history/audits/1.json"
            assert json.loads((work.history / "audits/1.json").read_bytes()) == expected
        else:
            assert status["latest_audit"] is None
            assert not (work.history / "audits").exists()
        return factory(payload, work)

    models = XdogModels(stub=factory.non_author_stub, author_stream_factory=stream_factory)
    assert models.config.max_requests == 30
    assert not hasattr(models.config, "author_max_turns")
    runner = RecipeRunner(FakeSandbox(), models)
    result = asyncio.run(
        RecipeAuthor(models, runner, workspace=workspace).author(
            [bundle()], now_iso=NOW, repair_feedback=feedback
        )
    )
    assert result["attempts"][0]["status"] == "validated"
    payload = json.loads((workspace.input / "payload.json").read_bytes())
    assert payload["repair_feedback"] == expected
    system = ai.system_prompt_text(factory.calls[0]["context"].system_prompt)
    assert system.count(load_packaged_skill("flow").content) == 1
    assert "<official_xdog_flow_skill>" not in system
    for instruction in (
        "/history/status.json",
        "latest_audit",
        "repair_feedback",
        "saved source",
        "Modify only the canonical /work files",
        "host/classifier",
        "blocker",
        "never invent source evidence",
        "accepted unchanged recipe",
        "full source audit",
    ):
        assert instruction in system


def test_prose_cannot_repair_previously_accepted_unchanged_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    workspace = AuthorWorkspace(tmp_path / "source")
    candidate = template_candidate()
    factory = SyntheticAuthorStreamFactory(lambda *_: candidate)
    models = XdogModels(author_stream_factory=factory)
    author = RecipeAuthor(models, RecipeRunner(FakeSandbox(), models), workspace=workspace)
    asyncio.run(author.author([bundle()], now_iso=NOW))
    replies = [
        AssistantMessage(content=(TextContent(text="The unchanged recipe is repaired."),)),
        AssistantMessage(stop_reason="error", error_message="synthetic-provider-termination"),
    ]
    models.author_stream_factory = lambda *_: lambda *_: EventStream.empty(replies.pop(0))
    with pytest.raises(RecipeError) as error:
        asyncio.run(
            author.author(
                [bundle()],
                now_iso=NOW,
                repair_feedback={"previous_recipe_digest": candidate_digest(candidate)},
            )
        )
    assert error.value.code == "MODEL_PROVIDER_ERROR" and error.value.attempts == []
    assert models.calls[-1]["usage"]["turns"] == 2
    assert workspace.load_candidate() == candidate
    assert len(list((workspace.history / "attempts").glob("*.json"))) == 1


@pytest.mark.parametrize(
    ("feedback", "exception", "message"),
    [
        ({"notes": "x" * MAX_RECORD}, AuthorWorkspaceError, "author_file_size_limit"),
        ({"score": float("nan")}, RecipeError, "finite JSON"),
        ([], RecipeError, "Repair feedback must be an object"),
    ],
)
def test_invalid_or_oversized_feedback_fails_before_agent(tmp_path, feedback, exception, message):
    workspace = AuthorWorkspace(tmp_path / "source")
    workspace.record_audit({"status": "previous"})
    original = {path: path.read_bytes() for path in workspace.history.rglob("*.json")}
    models = XdogModels(author_stream_factory=lambda *_: pytest.fail("unexpected agent run"))
    with pytest.raises(exception, match=message):
        asyncio.run(
            RecipeAuthor(models, RecipeRunner(FakeSandbox(), models), workspace=workspace).author(
                [bundle()], now_iso=NOW, repair_feedback=feedback
            )
        )
    assert models.calls == []
    assert {path: path.read_bytes() for path in workspace.history.rglob("*.json")} == original


@pytest.mark.parametrize("eventually_change", [False, True])
def test_unchanged_repair_stays_in_local_history_before_validation_or_callback(
    tmp_path, monkeypatch, eventually_change
):
    monkeypatch.setattr(ai, "provider", lambda *_: pytest.fail("unexpected provider"))
    candidate = template_candidate()
    changed = copy.deepcopy(candidate)
    changed["parser_py"] = changed["parser_py"].replace(
        "return extract_position_draft(bundle, config)",
        'return extract_position_draft(bundle, {**config, "field_map": {"岗位": "title"}})',
    )
    planned = [candidate] * 3 + ([changed] if eventually_change else [])
    factory = SyntheticAuthorStreamFactory(lambda *_: planned.pop(0))
    models = XdogModels(
        author_stream_factory=factory
        if eventually_change
        else terminating_author_stream(factory, len(planned))
    )
    workspace = AuthorWorkspace(tmp_path / "source")
    sandbox, callbacks = FakeSandbox(), []
    author = RecipeAuthor(models, RecipeRunner(sandbox, models), workspace=workspace)
    validate = AsyncMock(wraps=author.validate_candidate)
    monkeypatch.setattr(author, "validate_candidate", validate)

    async def on_attempt(attempt):
        callbacks.append(attempt)

    run = author.author(
        [bundle()],
        now_iso=NOW,
        on_attempt=on_attempt,
        repair_feedback={
            "status": "audit_feedback",
            "previous_recipe_digest": candidate_digest(candidate),
        },
    )
    if eventually_change:
        result = asyncio.run(run)
        assert result["candidate"] == changed
        assert result["attempts"] == callbacks and len(callbacks) == 1
        assert sandbox.calls and callbacks[0]["status"] == "validated"
    else:
        with pytest.raises(RecipeError) as error:
            asyncio.run(run)
        assert error.value.code == "MODEL_PROVIDER_ERROR" and error.value.attempts == []
        assert sandbox.calls == callbacks == []
    assert len(factory.calls) == 3 + eventually_change
    # Both paths use four actual dispatches: one terminates the rejected run.
    assert models.calls[0]["usage"]["turns"] == 4
    for number in range(1, 4):
        attempt = json.loads((workspace.history / f"attempts/{number}.json").read_bytes())
        assert attempt["status"] == "rejected" and "candidate" not in attempt
        assert attempt["errors"][0]["code"] == "REPAIR_UNCHANGED"
        assert "source-file repairs" in attempt["errors"][0]["message"]
    assert validate.await_count == len(callbacks) == eventually_change
