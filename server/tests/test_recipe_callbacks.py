"""Real SQLite outbox tests with httpx.MockTransport only; no real callbacks."""

import asyncio
import fcntl
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from youju.recipe_service import RecipeService
from youju.recipe_service.callbacks import CallbackDispatcher, load_callback_targets
from youju.recipe_service.models import Job, Outbox, Workspace

TARGETS = {
    "source-events": {"url": "https://receiver.invalid/events", "token": "synthetic-private-token"}
}
STAMP = "2026-10-12T00:00:00+00:00"


@pytest.fixture
def outbox(tmp_path):
    service = RecipeService(tmp_path)
    ws_id, job_id, event_id = "a" * 64, uuid4().hex, uuid4().hex
    with service._transaction() as session:
        session.add(
            Workspace(
                id=ws_id,
                source_id="source",
                physical_path=str(service.root / ws_id),
                owner_id="source-server",
                lifecycle="managed",
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.flush()
        session.add(
            Job(
                id=job_id,
                workspace_id=ws_id,
                request_id="build-1",
                status="failed",
                input_digest="b" * 64,
                context={},
                created_at=STAMP,
                updated_at=STAMP,
            )
        )
        session.flush()
        session.add(
            Outbox(
                id=event_id,
                job_id=job_id,
                target_id="source-events",
                payload={"job_id": job_id, "status": "failed", "event_id": "untrusted-payload-id"},
                status="pending",
                attempts=0,
                available_at=1000,
                created_at=STAMP,
            )
        )
    try:
        yield service, job_id, event_id
    finally:
        service.close()


def _row(service, event_id):
    with Session(service.engine) as session:
        row = session.get(Outbox, event_id)
        return {
            key: getattr(row, key)
            for key in (
                "id",
                "status",
                "attempts",
                "available_at",
                "lease_token",
                "lease_expires_at",
                "last_error",
            )
        }


class UnreadBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        pytest.fail("Dispatcher consumed an untrusted callback response body")
        yield b""


def test_durable_retry_same_event_id_and_never_reruns_job(outbox):
    service, job_id, event_id = outbox
    clock, requests = [1000.0], []

    def receive(request):
        requests.append(request)
        assert str(request.url) == TARGETS["source-events"]["url"]
        assert request.headers["Authorization"] == "Bearer synthetic-private-token"
        assert request.headers["Idempotency-Key"] == event_id
        assert request.headers["X-Recipe-Event-ID"] == event_id
        assert json.loads(request.content)["event_id"] == event_id
        assert _row(service, event_id)["status"] == "delivering"
        # The durable claim has committed and the metadata lock is NOT held over HTTP.
        with (service.root / ".metadata.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return httpx.Response(503 if len(requests) == 1 else 204, stream=UnreadBody())

    dispatcher = CallbackDispatcher(
        service, TARGETS, transport=httpx.MockTransport(receive), clock=lambda: clock[0]
    )
    first = asyncio.run(dispatcher.run_once())
    assert first["status"] == "pending" and first["error_code"] == "CALLBACK_HTTP_ERROR"
    row = _row(service, event_id)
    assert row["available_at"] == 1005 and row["attempts"] == 1 and row["lease_token"] is None
    assert asyncio.run(dispatcher.run_once()) is None
    service.close()
    reopened = RecipeService(service.storage)
    try:
        clock[0] = 1005
        second = CallbackDispatcher(
            reopened, TARGETS, transport=httpx.MockTransport(receive), clock=lambda: clock[0]
        )
        result = asyncio.run(second.run_once())
        assert result["status"] == "delivered" and result["event_id"] == first["event_id"]
        assert _row(reopened, event_id)["last_error"] is None
        assert asyncio.run(second.run_once()) is None
        with Session(reopened.engine) as session:
            job = session.get(Job, job_id)
            assert job.status == "failed" and job.owner is None
        assert len(requests) == 2
    finally:
        reopened.close()


def test_concurrent_dispatchers_and_expired_lease_fencing(outbox):
    service, _, event_id = outbox
    clock = [1000.0]

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def receive(_):
            started.set()
            await release.wait()
            return httpx.Response(204)

        first = CallbackDispatcher(
            service, TARGETS, transport=httpx.MockTransport(receive), clock=lambda: clock[0]
        )
        second = CallbackDispatcher(
            service, TARGETS, transport=httpx.MockTransport(receive), clock=lambda: clock[0]
        )
        running = asyncio.create_task(first.run_once())
        await asyncio.wait_for(started.wait(), 2)
        assert await second.run_once() is None
        # Simulate a suspended/crashed owner whose lease expires during HTTP.
        clock[0] = 1061
        successor = second._claim()
        assert successor["event_id"] == event_id and successor["attempts"] == 2
        second._finish(successor, None)
        release.set()
        assert (await running)["status"] == "superseded"
        assert _row(service, event_id)["status"] == "delivered"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("redirect", "CALLBACK_REDIRECT_REJECTED"),
        ("timeout", "CALLBACK_TIMEOUT"),
        ("network", "CALLBACK_NETWORK_ERROR"),
        ("unexpected", "CALLBACK_DELIVERY_ERROR"),
    ],
)
def test_delivery_failures_are_bounded_redacted_and_ignore_environment(
    outbox, monkeypatch, failure, expected
):
    service, _, event_id = outbox
    calls, settings = [], []
    client_type = httpx.AsyncClient
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:1234")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")

    def capture_client(**kwargs):
        settings.append(kwargs)
        return client_type(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", capture_client)

    async def receive(request):
        calls.append(request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "http://169.254.169.254/private"})
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "network":
            raise httpx.ConnectError(
                "synthetic-private-token https://secret.invalid", request=request
            )
        raise RuntimeError("synthetic-private-token https://secret.invalid")

    dispatcher = CallbackDispatcher(
        service,
        TARGETS,
        transport=httpx.MockTransport(receive),
        timeout_seconds=0.02,
        clock=lambda: 1000,
    )
    result = asyncio.run(dispatcher.run_once())
    assert result["status"] == "pending" and result["error_code"] == expected
    assert len(calls) == 1  # Redirects are not followed; errors are not retried inline.
    assert settings[0]["trust_env"] is False and settings[0]["follow_redirects"] is False
    assert settings[0]["timeout"].read == 0.02
    assert _row(service, event_id)["last_error"] == expected
    assert "synthetic-private-token" not in json.dumps(result)


