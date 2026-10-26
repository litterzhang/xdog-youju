# HTML Crawler Contracts

## Current scope

`youju.crawlers` defines request/response models, three modes, typed errors, and
an asynchronous `Crawler` protocol. These are acquisition contracts, not ORM
entities. `StaticCrawler` implements bounded HTTP acquisition using HTTPX and
Protego. `DynamicCrawler` and `HeadedCrawler` share a Playwright/CDP controller
and the same HTTP access layer. The POC in `POC.md` adds explicit source
approval, async queue processing, deferred mode selection and capture storage.
Importing the crawler package performs no I/O; database work stays in the POC
layer, not in browser control.

## Three acquisition modes

| Mode | Adapter status | HTML semantics |
| --- | --- | --- |
| `static` | Implemented: `StaticCrawler`, bounded HTTP GET | Server response HTML; JavaScript is not executed. |
| `dynamic` | Implemented: `DynamicCrawler`, headless Chromium through CDP | Serialized main-frame DOM at capture time. |
| `headed` | Implemented: `HeadedCrawler`, visible Chromium through CDP | Serialized main-frame DOM at capture time. |

`headed` opens a real browser with a visible window; it is not a test double
and must never return synthetic content in production. Test doubles should
be named `FakeCrawler`. The two browser adapters share a CDP controller and
browser/session lifecycle code. Visible mode is not a promise of
human behavior or a way to bypass access controls.

Playwright launches an owned browser and creates a page CDP session. It does
not accept remote CDP endpoints or attach to a user's everyday browser. Each
capture uses a temporary profile/home and a new browser context. No existing
credentials are imported, and browser contexts are closed after capture.

## Static acquisition

After reviewing the target origin and access policy, run programmatically from
`server/` using `uv run python`. This performs real public HTTP requests:

```python
import asyncio
from youju.crawlers import CrawlRequest, OriginPolicy, StaticCrawler

async def main():
    # Trusted deployment configuration, never a client-supplied allowlist.
    policy = OriginPolicy(frozenset({"https://employment.ustc.edu.cn"}))
    crawler = StaticCrawler(policy)
    response = await crawler.crawl(CrawlRequest(
        url="https://employment.ustc.edu.cn/cn/indexnewslist.aspx?signtype=51",
    ))
    print(response.http_status, response.html_kind.value)

asyncio.run(main())
```

The adapter does not load, approve or update registry records. The separate
POC worker requires source/resource approval and constructs policy from the
registered source and organization scope. There is deliberately no public arbitrary-URL fetch endpoint.

Implemented behavior:

- Exact allowed origins, including scheme and port; no wildcard domains. Every
  navigation and robots redirect is checked. Credentials in URLs are rejected.
- All DNS answers must be public unicast addresses. A validated IP is pinned for
  the connection while preserving the original Host header and TLS SNI/certificate
  verification. There is no second unchecked hostname resolution for that request.
- No environment proxies, netrc credentials, connection reuse or cookie retention.
  `proxy_on` may be needed to install dependencies, but does not route this crawler.
  A trusted egress proxy would need a separately designed security policy.
- Fresh robots checks per origin per crawl, including allowed redirects. Protego
  handles allow/disallow patterns. A robots 404 means no published rules; all
  other non-200 final statuses and invalid robots responses stop the crawl.
- One active crawl per instance, with a one-second default per-host request
  interval. Recognized crawl-delay and request-rate directives may increase it;
  time-windowed request-rate rules require review. Independent instances do not
  share pacing; a future multi-worker scheduler must coordinate host budgets.
- The deadline includes queueing, DNS, robots, pacing, redirects and body reading.
  HTML defaults to a 2 MiB limit; robots is capped at 512 KiB. Redirects default
  to three per chain. Streaming body limits do not trust Content-Length alone.
- HTML/XHTML only. BOM, HTTP charset, an early meta charset or UTF-8 determine text
  decoding; invalid text fails instead of silently replacing characters. Original
  entity bytes are preserved. No JavaScript, images, frames or attachments load.
- Requests advertise `Accept-Encoding: identity`; compressed responses are rejected
  until a bounded decompression implementation is added. The selected public IP
  is not automatically retried with another address after a connection failure.

`http.py` contains the shared HTTP access implementation. Browser requests
use it through interception rather than delegating DNS or network access
directly to Chromium.

## Browser acquisition

Use `DynamicCrawler(policy)` with `CrawlerMode.DYNAMIC`, or
`HeadedCrawler(policy)` with `CrawlerMode.HEADED`. Both expose the same asynchronous
`crawl` method. The installed `chromium-browser`, `chromium`, or `google-chrome`
executable is discovered by default; `executable_path` is trusted server-side
configuration. Headed mode requires a working display and opens a visible window
only when explicitly selected.

