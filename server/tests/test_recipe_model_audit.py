"""Offline official-Agent tests. Only the provider stream is synthetic."""

import asyncio
import copy
import json
import multiprocessing
import shutil
import socket
from dataclasses import replace
from pathlib import Path

import pytest
import xdog.ai as ai
from sqlalchemy import create_engine
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall, Usage
from xdog.ai.utils.event_stream import EventStream

from youju.onboarding.xdog import XdogConfig, XdogModels
from youju.recipe_service.model_audit import (
    AUDIT_MODEL,
    AUDIT_SCHEMA,
    MAX_REQUESTS,
    AuditError,
    FrozenAuditCase,
    ModelAuditService,
    freeze_batch,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline audit test attempted network/provider creation")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(ai, "provider", forbidden)


def case(case_id="case-1", *, text="原文：学历要求博士。", output=None, issues=None):
    return FrozenAuditCase.freeze(
        case_id,
        documents=[{"document_id": "saved-doc-v1", "text": text}],
        current_output=output if output is not None else {"education": "硕士"},
        output_status="rejected",
        issues=issues if issues is not None else [{"code": "REVIEW"}],
    )


def verdict():
    return {
        "verdict": "model_suspected",
        "summary": "输出可能与原文不符，需复核。",
        "diagnostics": [
            {
                "field_path": "/education",
                "finding": "suspected_error",
                "explanation": "原文写明博士。",
                "evidence": [
                    {"document_id": "saved-doc-v1", "start": 3, "end": 9, "quote": "学历要求博士"}
                ],
            }
        ],
    }


class SyntheticStream:
    def __init__(self, result=None, *, marker=None, message=None):
        self.result = verdict() if result is None else result
        self.marker, self.message = marker, message
        self.calls = []
        self.metadata = Model(
            id=AUDIT_MODEL,
            api="synthetic",
            provider="synthetic",
            supports_tool_calls=True,
            context_window=200_000,
            max_tokens=4096,
        )

    def models(self):
        return (self.metadata,)

    def resolve_model(self, name):
        return self.metadata if name == AUDIT_MODEL else None

    def __call__(self, model, context, options):
        assert model == AUDIT_MODEL
        assert not self.calls, "Second provider request is forbidden, even after bad output"
        self.calls.append(context)
        assert [tool.name for tool in context.tools] == ["submit_audit"]
        assert options.web_search is False
        assert options.parallel_tool_calls is False
        assert options.max_tokens == 4096
        assert len(context.messages) == 1  # No author session, other audit or restored history.
        if self.marker:
            with Path(self.marker).open("a") as file:
                file.write("request\n")
        return EventStream.empty(
            self.message
            or AssistantMessage(
                content=(ToolCall(id="audit-submit", name="submit_audit", arguments=self.result),),
                stop_reason="toolUse",
                response_id="synthetic-response",
                usage=Usage(input=123, output=45, cache_read=7, total_tokens=175),
            )
        )


def service(path, stream=None, **kwargs):
    stream = stream or SyntheticStream()
    return ModelAuditService(path, XdogModels(), stream_factory=lambda: stream, **kwargs)


def run(svc, cases=None, **kwargs):
    return asyncio.run(svc.run_batch([case()] if cases is None else cases, **kwargs))


def test_frozen_isolation_schema_usage_and_provenance(tmp_path):
    documents = [{"document_id": "saved-doc-v1", "text": "原文：学历要求博士。"}]
    output, issues = {"education": "硕士"}, [{"code": "REVIEW"}]
    frozen = FrozenAuditCase.freeze(
        "case-1", documents=documents, current_output=output, output_status="review", issues=issues
    )
    documents[0]["text"], output["education"], issues[0]["code"] = "changed", "changed", "changed"
    stream = SyntheticStream()
    svc = service(tmp_path / "audit.sqlite3", stream)
    receipt = run(svc, [frozen])[0]
    assert receipt["status"] == "completed", receipt
    assert receipt["requests"] == MAX_REQUESTS == 1
    assert receipt["result"]["provenance"] == ["ai_audited", "model_suspected"]
    assert receipt["result"]["basis"] == "ai_opinion_not_gold"
    assert receipt["result"]["human_confirmed"] is False
    assert receipt["result"]["accuracy_verified"] is False
    assert receipt["sdk_usage"][0]["input"] == 123
    assert receipt["sdk_usage"][0]["cache_read"] == 7
    assert receipt["sdk_usage"][0]["total_tokens"] == 175
    assert receipt["usage_status"] == "sdk_reported"
    assert receipt["cost_usd"] is None and receipt["cost_status"] == "unknown"
    assert receipt["response_ids"] == ["synthetic-response"]
    prompt = "".join(part.text for part in stream.calls[0].messages[0].content)
    assert "博士" in prompt and "硕士" in prompt and "changed" not in prompt
    assert "UNTRUSTED" in str(stream.calls[0].system_prompt)
    assert AUDIT_SCHEMA["additionalProperties"] is False
    assert "$ref" not in json.dumps(AUDIT_SCHEMA)
    svc.close()


@pytest.mark.parametrize("limit", [0, 4, -1, True, 1.5, "3", None])
def test_invalid_max_batch_has_no_reservation(tmp_path, limit):
    stream = SyntheticStream()
    svc = service(tmp_path / "audit.db", stream)
    with pytest.raises(ValueError):
        run(svc, max_cases=limit)
    assert not stream.calls
    with svc.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM model_audits_v1").scalar() == 0
    svc.close()


@pytest.mark.parametrize("cases,limit", [([], 3), ([case()] * 4, 3), ([case()] * 2, 1)])
def test_invalid_batch_size(tmp_path, cases, limit):
    svc = service(tmp_path / "audit.db")
    with pytest.raises(ValueError):
        run(svc, cases, max_cases=limit)
    svc.close()


def test_cross_instance_reopen_and_copy_dedup_alias_id(tmp_path):
    path = tmp_path / "audit.sqlite3"
    first = service(path)
    saved = run(first)[0]
    second_stream = SyntheticStream()
    second = service(path, second_stream)
    again = run(second, [case("different-host-label")])[0]
    assert again["audit_ref"] == saved["audit_ref"]
    assert again["deduplicated"] and not second_stream.calls
    first.close()
    second.close()
    copied = tmp_path / "restored.db"
    shutil.copyfile(path, copied)
    restored = service(copied, second_stream)
    assert run(restored)[0]["deduplicated"]
    assert restored.get(saved["audit_ref"])["result"] == saved["result"]
    restored.close()


def _race_worker(path, marker, barrier, queue):
    svc = service(path, SyntheticStream(marker=marker))
    try:
        barrier.wait(timeout=15)
        receipt = run(svc)[0]
        queue.put((receipt["audit_ref"], receipt["deduplicated"]))
    finally:
        svc.close()


def test_process_race_reserves_once(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue, barrier = context.Queue(), context.Barrier(2)
    path, marker = str(tmp_path / "race.db"), str(tmp_path / "requests.txt")
    workers = [
        context.Process(target=_race_worker, args=(path, marker, barrier, queue)) for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        results = [queue.get(timeout=25) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert results[0][0] == results[1][0]
        assert sorted(value[1] for value in results) == [False, True]
        assert Path(marker).read_text().splitlines() == ["request"]
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        queue.close()


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda r: r["diagnostics"][0]["evidence"][0].update(quote="伪造"),
            "AUDIT_INVALID_EVIDENCE",
        ),
        (
            lambda r: r["diagnostics"][0]["evidence"][0].update(document_id="missing"),
            "AUDIT_INVALID_EVIDENCE",
        ),
        (lambda r: r["diagnostics"][0]["evidence"][0].update(end=999), "AUDIT_INVALID_EVIDENCE"),
        (lambda r: r["diagnostics"][0].update(evidence=[]), "AUDIT_INVALID_EVIDENCE"),
        (lambda r: r.update(diagnostics=[]), "AUDIT_UNSUPPORTED_CONCLUSION"),
        (lambda r: r.update(human_confirmed=True), "AUDIT_INVALID_RESULT"),
        (lambda r: r["diagnostics"][0].update(field_path="education"), "AUDIT_INVALID_FIELD_PATH"),
        (lambda r: r.update(verdict="consistent"), "AUDIT_UNSUPPORTED_CONCLUSION"),
    ],
)
def test_bad_evidence_and_schema_never_conclusive_or_retried(tmp_path, mutation, code):
    result = verdict()
    mutation(result)
    stream = SyntheticStream(result)
    svc = service(tmp_path / "audit.db", stream)
    saved = run(svc)[0]
    assert saved["status"] == "failed" and saved["error_code"] == code, saved
    assert saved["result"] is None
    assert run(svc)[0]["deduplicated"]
    assert len(stream.calls) == 1
    svc.close()


def test_inconclusive_needs_no_invented_evidence(tmp_path):
    svc = service(
        tmp_path / "audit.db",
        SyntheticStream(
            {
                "verdict": "inconclusive",
                "summary": "附件缺失，不能下结论。",
                "diagnostics": [
                    {
                        "field_path": "/education",
                        "finding": "uncertain",
                        "explanation": "缺少岗位表。",
                        "evidence": [],
                    }
                ],
            }
        ),
    )
    saved = run(svc)[0]
    assert saved["status"] == "completed"
    assert saved["result"]["verdict"] == "inconclusive"
    assert saved["result"]["provenance"] == ["ai_audited"]
    svc.close()


@pytest.mark.parametrize(
    "message",
    [
        AssistantMessage(
            stop_reason="error", error_message="secret-provider-error", response_id="ref"
        ),
        AssistantMessage(content=(TextContent(text="No submission"),), stop_reason="stop"),
        AssistantMessage(
            content=(ToolCall(id="evil", name="bash", arguments={"command": "id"}),),
            stop_reason="toolUse",
        ),
    ],
)
def test_provider_failure_no_submission_or_host_tool_never_retries(tmp_path, message):
    stream = SyntheticStream(message=message)
    svc = service(tmp_path / "audit.db", stream)
    saved = run(svc)[0]
    assert saved["status"] == "failed" and saved["result"] is None
    assert saved["requests"] == 1
    assert "secret-provider-error" not in json.dumps(saved)
    assert saved["cost_usd"] is None and saved["usage_status"] == "unknown"
    assert run(svc)[0]["deduplicated"] and len(stream.calls) == 1
    svc.close()


def test_cancellation_persists_and_reopen_does_not_retry(tmp_path):
    async def scenario():
        entered = asyncio.Event()

        class BlockingStream(SyntheticStream):
            async def __call__(self, *_args):
                entered.set()
                await asyncio.Event().wait()

        path = tmp_path / "audit.db"
        svc = service(path, BlockingStream())
        task = asyncio.create_task(svc.run_batch([case()]))
        await asyncio.wait_for(entered.wait(), 5)
        audit_ref = svc._reserve(case())[0]
        assert svc.get(audit_ref)["status"] == "dispatch_unknown"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = svc.get(audit_ref)
        assert saved["status"] == "cancelled" and saved["requests"] == 1
        assert saved["cost_usd"] is None and saved["result"] is None
        svc.close()
        stream = SyntheticStream()
        reopened = service(path, stream)
        assert (await reopened.run_batch([case()]))[0]["deduplicated"]
        assert not stream.calls
        reopened.close()

    asyncio.run(scenario())


def test_unknown_dispatched_and_pre_dispatch_reservations_survive_restore(tmp_path):
    path = tmp_path / "audit.db"
    svc = service(path)
    reserved, _ = svc._reserve(case("reserved"))
    changed = case("unknown", output={"education": "本科"})
    unknown, _ = svc._reserve(changed)
    svc._patch(unknown, status="dispatch_unknown", requests=1)
    svc.close()  # Simulated host loss, not a lease to steal.
    stream = SyntheticStream()
    reopened = service(path, stream)
    results = run(reopened, [case("alias"), changed])
    assert all(r["deduplicated"] for r in results) and not stream.calls
    assert reopened.get(reserved)["status"] == "reserved"
    assert reopened.get(unknown)["status"] == "dispatch_unknown"
    reopened.close()


def test_input_output_issues_and_prompt_identity(tmp_path, monkeypatch):
    svc = service(tmp_path / "audit.db")
    original = svc._identity(case())
    assert original == svc._identity(case("alias"))
    assert original != svc._identity(case(text="原文变更"))
    assert original != svc._identity(case(output={"education": "本科"}))
    assert original != svc._identity(case(issues=[{"code": "OTHER"}]))
    import youju.recipe_service.model_audit as audit

    monkeypatch.setattr(audit, "SYSTEM_PROMPT", audit.SYSTEM_PROMPT + "\nChanged contract")
    assert original != svc._identity(case())
    assert original["model"] == "gpt-6.1-sol"
    svc.close()


def test_live_requires_both_explicit_gates_before_reservation(tmp_path):
    for opt_in, enabled in [(False, False), (True, False), (False, True)]:
        config = XdogConfig(
            provider="copilot",
            small_model="unused",
            large_model="unused",
            author_model="unused",
            live_enabled=enabled,
        )
        svc = ModelAuditService(tmp_path / "audit.db", XdogModels(config), live_opt_in=opt_in)
        with pytest.raises(AuditError, match="AUDIT_LIVE_DISABLED"):
            run(svc)
        with svc.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT count(*) FROM model_audits_v1").scalar() == 0
        svc.close()


def test_official_provider_adapter_exact_model_and_no_fallback(tmp_path, monkeypatch):
    synthetic = SyntheticStream()

    class Provider:
        id = "copilot"

        def model(self, name):
            assert name == AUDIT_MODEL
            return replace(synthetic.metadata, id="copilot/" + AUDIT_MODEL, api="openai-responses")

        def models(self):
            return (self.model(AUDIT_MODEL),)

        def stream(self, model, context, options):
            return synthetic(model, context, options)

    monkeypatch.setattr(ai, "provider", lambda name: Provider() if name == "copilot" else None)
    config = XdogConfig(
        provider="copilot",
        small_model="unused",
        large_model="unused",
        author_model="not-the-auditor",
        live_enabled=True,
    )
    svc = ModelAuditService(tmp_path / "audit.db", XdogModels(config), live_opt_in=True)
    saved = run(svc)[0]
    assert saved["status"] == "completed", saved
    assert saved["identity"]["model"] == AUDIT_MODEL
    assert saved["identity"]["mode"] == "live"  # Mocked provider, NOT a real model experiment.
    svc.close()


def test_rejects_production_or_foreign_database(tmp_path):
    path = tmp_path / "source.db"
    engine = create_engine("sqlite:///" + str(path))
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sources (id TEXT)")
    engine.dispose()
    with pytest.raises(ValueError, match="non-audit"):
        service(path)
    engine = create_engine("sqlite:///" + str(path))
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).all() == [("sources",)]
    engine.dispose()


