"""Shared bounded HTTP access for static pages and intercepted browser resources."""

import asyncio
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from math import isfinite
from time import monotonic, time
from urllib.parse import urljoin

import httpx
from protego import Protego

from .contracts import CrawlError, CrawlErrorCode, CrawlRequest
from .policy import OriginPolicy, origin

REDIRECTS = {301, 302, 303, 307, 308}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
USER_AGENT = "YoujuCrawler/0.1"
ROBOTS_AGENT = "YoujuCrawler"
ROBOTS_LIMIT = 512 * 1024
ROBOTS_CACHE_SECONDS = 300


@dataclass(frozen=True)
class _Page:
    url: httpx.URL
    status: int
    headers: httpx.Headers
    body: bytes


@dataclass(frozen=True)
class _RobotsCacheEntry:
    rules: Protego
    expires_at: float  # Monotonic deadline, never extended by a cache hit.
    delay: float
    checked_urls: tuple[str, ...]  # Preserve the verified redirect chain's scope.


def _delta_seconds(value: str) -> int | None:
    value = value.strip()
    if not value.isascii() or not value.isdecimal():
        return None
    # RFC 9111 delta-seconds overflow is conservatively saturated at 2**31.
    return min(int(value), 2**31) if len(value) <= 10 else 2**31


def _robots_expires_at(headers: httpx.Headers, started: float, received: float) -> float:
    """Bound local residence and origin freshness; no stale/revalidation fallback.

    RFC 9111's Age/Date corrected initial age is subtracted before the 300-second
    local residence cap. Including DNS/pacing in response delay is conservative.
    Ambiguous freshness headers disable caching, not this verified rules response.
    """
    lifetime = ROBOTS_CACHE_SECONDS
    max_age_seen = False
    for directive in headers.get_list("cache-control", split_commas=True):
        name, separator, value = directive.strip().partition("=")
        name = name.strip().lower()
        if name in {"no-store", "no-cache"}:
            return received
        if name == "max-age":
            if max_age_seen or not separator:
                return received
            max_age_seen = True
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            parsed = _delta_seconds(value)
            if parsed is None:
                return received
            lifetime = parsed
    age = _delta_seconds(headers.get("age", "0"))
    if age is None:
        return received
    apparent_age = 0.0
    if "date" in headers:
        try:
            dated = parsedate_to_datetime(headers["date"])
            if dated.tzinfo is None:
                dated = dated.replace(tzinfo=UTC)
            apparent_age = max(0.0, time() - dated.timestamp())
        except (TypeError, ValueError, OverflowError):
            return received
    initial_age = max(apparent_age, age + max(0.0, received - started))
    return received + min(ROBOTS_CACHE_SECONDS, max(0.0, lifetime - initial_age))


