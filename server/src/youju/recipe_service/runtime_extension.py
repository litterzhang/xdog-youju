"""Optional reverse queries: a local contract, never an import of the source runtime.

All identity is supplied by the trusted host. Query results are untrusted data,
not instructions or acceptance evidence. The remote server must authorize every
request independently; a binding is not a substitute for server authorization.
"""

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
MAX_CURSOR = 2**31 - 1
MAX_PAGE_BYTES = 64 * 1024


class RuntimeExtensionError(ValueError):
    """Only fixed, secret-free error codes cross the tool boundary."""

    def __init__(self, code="RUNTIME_QUERY_UNAVAILABLE"):
        if code not in {
            "RUNTIME_QUERY_UNAVAILABLE",
            "RUNTIME_QUERY_INVALID",
            "RUNTIME_QUERY_LIMIT",
            "RUNTIME_QUERY_RESPONSE_INVALID",
        }:
            code = "RUNTIME_QUERY_UNAVAILABLE"
        super().__init__(code)
        self.code = code


def identifier(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or value != value.strip()
    ):
        raise RuntimeExtensionError("RUNTIME_QUERY_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    workspace_id: str
    source_id: str
    task_id: str
    input_revision: str

    def __post_init__(self):
        for value in (self.workspace_id, self.source_id, self.task_id, self.input_revision):
            identifier(value)


class RecipeRuntimeExtension(Protocol):
    """One assigned task, async read-only access. No caller-selected scope or URLs.

    Every method returns {items: [JSON objects], next_cursor: int | None}.
    Read methods page sections/rows of one notice or its saved parse result;
    they must never fetch material, run parsing, repair or change stored data.
    Implementations must propagate cancellation and bound their response sizes.
    """

    @property
    def binding(self) -> RuntimeBinding: ...

    async def list_notices(self, *, cursor: int = 0, limit: int = DEFAULT_PAGE_SIZE) -> dict: ...

    async def read_notice(
        self, notice_id: str, *, cursor: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> dict: ...

    async def read_parse_result(
        self, notice_id: str, *, cursor: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> dict: ...


RuntimeExtensionFactory = Callable[[RuntimeBinding], RecipeRuntimeExtension | None]


def pagination(cursor, limit):
    if (
        type(cursor) is not int
        or not 0 <= cursor <= MAX_CURSOR
        or type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_SIZE
    ):
        raise RuntimeExtensionError("RUNTIME_QUERY_INVALID")


def page_json(page, *, cursor, limit):
    """Validate all transports and injected implementations again at the tool edge."""
    if not isinstance(page, dict) or set(page) != {"items", "next_cursor"}:
        raise RuntimeExtensionError("RUNTIME_QUERY_RESPONSE_INVALID")
    items, next_cursor = page["items"], page["next_cursor"]
    if (
        not isinstance(items, list)
        or len(items) > limit
        or any(not isinstance(item, dict) for item in items)
        or (
            next_cursor is not None
            and (type(next_cursor) is not int or not cursor < next_cursor <= MAX_CURSOR)
        )
    ):
        raise RuntimeExtensionError("RUNTIME_QUERY_RESPONSE_INVALID")
    try:
        text = json.dumps({"untrusted": True, "data": page}, ensure_ascii=False, allow_nan=False)
        if len(text.encode("utf-8")) > MAX_PAGE_BYTES:
            raise RuntimeExtensionError("RUNTIME_QUERY_LIMIT")
    except (TypeError, UnicodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, RuntimeExtensionError):
            raise
        raise RuntimeExtensionError("RUNTIME_QUERY_RESPONSE_INVALID") from None
    return text


@dataclass(frozen=True, slots=True, repr=False)
class HttpRecipeRuntimeExtension:
    """Trusted configured endpoint only; no arbitrary URL, path or redirect tool.

    A fresh client per query owns/cleans up its connection even on cancellation.
    The optional transport is an internal host/test boundary, not Agent input.
    HTTP (without TLS) is allowed only for explicit loopback configuration.
    """

    binding: RuntimeBinding
    endpoint: str = field(repr=False, kw_only=True)
    caller_id: str = field(repr=False, kw_only=True)
    token: str = field(repr=False, kw_only=True)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False, kw_only=True)
    timeout: float = field(default=10, kw_only=True)

    def __post_init__(self):
        try:
            url = httpx.URL(self.endpoint)
            valid = (
                isinstance(self.binding, RuntimeBinding)
                and url.scheme in {"https", "http"}
                and bool(url.host)
                and not url.userinfo
                and not url.query
                and not url.fragment
                and (url.scheme == "https" or url.host in {"127.0.0.1", "::1", "localhost"})
                and isinstance(self.token, str)
                and 1 <= len(self.token) <= 4096
                and all(33 <= ord(c) <= 126 for c in self.token)
                and isinstance(self.caller_id, str)
                and 1 <= len(self.caller_id) <= 128
                and all(33 <= ord(c) <= 126 for c in self.caller_id)
                and type(self.timeout) in {int, float}
                and math.isfinite(self.timeout)
                and 0 < self.timeout <= 60
            )
        except (TypeError, ValueError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise ValueError("Invalid trusted runtime query configuration") from None

    async def list_notices(self, *, cursor=0, limit=DEFAULT_PAGE_SIZE):
        return await self._query("list_notices", cursor=cursor, limit=limit)

    async def read_notice(self, notice_id, *, cursor=0, limit=DEFAULT_PAGE_SIZE):
        return await self._query("read_notice", notice_id=notice_id, cursor=cursor, limit=limit)

    async def read_parse_result(self, notice_id, *, cursor=0, limit=DEFAULT_PAGE_SIZE):
        return await self._query(
            "read_parse_result", notice_id=notice_id, cursor=cursor, limit=limit
        )

    async def _query(self, operation, *, cursor, limit, notice_id=None):
        pagination(cursor, limit)
        body = {
            "operation": operation,
            "source_id": self.binding.source_id,
            "workspace_id": self.binding.workspace_id,
            "task_id": self.binding.task_id,
            "input_revision": self.binding.input_revision,
            "cursor": cursor,
            "limit": limit,
        }
        if operation != "list_notices":
            body["notice_id"] = identifier(notice_id)
        try:
            async with (
                asyncio.timeout(self.timeout),
                httpx.AsyncClient(
                    transport=self.transport,
                    timeout=self.timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    self.endpoint,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + self.token,
                        "X-Recipe-Caller": self.caller_id,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeExtensionError()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise RuntimeExtensionError("RUNTIME_QUERY_RESPONSE_INVALID")
                    length = response.headers.get("content-length")
                    if length is not None and (
                        not length.isdecimal() or int(length) > MAX_PAGE_BYTES
                    ):
                        raise RuntimeExtensionError("RUNTIME_QUERY_LIMIT")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_PAGE_BYTES:
                            raise RuntimeExtensionError("RUNTIME_QUERY_LIMIT")
                    page = json.loads(chunks)
                    page_json(page, cursor=cursor, limit=limit)
                    return page
        except RuntimeExtensionError:
            raise
        except Exception:
            # Never include response bodies, headers, URLs, tokens or exception text.
            # CancelledError (BaseException) deliberately propagates unchanged.
            raise RuntimeExtensionError() from None
