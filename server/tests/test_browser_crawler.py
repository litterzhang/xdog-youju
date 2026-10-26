"""Two focused browser-boundary tests; no real browser or external HTTP requests."""

import asyncio
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from youju.crawlers import (
    CrawlerMode,
    CrawlError,
    CrawlErrorCode,
    CrawlRequest,
    DynamicCrawler,
    HeadedCrawler,
    HtmlKind,
    OriginPolicy,
)
from youju.crawlers.browser import _ResourceBroker
from youju.crawlers.http import _Page

URL = "https://render.example/jobs"
RAW = b"<html><body><script src='/jobs.js'></script></body></html>"
DOM = "<!DOCTYPE html><html><body><p id='jobs'>招聘公告</p></body></html>"
POLICY = OriginPolicy(frozenset({"https://render.example"}))


def document():
    return _Page(httpx.URL(URL), 200, httpx.Headers({"content-type": "text/html"}), RAW)


class FakeCDP:
    def __init__(self):
        self.detached = False

    async def send(self, method, params=None):
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}
        if method == "Page.createIsolatedWorld":
            assert params["frameId"] == "main"
            return {"executionContextId": 7}
        assert method == "Runtime.evaluate"
        assert params["contextId"] == 7
        assert "TextEncoder" in params["expression"]
        return {"result": {"value": {"html": DOM, "url": URL}}}

    async def detach(self):
        self.detached = True


class FakeRoute:
    def __init__(self, url=URL, resource_type="document"):
        self.request = SimpleNamespace(
            url=url, resource_type=resource_type, method="GET", frame="main"
        )
        self.fulfilled = None
        self.aborted = False

    async def fulfill(self, **response):
        self.fulfilled = response

    async def abort(self):
        self.aborted = True


def test_both_browser_modes_capture_dom_through_an_isolated_cdp_world():
    async def run():
        for crawler_type, mode in (
            (DynamicCrawler, CrawlerMode.DYNAMIC),
            (HeadedCrawler, CrawlerMode.HEADED),
        ):
            cdp = FakeCDP()

            async def new_session(page, cdp=cdp):
                return cdp

            response = await crawler_type(POLICY)._snapshot(
                SimpleNamespace(new_cdp_session=new_session),
                object(),
                CrawlRequest(url=URL, mode=mode),
                monotonic() + 30,
                document(),
            )
            assert response.html == DOM
            assert response.html_kind is HtmlKind.RENDERED_DOM
            assert response.response_body == RAW
            assert response.http_status == 200
            assert cdp.detached

    asyncio.run(run())


def test_broker_fulfills_checked_html_and_propagates_blocked_resource_errors():
    async def run():
        request = CrawlRequest(url=URL, mode=CrawlerMode.DYNAMIC)
        broker = _ResourceBroker(
            DynamicCrawler(POLICY),
            None,
            request,
            document(),
            monotonic() + 30,
            8,
            4096,
        )
        broker.page = SimpleNamespace(main_frame="main")
        main = FakeRoute()
        await broker.handle(main)
        assert main.fulfilled["body"] == RAW
        assert not main.aborted

        forbidden = FakeRoute("http://127.0.0.1/private", "fetch")
        await broker.handle(forbidden)
        assert forbidden.aborted
        assert forbidden.fulfilled is None
        with pytest.raises(CrawlError) as error:
            broker.raise_failure()
        assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
        await broker.close()

    asyncio.run(run())
