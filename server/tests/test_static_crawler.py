"""Offline adapter checks; mocked DNS/transport never connect to external sites."""

import asyncio
import socket
from datetime import UTC, datetime
from email.utils import formatdate

import httpx
import pytest

import youju.crawlers.http as crawler_http
from youju.crawlers import (
    CrawlError,
    CrawlErrorCode,
    CrawlRequest,
    HtmlKind,
    OriginPolicy,
    StaticCrawler,
)

ORIGIN = "https://recruitment.example"
PUBLIC_IP = "93.184.216.34"


def reply(body=b"", *, status=200, media_type="text/html; charset=utf-8", **headers):
    return httpx.Response(
        status,
        headers={"content-type": media_type, **headers},
        stream=httpx.ByteStream(body),
    )


@pytest.fixture
def network(monkeypatch):
    addresses = [PUBLIC_IP]
    observed = []

    def resolve(host, port, *args, **kwargs):
        assert host == "recruitment.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]

    async def no_wait(self, url):
        pass  # Pacing is not exercised by these transport tests.

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(StaticCrawler, "_wait_for_slot", no_wait)

    def make(handler):
        async def observe(request):
            observed.append(request)
            assert request.url.host == PUBLIC_IP
            assert request.headers["host"] == "recruitment.example"
            assert request.extensions["sni_hostname"] == "recruitment.example"
            return await handler(request)

        return StaticCrawler(
            OriginPolicy(frozenset({ORIGIN})),
            _transport=httpx.MockTransport(observe),
        )

    return make, addresses, observed


def test_static_html_preserves_chinese_and_checks_each_navigation(network):
    make, _, observed = network
    html = "<!doctype html><html><body>中国科大招聘公告</body></html>"
    body = html.encode("gb18030")

    async def handler(request):
        if request.url.path == "/robots.txt":
            return reply(b"User-agent: *\nAllow: /\n", media_type="text/plain")
        if request.url.path == "/start":
            return reply(status=302, location="/jobs?sign=1")
        return reply(body, media_type="text/html; charset=gb18030")

    response = asyncio.run(make(handler).crawl(CrawlRequest(url=ORIGIN + "/start")))
    assert response.html == html
    assert response.response_body == body
    assert response.html_kind is HtmlKind.RESPONSE_HTML
    assert response.http_status == 200
    assert response.final_url == ORIGIN + "/jobs?sign=1"
    assert response.captured_at.tzinfo is UTC
    assert [request.url.path for request in observed] == ["/robots.txt", "/start", "/jobs"]


def test_private_dns_robots_denial_and_out_of_scope_redirects_are_blocked(network):
    make, addresses, observed = network
    robots = b"User-agent: *\nDisallow: /private/*\n"

    async def handler(request):
        if request.url.path == "/robots.txt":
            return reply(robots, media_type="text/plain")
        return reply(status=302, location="http://127.0.0.1/private")

    # Even a mixed public/private answer is rejected before issuing an HTTP request.
    addresses.append("127.0.0.1")
    with pytest.raises(CrawlError) as error:
        asyncio.run(make(handler).crawl(CrawlRequest(url=ORIGIN + "/jobs")))
    assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
    assert observed == []
    addresses.pop()

    with pytest.raises(CrawlError) as error:
        asyncio.run(make(handler).crawl(CrawlRequest(url=ORIGIN + "/private/notice")))
    assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
    assert [request.url.path for request in observed] == ["/robots.txt"]

    with pytest.raises(CrawlError) as error:
        asyncio.run(make(handler).crawl(CrawlRequest(url=ORIGIN + "/jobs")))
    assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
    assert observed[-1].url.path == "/jobs"  # The forbidden redirect was never requested.


