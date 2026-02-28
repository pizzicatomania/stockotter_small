from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable
from datetime import timedelta

import requests

from stockotter_v2.schemas import NewsItem, now_in_seoul
from stockotter_v2.storage import FileCache

from .parser import (
    ParsedNewsLink,
    extract_article_raw_text,
    extract_article_summary,
    parse_news_listing,
)

logger = logging.getLogger(__name__)

SUMMARY_ONLY_PREFIX = "[summary_only] "


class NaverNewsFetcher:
    """Fetch news items from Naver Finance stock news pages."""

    def __init__(
        self,
        *,
        cache: FileCache | None = None,
        session: requests.Session | None = None,
        sleep_seconds: float = 0.6,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 6 * 60 * 60,
        max_pages: int = 3,
    ) -> None:
        if sleep_seconds < 0:
            raise ValueError("sleep_seconds must be >= 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")

        self.cache = cache
        self.session = session or requests.Session()
        self.sleep_seconds = sleep_seconds
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_pages = max_pages
        self.session.headers.setdefault(
            "User-Agent",
            (
                "Mozilla/5.0 "
                "(compatible; stockotter-small/0.1.0; "
                "+https://github.com/pizzicatomania/stockotter_small)"
            ),
        )

    def fetch_recent_for_tickers(
        self, tickers: Iterable[str], *, hours: int = 24
    ) -> list[NewsItem]:
        if hours < 1:
            raise ValueError("hours must be >= 1")

        deduped_by_url: dict[str, NewsItem] = {}
        for ticker in tickers:
            normalized_ticker = ticker.strip()
            if not normalized_ticker:
                continue
            try:
                items = self.fetch_recent_for_ticker(normalized_ticker, hours=hours)
            except Exception:
                logger.exception("failed to fetch ticker=%s", normalized_ticker)
                continue

            for item in items:
                existing = deduped_by_url.get(item.url)
                if existing is None:
                    deduped_by_url[item.url] = item
                    continue

                merged_tickers = sorted(set(existing.tickers_mentioned + item.tickers_mentioned))
                deduped_by_url[item.url] = existing.model_copy(
                    update={"tickers_mentioned": merged_tickers}
                )

        return list(deduped_by_url.values())

    def fetch_recent_for_ticker(self, ticker: str, *, hours: int = 24) -> list[NewsItem]:
        if hours < 1:
            raise ValueError("hours must be >= 1")

        cutoff = now_in_seoul() - timedelta(hours=hours)
        collected: list[NewsItem] = []

        for page in range(1, self.max_pages + 1):
            list_url = self._build_list_url(ticker=ticker, page=page)
            try:
                list_html = self._fetch_text(list_url)
            except Exception:
                logger.exception("failed to fetch list page ticker=%s page=%s", ticker, page)
                continue

            links = parse_news_listing(list_html)
            if not links:
                break

            reached_older_articles = False
            for link in links:
                if link.published_at < cutoff:
                    reached_older_articles = True
                    continue

                try:
                    item = self._build_news_item(link=link, ticker=ticker)
                except Exception:
                    logger.exception("failed to parse article ticker=%s url=%s", ticker, link.url)
                    continue
                collected.append(item)

            if reached_older_articles:
                break

        return collected

    def _build_news_item(self, *, link: ParsedNewsLink, ticker: str) -> NewsItem:
        summary = link.title
        raw_text = ""

        try:
            article_html = self._fetch_text(link.url)
        except Exception:
            logger.exception("failed to fetch article url=%s", link.url)
            article_html = ""

        if article_html:
            raw_text = extract_article_raw_text(article_html)
            if not raw_text:
                summary_from_html = extract_article_summary(article_html)
                if summary_from_html:
                    summary = summary_from_html

        if not raw_text:
            raw_text = f"{SUMMARY_ONLY_PREFIX}{summary}"

        return NewsItem(
            id=self._news_id(link.url),
            source=link.source,
            title=link.title,
            url=link.url,
            published_at=link.published_at,
            raw_text=raw_text,
            tickers_mentioned=[ticker],
        )

    def _fetch_text(self, url: str) -> str:
        if self.cache is not None:
            cached = self.cache.get(url, ttl_seconds=self.cache_ttl_seconds)
            if cached is not None:
                return cached

        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        text = response.text

        if self.cache is not None:
            self.cache.set(url, text, ttl_seconds=self.cache_ttl_seconds)
        return text

    @staticmethod
    def _build_list_url(*, ticker: str, page: int) -> str:
        return (
            "https://finance.naver.com/item/news_news.naver"
            f"?code={ticker}&page={page}&sm=title_entity_id.basic"
        )

    @staticmethod
    def _news_id(url: str) -> str:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return f"naver-{digest[:16]}"