def test_cancellation_releases_lease_without_reporting_delivery(outbox):
    service, _, event_id = outbox

    async def scenario():
        started = asyncio.Event()

        async def receive(_):
            started.set()
            await asyncio.Event().wait()

        dispatcher = CallbackDispatcher(
            service, TARGETS, transport=httpx.MockTransport(receive), clock=lambda: 1000
        )
        task = asyncio.create_task(dispatcher.run_once())
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = _row(service, event_id)
        assert row["status"] == "pending" and row["last_error"] == "CALLBACK_CANCELLED"
        assert row["lease_token"] is None

    asyncio.run(scenario())


def test_unknown_target_and_oversized_payload_never_send(outbox):
    service, _, event_id = outbox
    transport = httpx.MockTransport(lambda _: pytest.fail("Unexpected callback"))
    dispatcher = CallbackDispatcher(service, {}, transport=transport, clock=lambda: 1000)
    assert asyncio.run(dispatcher.run_once())["error_code"] == "CALLBACK_TARGET_UNKNOWN"
    with service._transaction() as session:
        row = session.get(Outbox, event_id)
        row.available_at = 1000
        row.payload = {"message": "x" * 65537, "url": "http://arbitrary.invalid"}
    dispatcher = CallbackDispatcher(service, TARGETS, transport=transport, clock=lambda: 1000)
    assert asyncio.run(dispatcher.run_once())["error_code"] == "CALLBACK_PAYLOAD_TOO_LARGE"


def test_callback_configuration_rejects_nonexact_unsafe_targets_without_echoing_secrets():
    for url in (
        "file:///tmp/a",
        "ftp://receiver.invalid/a",
        "https://user:synthetic-secret@receiver.invalid/a",
        "https://receiver.invalid/a#fragment",
        "https://receiver.invalid/a\r\nX-Auth: secret",
        "https://receiver.invalid\\@attacker.invalid/a",
        "https://*.invalid/a",
        "https://receiver.invalid:99999/a",
    ):
        with pytest.raises(ValueError) as error:
            load_callback_targets({"events": {"url": url, "token": "synthetic-private-token"}})
        assert url not in str(error.value) and "synthetic-private-token" not in str(error.value)
    with pytest.raises(ValueError):
        load_callback_targets({"events": {"url": "https://receiver.invalid/a", "token": "x\ny"}})


def test_retry_backoff_is_persisted_and_capped(outbox):
    service, _, event_id = outbox
    clock = [1000.0]
    dispatcher = CallbackDispatcher(
        service,
        TARGETS,
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
        retry_seconds=2,
        max_retry_seconds=5,
        clock=lambda: clock[0],
    )
    for delay in (2, 4, 5, 5):
        assert asyncio.run(dispatcher.run_once())["status"] == "pending"
        assert _row(service, event_id)["available_at"] == clock[0] + delay
        clock[0] += delay