def test_non_html_oversized_streams_and_deadline_fail_explicitly(network):
    make, _, _ = network
    for case, expected in (
        ("pdf", CrawlErrorCode.NOT_HTML),
        ("large", CrawlErrorCode.SIZE_LIMIT),
        ("slow", CrawlErrorCode.TIMEOUT),
    ):

        async def handler(request, case=case):
            if request.url.path == "/robots.txt":
                return reply(status=404)
            if case == "pdf":
                return reply(b"%PDF", media_type="application/pdf")
            if case == "slow":
                await asyncio.sleep(0.1)
            return reply(b"<html>" + b"x" * 64 + b"</html>")

        with pytest.raises(CrawlError) as error:
            asyncio.run(
                make(handler).crawl(
                    CrawlRequest(
                        url=ORIGIN + "/jobs",
                        max_html_bytes=32,
                        timeout_seconds=0.01 if case == "slow" else 30,
                    )
                )
            )
        assert error.value.code is expected


def test_robots_cache_freshness_shared_safety_and_pacing(network, monkeypatch):
    make, addresses, observed = network
    clock = [1000.0]
    wall = datetime(2026, 10, 11, tzinfo=UTC).timestamp()
    monkeypatch.setattr(crawler_http, "monotonic", lambda: clock[0])
    monkeypatch.setattr(crawler_http, "time", lambda: wall + clock[0] - 1000)
    state = {"status": 200, "headers": {}}
    rules = b"User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n"
    robots_requests = []

    async def handler(request):
        if request.url.path == "/robots.txt":
            robots_requests.append(request)
            return reply(rules, status=state["status"], media_type="text/plain", **state["headers"])
        return reply(b"<html>jobs</html>")

    async def scenario():
        crawler = make(handler)
        await crawler.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
        entry = crawler._robots_cache[ORIGIN]
        assert entry.expires_at == clock[0] + 300
        assert entry.delay == 2
        assert entry.checked_urls == (ORIGIN + "/robots.txt",)
        await crawler.crawl(CrawlRequest(url=ORIGIN + "/another"))
        assert crawler._robots_cache[ORIGIN] is entry
        assert len(robots_requests) == 1
        with pytest.raises(CrawlError) as error:
            await crawler.crawl(CrawlRequest(url=ORIGIN + "/private/notice"))
        assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
        assert observed[-1].url.path == "/another"

        # Adapters can share cache and pacing state; every actual page still pins DNS.
        shared = make(handler)
        for name in ("_lock", "_last_request", "_delays", "_robots_cache"):
            setattr(shared, name, getattr(crawler, name))
        await shared.crawl(CrawlRequest(url=ORIGIN + "/shared"))
        assert len(robots_requests) == 1
        before = len(observed)
        addresses.append("127.0.0.1")
        with pytest.raises(CrawlError) as error:
            await shared.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
        assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
        assert len(observed) == before
        addresses.pop()

        # At the exact deadline refresh is mandatory, and failure removes old rules.
        clock[0] = entry.expires_at
        state["status"] = 503
        for _ in range(2):
            with pytest.raises(CrawlError) as error:
                await crawler.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
            assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
            assert ORIGIN not in crawler._robots_cache
            assert observed[-1].url.path == "/robots.txt"
        assert len(robots_requests) == 3
        state["status"] = 404
        await crawler.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
        await crawler.crawl(CrawlRequest(url=ORIGIN + "/private/now-allowed"))
        assert len(robots_requests) == 4
        assert crawler._robots_cache[ORIGIN].delay == 0
        clock[0] = crawler._robots_cache[ORIGIN].expires_at
        state["status"] = 200
        with pytest.raises(CrawlError) as error:
            await crawler.crawl(CrawlRequest(url=ORIGIN + "/private/no-longer-allowed"))
        assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
        assert observed[-1].url.path == "/robots.txt"
        assert len(robots_requests) == 5

        # Both verified 200 and 404 obey HTTP cache directives and upstream age.
        for status in (200, 404):
            for headers, ttl in (
                ({}, 300),
                ({"age": "20"}, 280),
                ({"cache-control": "public, No-Store"}, 0),
                ({"cache-control": 'no-cache="Set-Cookie, Date"'}, 0),
                ({"cache-control": "max-age=0"}, 0),
                ({"cache-control": 'max-age="30"', "age": "10"}, 20),
                ({"cache-control": "max-age=30", "age": "30"}, 0),
                ({"cache-control": "max-age=1000", "age": "500"}, 300),
                ({"cache-control": "max-age=1000", "age": "950"}, 50),
                ({"cache-control": "max-age=30", "age": "-1"}, 0),
                ({"cache-control": "max-age=5, max-age=60"}, 0),
                ({"cache-control": "max-age=invalid"}, 0),
                (
                    {
                        "cache-control": "max-age=60",
                        "age": "5",
                        "date": formatdate(crawler_http.time() - 20, usegmt=True),
                    },
                    40,
                ),
            ):
                state.update(status=status, headers=headers)
                access = make(handler)
                count = len(robots_requests)
                await access.crawl(CrawlRequest(url=ORIGIN + "/one"))
                if ttl:
                    assert access._robots_cache[ORIGIN].expires_at == clock[0] + ttl
                else:
                    assert ORIGIN not in access._robots_cache
                await access.crawl(CrawlRequest(url=ORIGIN + "/two"))
                assert len(robots_requests) - count == (1 if ttl else 2)

        # Redirect cache controls constrain the verified final rules as well.
        redirect_headers = {"cache-control": "no-store"}
        redirect_paths = []

        async def redirected(request):
            redirect_paths.append(request.url.path)
            if request.url.path == "/robots.txt":
                return reply(status=302, location="/robot-rules", **redirect_headers)
            if request.url.path == "/robot-rules":
                return reply(rules, media_type="text/plain", **{"cache-control": "max-age=90"})
            return reply(b"<html>jobs</html>")

        redirected_access = make(redirected)
        await redirected_access.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
        assert ORIGIN not in redirected_access._robots_cache
        redirect_headers["cache-control"] = "max-age=30"
        await redirected_access.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
        redirected_entry = redirected_access._robots_cache[ORIGIN]
        assert redirected_entry.expires_at == clock[0] + 30
        assert redirected_entry.checked_urls == (ORIGIN + "/robots.txt", ORIGIN + "/robot-rules")
        await redirected_access.crawl(CrawlRequest(url=ORIGIN + "/another"))
        assert redirect_paths.count("/robots.txt") == 2
        assert redirect_paths.count("/robot-rules") == 2
        redirected_access.max_redirects = 0
        before = len(redirect_paths)
        with pytest.raises(CrawlError) as error:
            await redirected_access.crawl(CrawlRequest(url=ORIGIN + "/jobs"))
        assert error.value.code is CrawlErrorCode.POLICY_BLOCKED
        assert len(redirect_paths) == before

        # Restore the real pacing method on these instances; advance a fake clock
        # rather than waiting or opening sockets. Cache hits do not skip page pacing.
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)
            clock[0] += delay

        monkeypatch.setattr(crawler_http.asyncio, "sleep", fake_sleep)
        for delay in (0, 2):
            paced_paths = []

            async def paced_handler(request, delay=delay, paced_paths=paced_paths):
                paced_paths.append((request.url.path, clock[0]))
                if request.url.path == "/robots.txt":
                    body = f"User-agent: *\nCrawl-delay: {delay}\n".encode()
                    return reply(body, media_type="text/plain")
                return reply(b"<html>jobs</html>")

            paced = make(paced_handler)
            paced._wait_for_slot = crawler_http.HttpAccess._wait_for_slot.__get__(paced)
            sleeps.clear()
            await paced.crawl(CrawlRequest(url=ORIGIN + "/one"))
            await paced.crawl(CrawlRequest(url=ORIGIN + "/two"))
            assert [path for path, _ in paced_paths] == ["/robots.txt", "/one", "/two"]
            assert sleeps == [max(1, delay), max(1, delay)]

    asyncio.run(scenario())


@pytest.mark.parametrize("modified", ["Fri, 21 Mar 2025 08:00:00 GMT", "x" * 257])
def test_only_bounded_observed_last_modified_is_preserved(network, modified):
    make, _, _ = network

    async def handler(request):
        if request.url.path == "/robots.txt":
            return reply(b"User-agent: *\nAllow: /\n", media_type="text/plain")
        return reply(
            b"<html><body>Readable recruitment</body></html>", **{"last-modified": modified}
        )

    response = asyncio.run(make(handler).crawl(CrawlRequest(url=ORIGIN + "/notice")))
    assert response.last_modified == (modified if len(modified) <= 256 else None)
    assert "Readable recruitment" in response.html