```python
import asyncio
from youju.crawlers import CrawlerMode, CrawlRequest, DynamicCrawler, OriginPolicy

async def main():
    crawler = DynamicCrawler(
        OriginPolicy(frozenset({"https://employment.ustc.edu.cn"})),
    )
    response = await crawler.crawl(CrawlRequest(
        url="https://employment.ustc.edu.cn/cn/indexnewslist.aspx?signtype=51",
        mode=CrawlerMode.DYNAMIC,
        ready_selector='a[href*="indexnews.aspx?sign="]',
        timeout_seconds=45,
    ))
    print(response.html_kind.value, response.http_status)

asyncio.run(main())
```

This is a constrained main-document renderer, not a general-purpose logged-in
browser or a pixel-faithful screenshot service:

- The initial HTML and its HTTP redirects are fetched through the checked HTTP
  layer first. Navigation uses the resulting final URL without rewriting base
  URLs or substituting a different document under the original URL.
- Browser HTTP routes never call `continue_`. Main-frame GET scripts, styles,
  XHR and fetch resources are fulfilled using the same origin, DNS/IP, robots,
  pacing and size checks. Browser cookies/auth headers are not forwarded; server
  Set-Cookie headers are not copied into renderer responses.
- Non-GET data requests, WebSockets, redirected subresources and later document
  redirects fail explicitly. Images, media, fonts, unsupported resource types,
  child frames and popups are aborted. Service workers and downloads are disabled.
  Cross-origin assets require explicit approval in `OriginPolicy`.
- Unhandled browser traffic is pointed at a locally reserved, non-listening proxy,
  with the implicit loopback bypass disabled. Browser DNS resolution and direct
  WebRTC UDP/QUIC paths are restricted. The backend does not turn off Chromium's
  sandbox or TLS validation. These browser controls are defense in depth, not a
  replacement for OS/container network and memory isolation in deployment.
- A render defaults to 64 intercepted requests and 16 MiB of fulfilled entity
  bodies. Each body is also bounded by the request's byte limit. Robots and
  initial redirects have their own existing bounds. Compressed responses remain
  unsupported. There is no unbounded retry or browser fallback.
- Navigation waits for DOMContentLoaded, then for an optional `ready_selector`
  to be attached. It does not wait indefinitely for network-idle. CDP creates an
  isolated JavaScript world and serializes the current document plus doctype;
  page scripts cannot replace the capture world's builtins.
- UTF-8 DOM size is checked inside the browser before the HTML is returned through
  CDP. This is not a hard cap on the renderer's native/JavaScript heap; deployment
  still needs process memory limits for hostile or pathological pages.
- The request deadline covers the operation, including resource callbacks.
  Cleanup cancels outstanding broker tasks and closes only the owned browser,
  allowing up to five additional seconds for the close operation.
- Original main-response bytes are separate from rendered HTML. If client-side
  navigation changes the final URL without a matching observed HTTP document,
  status and original response bytes remain unknown rather than being invented.

A visible window does not provide manual-login, CAPTCHA-solving, or general
interactive automation. None of these adapters changes source approval or stores
recruitment records.

## Common interface

```python
from youju.crawlers import Crawler, CrawlerMode, CrawlRequest, CrawlResponse

async def acquire(crawler: Crawler) -> CrawlResponse:
    return await crawler.crawl(CrawlRequest(
        url="https://example.test/recruitment",
        mode=CrawlerMode.DYNAMIC,
        ready_selector=".job-list",
        timeout_seconds=30,
    ))
```

This illustrates the interface, not an executable crawl command. `ready_selector`
is an optional CSS selector for browser readiness, not executable code. Static
requests reject it. Mode belongs to the request and is persisted by the POC
queue/capture records. It is not a catalog-controlled source or seed approval
field. The USTC worker supplies a table/list readiness selector for dynamic retries.

All responses expose `html`, but their representation is explicit:

- `response_html`: HTML decoded from the HTTP entity body. Static captures also
  retain `response_body` bytes before text decoding and their actual HTTP status.
- `rendered_dom`: serialized DOM after browser navigation and the configured
  readiness condition. Original response bytes are optional and separate.

Browser adapters should serialize the current main-frame document, including its
doctype, rather than reuse the initial network response. Capture occurs at a
specific instant; there is no universal "finished" state for a dynamic page.
The snapshot does not imply that iframe documents, shadow trees, JavaScript heap
state, or canvas pixels are included. Those are outside the initial contract.