def test_full_three_case_batch_keeps_each_agent_independent(tmp_path):
    streams = []

    def factory():
        stream = SyntheticStream()
        streams.append(stream)
        return stream

    svc = ModelAuditService(tmp_path / "audit.db", XdogModels(), stream_factory=factory)
    cases = [case(str(n), issues=[{"code": str(n)}]) for n in range(3)]
    assert all(r["status"] == "completed" for r in run(svc, cases))
    assert len(streams) == 3 and all(len(s.calls) == 1 for s in streams)
    assert len({r["audit_ref"] for r in run(svc, cases)}) == 3
    assert len(streams) == 3
    svc.close()


def test_frozen_invalid_material_and_nonfinite_input():
    with pytest.raises(ValueError):
        case(output={"bad": float("nan")})
    with pytest.raises(ValueError):
        FrozenAuditCase.freeze(
            "bad", documents=[], current_output={}, output_status="review", issues=[]
        )
    payload = json.loads(case().snapshot_json)
    payload["documents"] *= 2
    with pytest.raises(ValueError):
        FrozenAuditCase("bad", json.dumps(payload))
    immutable = case()
    with pytest.raises(AttributeError):
        immutable.snapshot_json = "{}"
    assert copy.deepcopy(immutable) == immutable


def test_cli_json_list_format_and_whole_batch_prevalidation(tmp_path):
    item = {"case_id": "cli-case", **json.loads(case().snapshot_json)}
    stream = SyntheticStream()
    svc = service(tmp_path / "audit.db", stream)
    with pytest.raises(ValueError, match="exact frozen"):
        run(svc, [item, {**item, "unexpected": True}])
    assert not stream.calls
    with svc.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM model_audits_v1").scalar() == 0
    frozen = freeze_batch(json.loads(json.dumps([item])))
    assert len(frozen) == 1 and isinstance(frozen[0], FrozenAuditCase)
    expected_ref = svc.reference(item)
    receipt = run(svc, [item])[0]
    assert receipt["status"] == "completed" and receipt["audit_ref"] == expected_ref
    svc.close()


