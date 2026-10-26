"""Shared CDP capture with dedicated browsers and intercepted, policy-checked HTTP resources."""

import asyncio
import os
import shutil
import socket
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import httpx
from playwright.async_api import Error as BrowserError
from playwright.async_api import TimeoutError as BrowserTimeout
from playwright.async_api import async_playwright

from .contracts import CrawlerMode, CrawlError, CrawlErrorCode, CrawlRequest, CrawlResponse
from .http import HTML_TYPES, REDIRECTS, ROBOTS_AGENT, USER_AGENT, HttpAccess
from .policy import OriginPolicy, origin

ASSET_TYPES = frozenset(
    HTML_TYPES
    | {
        "application/javascript",
        "text/javascript",
        "application/x-javascript",
        "text/css",
        "application/json",
        "text/plain",
        "application/xml",
        "text/xml",
    }
)
RESOURCE_TYPES = {"document", "script", "stylesheet", "xhr", "fetch"}
DROP_HEADERS = {
    "connection",
    "content-length",
    "transfer-encoding",
    "content-encoding",
    "set-cookie",
}


def _browser_environment(home: str) -> dict[str, str]:
    # Do not pass application credentials or proxy variables into the browser process.
    env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
            "LANG",
            "LC_ALL",
            "TZ",
        )
        if name in os.environ
    }
    env.update(HOME=home, XDG_CONFIG_HOME=f"{home}/config", XDG_CACHE_HOME=f"{home}/cache")
    return env


