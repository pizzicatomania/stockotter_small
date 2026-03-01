"""Naver Finance news fetching utilities."""

from .naver_fetcher import NaverNewsFetcher
from .parser import (
    ParsedNewsLink,
    ParsedRssEntry,
    extract_article_raw_text,
    extract_article_summary,
    parse_news_listing,
    parse_rss_feed,
)

__all__ = [
    "NaverNewsFetcher",
    "ParsedNewsLink",
    "ParsedRssEntry",
    "extract_article_raw_text",
    "extract_article_summary",
    "parse_news_listing",
    "parse_rss_feed",
]
