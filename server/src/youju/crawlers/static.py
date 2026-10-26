"""Bounded anonymous HTML acquisition with explicit origins, robots checks and pinned DNS."""

import asyncio
import codecs
import re
from datetime import UTC, datetime

import httpx

from .contracts import CrawlerMode, CrawlError, CrawlErrorCode, CrawlRequest, CrawlResponse
from .http import HttpAccess, _Page


def _decode_html(page: _Page) -> str:
    """Honor BOM, declared charset, then a meta charset; never silently replace broken text."""
    content_type = page.headers.get("content-type", "")
    match = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    if page.body.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif page.body.startswith((codecs.BOM_UTF32_BE, codecs.BOM_UTF32_LE)):
        encoding = "utf-32"
    elif page.body.startswith((codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)):
        encoding = "utf-16"
    elif match:
        encoding = match.group(1)
    else:
        head = page.body[:4096].decode("ascii", errors="ignore")
        meta = re.search(r"<meta\b[^>]*charset\s*=\s*[\"']?([\w.-]+)", head, re.I)
        encoding = meta.group(1) if meta else "utf-8"
    try:
        html = page.body.decode(encoding, errors="strict")
    except (LookupError, UnicodeError) as error:
        raise CrawlError(
            CrawlErrorCode.NOT_HTML, "HTML text could not be decoded safely"
        ) from error
    if not html.strip():
        raise CrawlError(CrawlErrorCode.NOT_HTML, "The response contains no HTML")
    return html


class StaticCrawler(HttpAccess):
    """Use one instance per worker; it serializes requests and retains per-host pacing."""

    async def crawl(self, request: CrawlRequest) -> CrawlResponse:
        if request.mode is not CrawlerMode.STATIC:
            raise ValueError("StaticCrawler only accepts static requests")
        try:
            async with asyncio.timeout(request.timeout_seconds), self._lock:
                async with self._client(request.timeout_seconds) as client:
                    page = await self._download(client, request)
                    modified = page.headers.get("last-modified")
                    if modified is not None and len(modified) > 256:
                        modified = None  # A malformed optional header cannot erase readable HTML.
                    return CrawlResponse(
                        request=request,
                        final_url=str(page.url),
                        html=_decode_html(page),
                        http_status=page.status,
                        response_body=page.body,
                        last_modified=modified,
                        captured_at=datetime.now(UTC),
                    )
        except (TimeoutError, httpx.TimeoutException) as error:
            raise CrawlError(
                CrawlErrorCode.TIMEOUT, "HTML acquisition exceeded its time limit"
            ) from error
        except (httpx.HTTPError, OSError) as error:
            raise CrawlError(CrawlErrorCode.NETWORK, "HTTP acquisition failed") from error