class _ResourceBroker:
    """Fulfill renderer requests from pinned HTTP; never let a route continue to the network."""

    def __init__(self, access, client, request, initial, deadline, max_requests, max_bytes):
        self.access = access
        self.client = client
        self.request = request
        self.initial = initial
        self.deadline = deadline
        self.max_requests = max_requests
        self.max_bytes = max_bytes
        self.page = None
        self.failure = None
        self.main_document = initial
        self.count = 0
        self.byte_count = 0
        self.initial_used = False
        self.rules = {}
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.closed = False

    async def handle(self, route):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            if self.closed:
                with suppress(BrowserError):
                    await route.abort()
                return
            async with asyncio.timeout(max(0, self.deadline - monotonic())):
                self.count += 1
                if self.count > self.max_requests:
                    raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "Browser request budget exceeded")
                request = route.request
                if request.resource_type not in RESOURCE_TYPES:
                    await route.abort()
                    return
                if request.frame != self.page.main_frame:
                    await (
                        route.abort()
                    )  # Child frames and popups are outside main-document capture.
                    return
                if request.method != "GET":
                    raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Browser requests must use GET")
                url = self.access.policy.check_url(request.url)
                async with self.lock:
                    if self.failure:
                        await route.abort()
                        return
                    if (
                        request.resource_type == "document"
                        and not self.initial_used
                        and url == self.initial.url
                    ):
                        document = self.initial
                        self.initial_used = True
                    else:
                        key = origin(url)
                        if key not in self.rules:
                            self.rules[key] = await self.access._robots(self.client, url)
                        if not self.rules[key].can_fetch(str(url), ROBOTS_AGENT):
                            raise CrawlError(
                                CrawlErrorCode.POLICY_BLOCKED, "robots.txt disallows a resource"
                            )
                        limit = min(self.request.max_html_bytes, self.max_bytes - self.byte_count)
                        if limit <= 0:
                            raise CrawlError(
                                CrawlErrorCode.SIZE_LIMIT, "Browser byte budget exceeded"
                            )
                        document = await self.access._get(
                            self.client,
                            url,
                            limit,
                            media_types=None
                            if request.resource_type == "document"
                            else ASSET_TYPES,
                        )
                        if document.status in REDIRECTS:
                            # Never substitute redirected bytes under the wrong document/module URL.
                            raise CrawlError(
                                CrawlErrorCode.POLICY_BLOCKED,
                                "Redirected browser resources require separate review",
                            )
                    self.byte_count += len(document.body)
                    if self.byte_count > self.max_bytes:
                        raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "Browser byte budget exceeded")
                    if request.resource_type == "document":
                        self.main_document = document
                    await route.fulfill(
                        status=document.status,
                        headers={
                            k: v for k, v in document.headers.items() if k not in DROP_HEADERS
                        },
                        body=document.body,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Propagate callback failures explicitly; they cannot raise directly into page.goto.
            if self.failure is None:
                self.failure = error
            with suppress(BrowserError):
                await route.abort()
        finally:
            self.tasks.discard(task)

    async def block_websocket(self, websocket):
        if self.failure is None:
            self.failure = CrawlError(CrawlErrorCode.POLICY_BLOCKED, "WebSockets are not supported")
        with suppress(BrowserError):
            await websocket.close()

    def raise_failure(self):
        if self.failure is not None:
            raise self.failure

    async def close(self):
        self.closed = True
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class _BrowserCrawler(HttpAccess):
    mode: CrawlerMode

    def __init__(
        self,
        policy: OriginPolicy,
        *,
        executable_path: str | Path | None = None,
        min_interval_seconds: float = 1,
        max_redirects: int = 3,
        max_requests: int = 64,
        max_total_bytes: int = 16 * 1024 * 1024,
        _transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(
            policy,
            min_interval_seconds=min_interval_seconds,
            max_redirects=max_redirects,
            _transport=_transport,
        )
        if type(max_requests) is not int or max_requests <= 0:
            raise ValueError("max_requests must be a positive integer")
        if type(max_total_bytes) is not int or max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be a positive integer")
        self.executable_path = str(executable_path) if executable_path is not None else None
        self.max_requests = max_requests
        self.max_total_bytes = max_total_bytes

    def _executable(self) -> str:
        executable = self.executable_path or next(
            (
                found
                for name in (
                    "chromium-browser",
                    "chromium",
                    "google-chrome",
                )
                if (found := shutil.which(name))
            ),
            None,
        )
        if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise CrawlError(
                CrawlErrorCode.BROWSER_UNAVAILABLE, "Configure an installed Chromium executable"
            )
        if self.mode is CrawlerMode.HEADED and sys.platform.startswith("linux"):
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise CrawlError(
                    CrawlErrorCode.BROWSER_UNAVAILABLE, "Headed mode requires a display"
                )
        return str(Path(executable).resolve())

    async def crawl(self, request: CrawlRequest) -> CrawlResponse:
        if request.mode is not self.mode:
            raise ValueError(f"{type(self).__name__} requires {self.mode.value} mode")
        executable = self._executable()
        deadline = monotonic() + request.timeout_seconds
        try:
            async with asyncio.timeout(request.timeout_seconds), self._lock:
                async with self._client(request.timeout_seconds) as client:
                    # Resolve redirects first to preserve the document URL and relative-link base.
                    initial = await self._download(client, request)
                    if len(initial.body) > self.max_total_bytes:
                        raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "Browser byte budget exceeded")
                    return await self._render(client, request, initial, deadline, executable)
        except (TimeoutError, BrowserTimeout, httpx.TimeoutException) as error:
            raise CrawlError(
                CrawlErrorCode.TIMEOUT, "Browser capture exceeded its time limit"
            ) from error
        except (BrowserError, httpx.HTTPError, OSError) as error:
            raise CrawlError(CrawlErrorCode.NETWORK, "Browser acquisition failed") from error

    async def _render(self, client, request, initial, deadline, executable):
        # Keep an unlistened port reserved: un-intercepted browser traffic must fail, not go direct.
        with socket.socket() as sink, TemporaryDirectory(prefix="youju-browser-") as home:
            sink.bind(("127.0.0.1", 0))
            proxy = f"http://127.0.0.1:{sink.getsockname()[1]}"
            async with async_playwright() as playwright:
                try:
                    browser = await playwright.chromium.launch(
                        executable_path=executable,
                        headless=self.mode.headless,
                        chromium_sandbox=True,
                        env=_browser_environment(home),
                        proxy={"server": proxy, "bypass": "<-loopback>"},
                        args=[
                            "--host-resolver-rules=MAP * ~NOTFOUND",
                            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                            "--disable-quic",
                        ],
                        timeout=max(1, int((deadline - monotonic()) * 1000)),
                    )
                except BrowserTimeout:
                    raise
                except BrowserError as error:
                    raise CrawlError(
                        CrawlErrorCode.BROWSER_UNAVAILABLE, "Chromium could not start safely"
                    ) from error
                broker = _ResourceBroker(
                    self,
                    client,
                    request,
                    initial,
                    deadline,
                    self.max_requests,
                    self.max_total_bytes,
                )
                try:
                    context = await browser.new_context(
                        service_workers="block",
                        accept_downloads=False,
                        user_agent=USER_AGENT,
                        permissions=[],
                    )
                    await context.route("**/*", broker.handle)
                    await context.route_web_socket("**/*", broker.block_websocket)
                    page = await context.new_page()
                    broker.page = page
                    try:
                        await page.goto(str(initial.url), wait_until="domcontentloaded")
                        if request.ready_selector is not None:
                            await page.wait_for_selector(request.ready_selector, state="attached")
                        else:
                            # Generic onboarding lacks a source selector until authoring.
                            # Bound the quiet-window wait; polling sites must not hang forever.
                            with suppress(BrowserTimeout):
                                await page.wait_for_load_state("networkidle", timeout=2000)
                        broker.raise_failure()
                        result = await self._snapshot(
                            context, page, request, deadline, broker.main_document
                        )
                        broker.raise_failure()
                        return result
                    except BrowserError:
                        broker.raise_failure()
                        raise
                finally:
                    await broker.close()
                    # Close only the browser owned by this invocation.
                    await asyncio.wait_for(browser.close(), timeout=5)

    async def _snapshot(self, context, page, request, deadline, main_document):
        cdp = await context.new_cdp_session(page)
        try:
            tree = await cdp.send("Page.getFrameTree")
            world = await cdp.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": tree["frameTree"]["frame"]["id"],
                    "worldName": "youju-capture",
                },
            )
            # Isolated-world builtins cannot be replaced by scripts from the untrusted page.
            result = await cdp.send(
                "Runtime.evaluate",
                {
                    "contextId": world["executionContextId"],
                    "returnByValue": True,
                    "timeout": max(1, int((deadline - monotonic()) * 1000)),
                    "expression": """(() => {
                    if (!document.documentElement) return {missing: true};
                    const doctype = document.doctype
                        ? new XMLSerializer().serializeToString(document.doctype) : '';
                    const html = doctype + document.documentElement.outerHTML;
                    if (new TextEncoder().encode(html).length > MAX_BYTES) return {tooLarge: true};
                    return {html, url: location.href};
                })()""".replace("MAX_BYTES", str(request.max_html_bytes)),
                },
            )
            if "exceptionDetails" in result:
                raise CrawlError(CrawlErrorCode.NOT_HTML, "DOM serialization failed")
            value = result.get("result", {}).get("value", {})
            if not isinstance(value, dict):
                raise CrawlError(CrawlErrorCode.NOT_HTML, "Invalid DOM capture result")
            if value.get("tooLarge"):
                raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "Rendered HTML exceeds the size limit")
            if not isinstance(value.get("html"), str) or not value["html"].strip():
                raise CrawlError(
                    CrawlErrorCode.NOT_HTML, "The browser did not return an HTML document"
                )
            final_url = self.policy.check_url(value["url"])
            matches = final_url == main_document.url
            modified = main_document.headers.get("last-modified") if matches else None
            if modified is not None and len(modified) > 256:
                modified = None
            return CrawlResponse(
                last_modified=modified,
                request=request,
                final_url=str(final_url),
                html=value["html"],
                http_status=main_document.status if matches else None,
                response_body=main_document.body if matches else None,
                captured_at=datetime.now(UTC),
            )
        finally:
            with suppress(BrowserError):
                await cdp.detach()


class DynamicCrawler(_BrowserCrawler):
    """Capture rendered main-frame HTML in a dedicated headless Chromium browser."""

    mode = CrawlerMode.DYNAMIC


class HeadedCrawler(_BrowserCrawler):
    """Capture rendered main-frame HTML in a dedicated visible Chromium browser."""

    mode = CrawlerMode.HEADED
