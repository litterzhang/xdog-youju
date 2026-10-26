"""HTML acquisition contracts only; this module sends no requests and launches no browsers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Protocol

from youju.models.base import validate_http_url


class CrawlerMode(StrEnum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    HEADED = "headed"  # A real browser with a visible window.

    @property
    def headless(self) -> bool | None:
        if self is CrawlerMode.STATIC:
            return None
        return self is CrawlerMode.DYNAMIC


class HtmlKind(StrEnum):
    RESPONSE_HTML = "response_html"
    RENDERED_DOM = "rendered_dom"


class CrawlErrorCode(StrEnum):
    POLICY_BLOCKED = "policy_blocked"
    TIMEOUT = "timeout"
    NETWORK = "network"
    NOT_HTML = "not_html"
    SIZE_LIMIT = "size_limit"
    BROWSER_UNAVAILABLE = "browser_unavailable"


class CrawlError(Exception):
    """A failed acquisition; never replace it with fabricated or empty HTML."""

    def __init__(self, code: CrawlErrorCode, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, kw_only=True)
class CrawlRequest:
    url: str
    mode: CrawlerMode = CrawlerMode.STATIC
    timeout_seconds: float = 30
    max_html_bytes: int = 2 * 1024 * 1024
    ready_selector: str | None = None

    def __post_init__(self):
        validate_http_url(self.url)
        if not isinstance(self.mode, CrawlerMode):
            raise TypeError("mode must be a CrawlerMode")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if type(self.max_html_bytes) is not int or self.max_html_bytes <= 0:
            raise ValueError("max_html_bytes must be a positive integer")
        if self.ready_selector is not None:
            if self.mode is CrawlerMode.STATIC:
                raise ValueError("Static HTTP acquisition cannot wait for a rendered element")
            if not isinstance(self.ready_selector, str) or not self.ready_selector.strip():
                raise ValueError("ready_selector must be a nonblank CSS selector")


@dataclass(frozen=True, kw_only=True)
class CrawlResponse:
    """An observed HTML snapshot, not a guarantee of HTTP success or recruitment validity."""

    request: CrawlRequest
    final_url: str
    html: str = field(repr=False)
    http_status: int | None
    captured_at: datetime
    # Entity body before text decoding, when available; not compressed wire bytes.
    response_body: bytes | None = field(default=None, repr=False)

    # Original response header only, never document.lastModified's synthetic clock.
    last_modified: str | None = None

    def __post_init__(self):
        if self.last_modified is not None and (
            not isinstance(self.last_modified, str) or len(self.last_modified) > 256
        ):
            raise ValueError("last_modified must be bounded response-header text")
        validate_http_url(self.final_url)
        if not isinstance(self.html, str) or not self.html.strip():
            raise ValueError("html must contain an actual nonblank HTML snapshot")
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("http_status must be an HTTP status or None when unobserved")
        if self.request.mode is CrawlerMode.STATIC and (
            self.http_status is None or self.response_body is None
        ):
            raise ValueError("Static acquisition must preserve its HTTP status and response body")
        if self.request.mode is CrawlerMode.STATIC and self.http_status in {204, 205, 304}:
            raise ValueError("This HTTP status has no new HTML body to capture")
        if not isinstance(self.captured_at, datetime) or self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.response_body is not None and not isinstance(self.response_body, bytes):
            raise TypeError("response_body must be bytes")
        if len(self.html.encode("utf-8")) > self.request.max_html_bytes or (
            self.response_body is not None and len(self.response_body) > self.request.max_html_bytes
        ):
            raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "HTML capture exceeds the requested limit")
        object.__setattr__(self, "captured_at", self.captured_at.astimezone(UTC))

    @property
    def html_kind(self) -> HtmlKind:
        if self.request.mode is CrawlerMode.STATIC:
            return HtmlKind.RESPONSE_HTML
        return HtmlKind.RENDERED_DOM


class Crawler(Protocol):
    """Shared interface for HTTP and CDP adapters, independent of storage and parsing."""

    async def crawl(self, request: CrawlRequest) -> CrawlResponse:
        """Acquire HTML or raise CrawlError; source approval and network policy remain mandatory."""
        ...