def test_provider_exception_is_safe_and_not_retried(tmp_path):
    class RaisingStream(SyntheticStream):
        def __call__(self, *_args):
            self.calls.append(True)
            raise RuntimeError("fixture private provider failure")

    stream = RaisingStream()
    svc = service(tmp_path / "audit.db", stream)
    saved = run(svc)[0]
    assert saved["status"] == "failed" and saved["error_code"] == "AUDIT_PROVIDER_FAILED"
    assert "private provider failure" not in json.dumps(saved)
    assert run(svc)[0]["deduplicated"] and len(stream.calls) == 1
    svc.close()


def test_timeout_no_retry_and_valid_supported_result(tmp_path):
    class BlockingStream(SyntheticStream):
        async def __call__(self, *_args):
            await asyncio.Event().wait()

    path = tmp_path / "audit.db"
    svc = service(path, BlockingStream(), timeout_seconds=0.02)
    saved = run(svc)[0]
    assert saved["status"] == "failed" and saved["error_code"] == "AUDIT_TIMEOUT"
    assert saved["requests"] == 1
    svc.close()
    stream = SyntheticStream()
    svc = service(path, stream)
    assert run(svc)[0]["deduplicated"] and not stream.calls
    result = verdict()
    result["verdict"] = "consistent"
    result["diagnostics"][0]["finding"] = "supported"
    svc.close()
    svc = service(tmp_path / "supported.db", SyntheticStream(result))
    supported = run(svc)[0]
    assert supported["status"] == "completed"
    assert supported["result"]["provenance"] == ["ai_audited"]
    assert supported["result"]["accuracy_verified"] is False
    svc.close()


