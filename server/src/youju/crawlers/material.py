"""Bounded material acquisition; binary documents are never disguised as HTML."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .contracts import CrawlError, CrawlErrorCode, CrawlRequest
from .http import HTML_TYPES, HttpAccess

MATERIAL_TYPES = frozenset(HTML_TYPES) | {
    "application/pdf",
    "application/octet-stream",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/xml",
    "text/xml",
    "text/plain",
}


@dataclass(frozen=True)
class MaterialResponse:
    url: str
    final_url: str
    body: bytes
    media_type: str
    http_status: int
    captured_at: datetime
    headers: dict
    html_kind: str | None = None
    response_body: bytes | None = None


class MaterialCrawler(HttpAccess):
    async def fetch(self, url: str, *, timeout=45, max_bytes=16 * 1024 * 1024):
        request = CrawlRequest(url=url, timeout_seconds=timeout, max_html_bytes=max_bytes)
        try:
            async with asyncio.timeout(timeout), self._lock:
                async with self._client(timeout) as client:
                    page = await self._download(client, request, media_types=MATERIAL_TYPES)
                    return MaterialResponse(
                        url=url,
                        final_url=str(page.url),
                        body=page.body,
                        media_type=page.headers.get("content-type", "application/octet-stream"),
                        http_status=page.status,
                        captured_at=datetime.now(UTC),
                        headers={
                            key: page.headers[key]
                            for key in ("etag", "last-modified")
                            if key in page.headers
                        },
                        html_kind="response_html"
                        if page.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        in HTML_TYPES
                        else None,
                        response_body=page.body,
                    )
        except (TimeoutError, httpx.TimeoutException) as error:
            raise CrawlError(CrawlErrorCode.TIMEOUT, "Material download timed out") from error
        except (httpx.HTTPError, OSError) as error:
            raise CrawlError(CrawlErrorCode.NETWORK, "Material download failed") from error
