"""Async bounded worker; no network or HTML parsing occurs inside a SQLite transaction."""

import asyncio
from collections import Counter
from functools import partial
from math import isfinite

import httpx

from youju.crawlers import (
    CrawlerMode,
    CrawlError,
    CrawlErrorCode,
    CrawlRequest,
    DynamicCrawler,
    OriginPolicy,
    StaticCrawler,
)
from youju.crawlers.policy import origin

from .ledger import Ledger
from .parsing import ParsedPage, page_role, parse_html
from .storage import BlobStore


class CrawlWorker:
    def __init__(
        self, engine, storage, *, retry_delay=60, timeout=45, concurrency=2, _crawlers=None
    ):
        if (
            not isfinite(retry_delay)
            or not isfinite(timeout)
            or retry_delay < 0
            or timeout <= 0
            or not 1 <= concurrency <= 8
        ):
            raise ValueError("Invalid worker retry, timeout or concurrency limits")
        self.ledger = Ledger(engine)
        self.store = BlobStore(storage)
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.concurrency = concurrency
        self._overrides = _crawlers
        self._adapters = {}

    def _adapter(self, claim):
        if self._overrides is not None:
            return self._overrides[claim.mode]
        if claim.source_id not in self._adapters:
            policy = OriginPolicy(frozenset({claim.approved_origin}))
            static, dynamic = StaticCrawler(policy), DynamicCrawler(policy)
            # Both adapters use HttpAccess; switching mode must retain its host pacing and lock.
            dynamic._lock = static._lock
            dynamic._last_request = static._last_request
            dynamic._delays = static._delays
            dynamic._robots_cache = static._robots_cache
            self._adapters[claim.source_id] = {"static": static, "dynamic": dynamic}
        return self._adapters[claim.source_id][claim.mode]

    def _prepare(self, claim, response):
        if (
            not claim.scope.contains(response.final_url)
            or origin(httpx.URL(response.final_url)) != claim.approved_origin
        ):
            raise CrawlError(
                CrawlErrorCode.POLICY_BLOCKED, "Final URL escaped the organization domain"
            )
        if response.request.url != claim.url or response.request.mode.value != claim.mode:
            raise ValueError("Crawler response does not match its claimed request")
        snapshot = self.store.put(response.html.encode("utf-8"))
        original = (
            self.store.put(response.response_body) if response.response_body is not None else None
        )
        return snapshot, original

    async def _process(self, claim):
        try:
            response = await self._adapter(claim).crawl(
                CrawlRequest(
                    url=claim.url,
                    mode=CrawlerMode(claim.mode),
                    timeout_seconds=self.timeout,
                    ready_selector=(
                        "#oInfoContent table"
                        if page_role(claim.url) == "notice"
                        else '#oInfoContent a[href*="indexnews.aspx"]'
                    )
                    if claim.mode == "dynamic"
                    else None,
                )
            )
            snapshot, original = await asyncio.to_thread(self._prepare, claim, response)
            parsed, parse_error = None, None
            if response.http_status is not None and 200 <= response.http_status < 300:
                try:
                    parsed = await asyncio.to_thread(
                        parse_html,
                        response.html,
                        response.final_url,
                        claim.scope,
                    )
                except Exception as error:
                    parse_error = error
                    parsed = ParsedPage(
                        "review",
                        page_role(response.final_url),
                        None,
                        explanation=f"parser_exception:{type(error).__name__}",
                    )
            result = await asyncio.to_thread(
                self.ledger.complete,
                claim,
                response,
                snapshot,
                original,
                parsed,
                self.retry_delay,
            )
            if parse_error is not None:
                raise parse_error  # Preserve the capture, but surface unexpected parser bugs.
            return result
        except CrawlError as error:
            return await asyncio.to_thread(
                partial(
                    self.ledger.fail,
                    claim,
                    error.code.value,
                    self.retry_delay,
                    terminal=error.code is CrawlErrorCode.POLICY_BLOCKED,
                ),
            )
        except asyncio.CancelledError:
            raise  # The persisted lease is recoverable by a later worker; do not invent an outcome.
        except Exception:
            await asyncio.to_thread(
                partial(self.ledger.fail, claim, "internal_error", self.retry_delay, terminal=True),
            )
            raise

    async def run_once(self, source_id: str, limit: int = 6) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        seen = set()
        counts = Counter()
        while len(seen) < limit:
            batch = []
            for _ in range(min(self.concurrency, limit - len(seen))):
                claim = await asyncio.to_thread(
                    self.ledger.claim, source_id, seen.copy(), self.timeout
                )
                if claim is None:
                    break
                seen.add(claim.resource_id)
                counts[claim.mode] += 1
                batch.append(claim)
            if not batch:
                break
            for result in await asyncio.gather(*(self._process(claim) for claim in batch)):
                counts[result["state"]] += 1
                counts["positions_created"] += result["positions"]
        # A resource is never retried in the same pass, even if retry_delay=0.
        return {"processed": len(seen), **dict(counts)}