The response also records the request, final URL, HTTP status when observed, and
a timezone-aware capture time normalized to UTC. Missing browser status remains
`None`, never a fabricated 200. HTML from an HTTP error is still an observation,
not proof of successful retrieval; the orchestration layer must classify it.
Static 204/205/304 responses cannot produce a new HTML capture. Conditional
requests/cache revalidation need a storage-aware orchestration path; do not
fabricate HTML for them or describe a cached body as a fresh server response.
HTML/body fields are excluded from automatic dataclass representations.

Timeouts, rejected targets, unsupported content types, size limits, unavailable
browsers and transport failures should raise `CrawlError`, not return empty or
invented HTML. Response construction checks the size of UTF-8 HTML and available
entity bytes; adapters must additionally bound streaming, decompression, browser
resources and execution time before building a response.

## Pipeline and security boundaries

```text
Approved source + resource + server-side policy
    -> selected HTTP/CDP adapter
    -> CrawlResponse
    -> stored capture and FetchAttempt
    -> link discovery or announcement parsing
```

All modes require reviewed source scope. The static adapter enforces the
network checks described above; the POC worker now checks registry approval
before claiming jobs and supplies the reviewed origin policy. Browser network requests use the broker and fallback
blocking described above; checking only the initial navigation URL would be
insufficient. Contracts and URL syntax validation alone do not authorize
requests. Broad untrusted crawling still requires deployment-level isolation.

This interface is for HTML pages. PDF/Excel attachments and XML/JSON/feed/sitemap
entry points need their own bounded download/parsing path; do not wrap them in
HTML and pretend they are rendered pages. Do not automatically retry static
failures in a browser or silently launch visible windows.

The POC `Capture` table associates acquisition mode and HTML representation
with the fetch attempt and `ResourceVersion`, preserving original response
bytes separately. Do not mix raw response and DOM snapshots as if they were
identical evidence. Source versions, HTML parsing and recruitment matching stay
separate from browser control.

## Verification

`uv run pytest tests/test_crawler_contracts.py` runs two offline contract tests.
These do not validate real HTTP, CDP connections, headless rendering, or a visible
browser. `uv run pytest tests/test_static_crawler.py` adds three offline adapter
tests for Chinese HTML/redirect handling, blocked targets/robots rules, and
size/type/time failures. They mock transport, DNS and pacing, not real sockets.

A separate real HTTP check on October 11, 2026 fetched the selected USTC
recruitment list through the static adapter: HTTP 200, 103,465 entity bytes,
with recruitment links present. It did not follow those links, change source
approval, persist recruitment records or launch a browser. This is one
point-in-time check, not an availability or coverage guarantee.

The browser boundary adds two offline tests in `tests/test_browser_crawler.py`:
isolated-world DOM response handling for both modes, and broker refusal of a
forbidden resource. These use fake CDP objects, not a real browser.

Separate checks on October 11, 2026 used actual sandboxed headless Chromium:

- A synthetic page loaded JavaScript and JSON through the intercepted HTTP layer;
  CDP returned the resulting Chinese DOM, separate from the original HTML bytes.
- The selected USTC list returned HTTP 200 and a `rendered_dom` snapshot containing
  announcement links (102,513 UTF-8 bytes in that capture). No announcement details
  were followed, source approvals changed, or recruitment records persisted.

Headed mode has not been opened on the user's desktop for visual verification.
The headless checks do not establish headed usability or production isolation.

## Implementation references

- Playwright browser/CDP connection API: `https://playwright.dev/python/docs/api/class-browsertype`
- Playwright page content/readiness API: `https://playwright.dev/python/docs/api/class-page`

- HTTPX SNI/transport extensions: `https://www.python-httpx.org/advanced/extensions/`
- Protego robots parser: `https://github.com/scrapy/protego`

## Source-pipeline materials

`crawlers/material.py` adds a separate binary-capable `MaterialCrawler`, sharing
`HttpAccess` policy, robots, pinned DNS, pacing and redirect checks. Existing
`CrawlResponse` remains HTML-only. Its optional media-type transport argument does
not widen the existing static/browser adapters' defaults. `MaterialResponse`
preserves attachment bytes and MIME metadata; general parsing runs in a separate
bubblewrap process during acquisition. Original bytes and parsed evidence are
retained separately. See `ONBOARDING.md`.

For generic onboarding pages without a ready selector, the browser now waits for
a bounded two-second network-quiet condition after DOMContentLoaded. Timeout of
that optional wait still permits a snapshot; it is not a guarantee that all
application activity has finished. Explicit ready selectors retain their previous
behavior, and all browser network restrictions remain in place.