@pytest.mark.parametrize(
    "api,model_id", [("openai-completions", AUDIT_MODEL), ("openai-responses", "different-model")]
)
def test_unsafe_retrying_protocol_and_model_fallback_are_refused(
    tmp_path, monkeypatch, api, model_id
):
    class Provider:
        id = "copilot"

        def model(self, _name):
            return Model(id=model_id, api=api, supports_tool_calls=True)

        def stream(self, *_args):
            pytest.fail("Rejected protocol/model must never dispatch")

    monkeypatch.setattr(ai, "provider", lambda _name: Provider())
    config = XdogConfig(
        provider="copilot", small_model="unused", large_model="unused", live_enabled=True
    )
    svc = ModelAuditService(tmp_path / "audit.db", XdogModels(config), live_opt_in=True)
    saved = run(svc)[0]
    assert saved["status"] == "failed" and saved["requests"] == 0
    assert saved["error_code"] in {"AUDIT_UNSAFE_PROVIDER_PROTOCOL", "AUDIT_MODEL_UNAVAILABLE"}
    assert run(svc)[0]["deduplicated"]
    svc.close()


def test_iterator_failure_redacted_before_sdk_logging_and_no_retry(tmp_path, caplog):
    class BrokenStream(SyntheticStream):
        def __call__(self, *_args):
            self.calls.append(True)

            async def broken():
                if False:
                    yield
                raise RuntimeError("fixture-untrusted-provider-detail")

            return EventStream.from_async_generator(broken())

    stream = BrokenStream()
    svc = service(tmp_path / "audit.db", stream)
    saved = run(svc)[0]
    assert saved["error_code"] == "AUDIT_PROVIDER_FAILED" and saved["requests"] == 1
    assert "fixture-untrusted-provider-detail" not in caplog.text
    assert run(svc)[0]["deduplicated"] and len(stream.calls) == 1
    svc.close()