class HttpAccess:
    """Internal transport with one worker lock and per-host pacing."""

    def __init__(
        self,
        policy: OriginPolicy,
        *,
        min_interval_seconds: float = 1,
        max_redirects: int = 3,
        _transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not isfinite(min_interval_seconds) or min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be finite and positive")
        if type(max_redirects) is not int or not 0 <= max_redirects <= 10:
            raise ValueError("max_redirects must be between 0 and 10")
        self.policy = policy
        self.min_interval = min_interval_seconds
        self.max_redirects = max_redirects
        self._transport = (
            _transport  # A transport seam for offline tests, not public configuration.
        )
        self._lock = asyncio.Lock()
        self._last_request: dict[str, float] = {}
        self._delays: dict[str, float] = {}
        # Share this dictionary together with the existing worker lock/pacing state.
        self._robots_cache: dict[str, _RobotsCacheEntry] = {}

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
            timeout=timeout,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )

    async def _wait_for_slot(self, url: httpx.URL):
        delay = max(self.min_interval, self._delays.get(url.host, 0))
        remaining = self._last_request.get(url.host, 0) + delay - monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._last_request[url.host] = monotonic()

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: httpx.URL,
        limit: int,
        *,
        robots: bool = False,
        media_types: frozenset[str] | None = None,
    ) -> _Page:
        ip = await self.policy.resolve_public_ip(url)
        await self._wait_for_slot(url)
        # Connect only to the validated IP, retaining the original Host and TLS identity.
        # No keep-alive/cookies: two allowed hosts sharing an IP must not share their identity.
        client.cookies.clear()
        async with client.stream(
            "GET",
            url.copy_with(host=ip),
            headers={
                "Host": url.netloc.decode("ascii"),
                "User-Agent": USER_AGENT,
                "Accept": "text/plain" if robots else ", ".join(sorted(media_types or HTML_TYPES)),
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
            extensions={"sni_hostname": url.host},
        ) as response:
            status = response.status_code
            if status in REDIRECTS or (robots and status != 200):
                return _Page(url, status, response.headers, b"")
            if status in {204, 205, 206, 304}:
                raise CrawlError(CrawlErrorCode.NOT_HTML, "No complete new HTML body was returned")
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if robots:
                if media_type not in {"", "text/plain"}:
                    raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "robots.txt is not plain text")
            elif media_type not in (media_types or HTML_TYPES):
                raise CrawlError(CrawlErrorCode.NOT_HTML, "The response media type is not allowed")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise CrawlError(CrawlErrorCode.NETWORK, "Encoded responses are not supported yet")
            length = response.headers.get("content-length")
            if (
                length
                and length.isdecimal()
                and (len(length) > len(str(limit)) or int(length) > limit)
            ):
                raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "Response body exceeds the size limit")
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > limit:
                    raise CrawlError(
                        CrawlErrorCode.SIZE_LIMIT, "Response body exceeds the size limit"
                    )
                body.extend(chunk)
            return _Page(url, status, response.headers, bytes(body))

    def _redirect(self, page: _Page) -> httpx.URL:
        location = page.headers.get("location")
        if not location:
            raise CrawlError(CrawlErrorCode.NETWORK, "Redirect has no destination")
        try:
            target = urljoin(str(page.url), location)
        except ValueError as error:
            raise CrawlError(
                CrawlErrorCode.POLICY_BLOCKED, "Invalid redirect destination"
            ) from error
        return self.policy.check_url(target)

    async def _robots(self, client: httpx.AsyncClient, url: httpx.URL) -> Protego:
        key = origin(url)
        target = self.policy.check_url(f"{key}/robots.txt")
        cached = self._robots_cache.get(key)
        if cached is not None and monotonic() < cached.expires_at:
            if len(cached.checked_urls) - 1 > self.max_redirects:
                raise CrawlError(
                    CrawlErrorCode.POLICY_BLOCKED, "Cached robots redirect chain exceeds the limit"
                )
            for checked_url in cached.checked_urls:
                self.policy.check_url(checked_url)
            self._delays[url.host] = cached.delay
            return cached.rules
        # Evict BEFORE refresh. Errors and cancellation must never retain stale rules.
        self._robots_cache.pop(key, None)
        visited = set()
        checked_urls = []
        expires_at = float("inf")
        for _ in range(self.max_redirects + 1):
            if str(target) in visited:
                break
            visited.add(str(target))
            checked_urls.append(str(target))
            started = monotonic()
            page = await self._get(client, target, ROBOTS_LIMIT, robots=True)
            received = monotonic()
            expires_at = min(expires_at, _robots_expires_at(page.headers, started, received))
            if page.status in REDIRECTS:
                target = self._redirect(page)
                continue
            if page.status not in {200, 404}:
                raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "robots.txt could not be verified")
            if page.status == 404:
                rules = Protego.parse("")
                delay = 0
            else:
                try:
                    content = page.body.decode("utf-8-sig", errors="strict")
                except UnicodeError as error:
                    raise CrawlError(
                        CrawlErrorCode.POLICY_BLOCKED, "Invalid robots.txt encoding"
                    ) from error
                if content.lstrip().startswith("<"):
                    raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "robots.txt contains markup")
                rules = Protego.parse(content)
                delay = rules.crawl_delay(ROBOTS_AGENT) or 0
                rate = rules.request_rate(ROBOTS_AGENT)
                if rate:
                    if rate.start_time is not None or rate.end_time is not None:
                        raise CrawlError(
                            CrawlErrorCode.POLICY_BLOCKED, "Timed robots rates need review"
                        )
                    if rate.requests <= 0:
                        raise CrawlError(
                            CrawlErrorCode.POLICY_BLOCKED, "Invalid robots request rate"
                        )
                    delay = max(delay, rate.seconds / rate.requests)
                if not isfinite(delay) or delay < 0:
                    raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Invalid robots crawl delay")
            self._delays[url.host] = delay
            if monotonic() < expires_at:
                self._robots_cache[key] = _RobotsCacheEntry(
                    rules, expires_at, delay, tuple(checked_urls)
                )
            return rules
        raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "robots.txt redirect limit exceeded")

    async def _download(
        self,
        client: httpx.AsyncClient,
        request: CrawlRequest,
        *,
        media_types: frozenset[str] | None = None,
    ) -> _Page:
        target = self.policy.check_url(request.url)
        visited = set()
        for _ in range(self.max_redirects + 1):
            if str(target) in visited:
                break
            visited.add(str(target))
            rules = await self._robots(client, target)
            if not rules.can_fetch(str(target), ROBOTS_AGENT):
                raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "robots.txt disallows this URL")
            page = await self._get(client, target, request.max_html_bytes, media_types=media_types)
            if page.status not in REDIRECTS:
                return page
            target = self._redirect(page)
        raise CrawlError(CrawlErrorCode.NETWORK, "Page redirect limit exceeded")
