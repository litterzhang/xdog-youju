"""Two small contract tests; no HTTP client, browser, CDP endpoint, or test-mocking framework."""

from datetime import UTC, datetime

import pytest

from youju.crawlers import (
    CrawlerMode,
    CrawlError,
    CrawlErrorCode,
    CrawlRequest,
    CrawlResponse,
    HtmlKind,
)

URL = "https://example.test/recruitment"
HTML = "<!doctype html><html><body>招聘公告</body></html>"
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def test_modes_share_html_but_preserve_capture_semantics():
    assert CrawlerMode("headed") is CrawlerMode.HEADED
    for mode, headless, kind in (
        (CrawlerMode.STATIC, None, HtmlKind.RESPONSE_HTML),
        (CrawlerMode.DYNAMIC, True, HtmlKind.RENDERED_DOM),
        (CrawlerMode.HEADED, False, HtmlKind.RENDERED_DOM),
    ):
        response = CrawlResponse(
            request=CrawlRequest(url=URL, mode=mode),
            final_url=URL,
            html=HTML,
            http_status=200 if mode is CrawlerMode.STATIC else None,
            captured_at=NOW,
            response_body=HTML.encode("utf-8") if mode is CrawlerMode.STATIC else None,
        )
        assert response.html == HTML
        assert response.html_kind is kind
        assert mode.headless is headless
        assert "招聘公告" not in repr(response)


def test_invalid_or_oversized_capture_is_not_a_successful_html_response():
    with pytest.raises(ValueError, match="cannot wait"):
        CrawlRequest(url=URL, ready_selector=".job-list")
    with pytest.raises(ValueError, match="preserve"):
        CrawlResponse(
            request=CrawlRequest(url=URL),
            final_url=URL,
            html=HTML,
            http_status=None,
            captured_at=NOW,
        )
    with pytest.raises(CrawlError) as error:
        CrawlResponse(
            request=CrawlRequest(url=URL, mode=CrawlerMode.DYNAMIC, max_html_bytes=1),
            final_url=URL,
            html=HTML,
            http_status=None,
            captured_at=NOW,
        )
    assert error.value.code is CrawlErrorCode.SIZE_LIMIT
