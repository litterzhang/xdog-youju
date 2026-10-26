"""Bounded asynchronous collection; attachment parsing is part of acquisition."""

import asyncio
import hashlib
import re
from collections import Counter

import httpx

from youju.crawlers import CrawlerMode, CrawlError, CrawlErrorCode, CrawlRequest, DynamicCrawler
from youju.crawlers.material import MaterialCrawler, MaterialResponse
from youju.crawlers.policy import OriginPolicy, origin
from youju.models.base import normalize_url
from youju.poc.scope import DiscoveryScope

from .sandbox import Sandbox, SandboxError
from .store import approved_origins


def prepare_document(document, body, kind):
    """Apply shared acquisition guards without changing network observation metadata."""
    if (
        not isinstance(document, dict)
        or not {"format", "status", "blocks", "tables", "links"} <= document.keys()
    ):
        raise ValueError("Invalid material parser contract")
    links = []
    for link in document["links"]:
        try:
            links.append(dict(link, url=normalize_url(link["url"])))
        except (ValueError, UnicodeError):
            document["status"] = "partial"
            document.setdefault("errors", []).append(
                {
                    "code": "UNSAFE_LINK_SYNTAX",
                    "message": "Unsafe HTTP link omitted from the acquisition queue",
                }
            )
    document["links"] = links
    if kind == "attachment" and document["format"] == "html":
        document["status"] = "partial"
        document.setdefault("errors", []).append(
            {
                "code": "attachment_returned_html",
                "message": "Expected attachment returned an HTML page",
            }
        )
    # Loading placeholders with scripts are not useful static captures.
    text = " ".join(block.get("text", "") for block in document["blocks"])
    if (
        document["format"] == "html"
        and not document["tables"]
        and not any(
            link.get("kind", "page") in {"page", "attachment"} for link in document["links"]
        )
        and len(text) < 150
        and b"<script" in body.lower()
        and (not text.strip() or re.search(r"loading|加载|javascript|启用.*脚本", text, re.I))
    ):
        document["status"] = "failed"
        document.setdefault("errors", []).append(
            {"code": "static_shell", "message": "No useful rendered content"}
        )
    return document


def observe_http_date(document, modified):
    """Attach an actually observed header, never a capture or inferred browser clock."""
    if (
        document.get("format") == "html"
        and isinstance(modified, str)
        and modified.strip()
        and len(modified) <= 256
    ):
        hints = document.get("date_hints", [])
        hints = hints if isinstance(hints, list) else []
        document["date_hints"] = [
            *hints[:63],
            {"kind": "http_last_modified", "value": modified, "locator": "http:last-modified"},
        ]
    return document


class AcquisitionWorker:
    def __init__(
        self,
        store,
        *,
        sandbox=None,
        concurrency=2,
        timeout=45,
        _material_crawler=None,
        _dynamic_crawler=None,
    ):
        if not 1 <= concurrency <= 8 or not 1 <= timeout <= 180:
            raise ValueError("Invalid acquisition concurrency or timeout")
        self.store, self.sandbox = store, sandbox or Sandbox()
        self.concurrency, self.timeout = concurrency, timeout
        self._material = _material_crawler
        self._dynamic = _dynamic_crawler
        self._adapters = {}

    def _adapter(self, claim, mode):
        if mode == "static" and self._material is not None:
            return self._material
        if mode == "dynamic" and self._dynamic is not None:
            return self._dynamic
        if claim.source_id not in self._adapters:
            page_policy = OriginPolicy(approved_origins(claim.approved_origin, claim.config))
            attachment_policy = OriginPolicy(
                approved_origins(claim.approved_origin, claim.config, attachments=True)
            )
            material, dynamic = MaterialCrawler(page_policy), DynamicCrawler(page_policy)
            attachment = MaterialCrawler(attachment_policy)
            for adapter in (dynamic, attachment):
                adapter._lock = material._lock
                adapter._last_request = material._last_request
                adapter._delays = material._delays
                adapter._robots_cache = material._robots_cache
            self._adapters[claim.source_id] = {
                "static": material,
                "dynamic": dynamic,
                "attachment": attachment,
            }
        if mode == "static" and claim.kind == "attachment":
            return self._adapters[claim.source_id]["attachment"]
        return self._adapters[claim.source_id][mode]

    async def _fetch(self, claim):
        if claim.mode == "static":
            return await self._adapter(claim, "static").fetch(
                claim.url, timeout=self.timeout, max_bytes=claim.config["max_bytes"]
            )
        response = await self._adapter(claim, "dynamic").crawl(
            CrawlRequest(
                url=claim.url,
                mode=CrawlerMode.DYNAMIC,
                timeout_seconds=self.timeout,
                max_html_bytes=min(2 * 1024 * 1024, claim.config["max_bytes"]),
            )
        )
        return MaterialResponse(
            url=claim.url,
            final_url=response.final_url,
            body=response.html.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            http_status=response.http_status or 0,
            captured_at=response.captured_at,
            headers={"last-modified": response.last_modified} if response.last_modified else {},
            html_kind="rendered_dom",
            response_body=response.response_body,
        )

    async def _process(self, claim):
        try:
            response = await self._fetch(claim)
            if (
                response.url != claim.url
                or not DiscoveryScope(claim.root_domain).contains(response.final_url)
                or origin(httpx.URL(response.final_url))
                not in approved_origins(
                    claim.approved_origin, claim.config, attachments=claim.kind == "attachment"
                )
            ):
                raise CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Material escaped approved scope")
            if len(response.body) > claim.config["max_bytes"]:
                raise CrawlError(CrawlErrorCode.SIZE_LIMIT, "Material exceeds byte limit")
            if not 200 <= response.http_status < 300:
                return await asyncio.to_thread(
                    self.store.fail,
                    claim,
                    f"http_{response.http_status}",
                    http_status=response.http_status or None,
                    final_url=response.final_url,
                )
            try:
                document = await self.sandbox.parse(
                    response.body,
                    media_type=response.media_type,
                    url=response.final_url,
                    material_key=f"{claim.resource_id}:{hashlib.sha256(response.body).hexdigest()}",
                )
            except (SandboxError, TimeoutError) as error:
                document = {
                    "format": "unknown",
                    "status": "failed",
                    "title": "",
                    "blocks": [],
                    "tables": [],
                    "links": [],
                    "truncated": False,
                    "errors": [{"code": "material_parser_failed", "message": str(error)[:1000]}],
                }
            document = prepare_document(document, response.body, claim.kind)
            observe_http_date(document, response.headers.get("last-modified"))
            return await asyncio.to_thread(self.store.save, claim, response, document)
        except CrawlError as error:
            return await asyncio.to_thread(
                self.store.fail,
                claim,
                error.code.value,
                terminal=error.code == CrawlErrorCode.POLICY_BLOCKED,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.to_thread(self.store.fail, claim, "internal_error", terminal=True)
            raise

    async def run_once(self, source_id, limit=50):
        if not 1 <= limit <= 500:
            raise ValueError("Acquisition pass limit must be between 1 and 500")
        seen, counts = set(), Counter()
        while len(seen) < limit:
            batch = []
            for _ in range(min(self.concurrency, limit - len(seen))):
                claim = await asyncio.to_thread(
                    self.store.claim, source_id, seen.copy(), self.timeout
                )
                if claim is None:
                    break
                batch.append(claim)
                seen.add(claim.resource_id)
                counts[claim.mode] += 1
            if not batch:
                break
            for state in await asyncio.gather(*(self._process(claim) for claim in batch)):
                counts[state] += 1
        return {"processed": len(seen), **counts}
