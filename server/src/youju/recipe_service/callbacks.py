"""Explicit, one-shot callback delivery; never starts a listener or an Agent.

Operator configuration (also consumed by ``rpc.register_recipe_rpc``)::

    RECIPE_CALLBACK_TARGETS = {
        "source-server": {"url": "http://127.0.0.1:9000/recipe-events",
                          "token": "operator-provided-callback-secret"},
    }

Run ``await CallbackDispatcher(service, targets).run_once()`` explicitly to claim
and attempt one event. The target ID is resolved ONLY through this configuration,
never through the event payload. HTTP is allowed for explicitly configured internal
endpoints; deployments crossing trust boundaries should configure HTTPS.

Delivery is at-least-once: receivers must deduplicate ``Idempotency-Key`` (also
``X-Recipe-Event-ID`` and JSON ``event_id``). A callback retry never retries a job.
No response bodies, authentication values or exception text enter the outbox.
"""

import asyncio
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import and_, or_, select

from .models import Outbox

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
MAX_PAYLOAD_BYTES = 64 * 1024


@dataclass(frozen=True)
class CallbackTarget:
    url: str
    token: str = field(repr=False)


def valid_identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def valid_token(value):
    # Validate before putting a static operator secret into an HTTP header.
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and all(33 <= ord(character) <= 126 for character in value)
    )


def load_callback_targets(config: Mapping) -> dict[str, CallbackTarget]:
    """Validate operator configuration without touching the filesystem or network."""
    if not isinstance(config, Mapping):
        raise ValueError("Invalid callback target configuration")
    targets = {}
    for target_id, entry in config.items():
        if (
            not valid_identifier(target_id)
            or not isinstance(entry, Mapping)
            or set(entry) != {"url", "token"}
            or not valid_token(entry["token"])
        ):
            raise ValueError("Invalid callback target configuration")
        url = entry["url"]
        try:
            if (
                not isinstance(url, str)
                or not 1 <= len(url) <= 2048
                or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
                or "\\" in url
            ):
                raise ValueError
            parsed = urlsplit(url)
            normalized = httpx.URL(url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or "#" in url
                or not normalized.is_absolute_url
                or not normalized.host
                or "*" in normalized.host
            ):
                raise ValueError
            # Accessing port also validates malformed/non-numeric ports.
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except (TypeError, ValueError, httpx.InvalidURL):
            raise ValueError("Invalid callback target configuration") from None
        targets[target_id] = CallbackTarget(url=url, token=entry["token"])
    return targets


class CallbackDispatcher:
    """Durable token leases fence concurrent and crashed callback dispatchers.

    Network I/O happens outside ``service._transaction()``. A crash after a remote
    acceptance but before committing delivery can resend the SAME event ID. An
    expired owner's eventual result cannot overwrite its successor's result.
    ``transport`` is an optional httpx transport, intended for offline tests.
    """

    def __init__(
        self,
        service,
        targets: Mapping,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10,
        lease_seconds: float = 60,
        retry_seconds: float = 5,
        max_retry_seconds: float = 3600,
        clock: Callable[[], float] = time.time,
    ):
        for value in (timeout_seconds, lease_seconds, retry_seconds, max_retry_seconds):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError("Invalid callback timing configuration")
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid callback timing configuration")
        if (
            timeout_seconds > 60
            or lease_seconds < timeout_seconds + 5
            or max_retry_seconds < retry_seconds
        ):
            raise ValueError("Invalid callback timing configuration")
        self.service = service
        self.targets = load_callback_targets(targets)
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.lease_seconds = lease_seconds
        self.retry_seconds = retry_seconds
        self.max_retry_seconds = max_retry_seconds
        self.clock = clock

    def _claim(self):
        now = self.clock()
        with self.service._transaction() as session:
            row = session.scalar(
                select(Outbox)
                .where(
                    or_(
                        and_(Outbox.status == "pending", Outbox.available_at <= now),
                        and_(Outbox.status == "delivering", Outbox.lease_expires_at <= now),
                    )
                )
                .order_by(Outbox.available_at, Outbox.created_at, Outbox.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "delivering"
            row.lease_token = uuid4().hex
            row.lease_expires_at = now + self.lease_seconds
            row.attempts += 1
            return {
                "event_id": row.id,
                "job_id": row.job_id,
                "target_id": row.target_id,
                "payload": row.payload,
                "lease_token": row.lease_token,
                "attempts": row.attempts,
            }

    def _finish(self, delivery, error_code):
        now = self.clock()
        status = "pending" if error_code else "delivered"
        with self.service._transaction() as session:
            row = session.get(Outbox, delivery["event_id"])
            if (
                row is None
                or row.status != "delivering"
                or row.lease_token != delivery["lease_token"]
                or row.lease_expires_at is None
                or row.lease_expires_at <= now
            ):
                status = "superseded"
            else:
                row.status = status
                row.last_error = error_code
                row.lease_token = None
                row.lease_expires_at = None
                if error_code:
                    delay = min(
                        self.max_retry_seconds,
                        self.retry_seconds * 2 ** min(max(row.attempts - 1, 0), 30),
                    )
                    row.available_at = now + delay
        return {
            "event_id": delivery["event_id"],
            "job_id": delivery["job_id"],
            "status": status,
            "attempts": delivery["attempts"],
            "error_code": error_code,
        }

    async def _send(self, delivery):
        target = self.targets.get(delivery["target_id"])
        if target is None:
            return "CALLBACK_TARGET_UNKNOWN"
        try:
            if not isinstance(delivery["payload"], dict):
                return "CALLBACK_PAYLOAD_INVALID"
            body = json.dumps(
                {**delivery["payload"], "event_id": delivery["event_id"]},
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            return "CALLBACK_PAYLOAD_INVALID"
        if len(body) > MAX_PAYLOAD_BYTES:
            return "CALLBACK_PAYLOAD_TOO_LARGE"
        headers = {
            "Authorization": f"Bearer {target.token}",
            "Content-Type": "application/json",
            "Idempotency-Key": delivery["event_id"],
            "X-Recipe-Event-ID": delivery["event_id"],
        }
        try:
            # Include connect/write/read/cleanup within one whole-attempt deadline.
            # Streaming intentionally does not consume an untrusted response body.
            async with asyncio.timeout(self.timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(self.timeout_seconds),
                ) as client:
                    async with client.stream(
                        "POST", target.url, content=body, headers=headers
                    ) as response:
                        if 200 <= response.status_code < 300:
                            return None
                        if 300 <= response.status_code < 400:
                            return "CALLBACK_REDIRECT_REJECTED"
                        return "CALLBACK_HTTP_ERROR"
        except (TimeoutError, httpx.TimeoutException):
            return "CALLBACK_TIMEOUT"
        except httpx.HTTPError:
            return "CALLBACK_NETWORK_ERROR"
        except Exception:
            # Transport hooks may raise arbitrary exceptions containing credentials.
            return "CALLBACK_DELIVERY_ERROR"

    async def run_once(self):
        """Attempt at most one event; return redacted delivery metadata or None."""
        delivery = self._claim()
        if delivery is None:
            return None
        try:
            error_code = await self._send(delivery)
        except asyncio.CancelledError:
            self._finish(delivery, "CALLBACK_CANCELLED")
            raise
        return self._finish(delivery, error_code)
