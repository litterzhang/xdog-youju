"""Bounded static and CDP-backed browser HTML acquisition."""

from .browser import DynamicCrawler, HeadedCrawler
from .contracts import (
    Crawler,
    CrawlerMode,
    CrawlError,
    CrawlErrorCode,
    CrawlRequest,
    CrawlResponse,
    HtmlKind,
)
from .policy import OriginPolicy
from .static import StaticCrawler

__all__ = [
    "Crawler",
    "CrawlerMode",
    "CrawlError",
    "CrawlErrorCode",
    "CrawlRequest",
    "CrawlResponse",
    "HtmlKind",
    "DynamicCrawler",
    "HeadedCrawler",
    "OriginPolicy",
    "StaticCrawler",
]
