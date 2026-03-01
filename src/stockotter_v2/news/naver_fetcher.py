from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterable
from datetime import timedelta

import requests

from stockotter_v2.config import SourceConfig
from stockotter_v2.schemas import NewsItem, now_in_seoul
from stockotter_v2.storage import FileCache

from .parser import (
    ParsedNewsLink,
    ParsedRssEntry,
    extract_article_raw_text,
    extract_article_summary,
    parse_news_listing,
    parse_rss_feed,
)

logger = logging.getLogger(__name__)

SUMMARY_ONLY_PREFIX = "[summary_only] "


class NaverNewsFetcher:
    """뉴스 수집기. RSS sources가 있으면 RSS를 우선 사용한다."""

    def __init__(
        self,
        *,
        cache: FileCache | None = None,
        session: requests.Session | None = None,
        sleep_seconds: float = 0.6,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: int = 6 * 60 * 60,
        max_pages: int = 3,
        sources: Iterable[SourceConfig] | None = None,
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
        self.sources = list(sources or [])
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

        normalized_tickers = self._normalize_tickers(tickers)
        if self._rss_sources:
            return self._fetch_recent_from_rss_sources(normalized_tickers, hours=hours)

        deduped_by_url: dict[str, NewsItem] = {}
        for normalized_ticker in normalized_tickers:
            try:
                items = self.fetch_recent_for_ticker(normalized_ticker, hours=hours)
            except Exception:
                logger.exception("failed to fetch ticker=%s", normalized_ticker)
                continue

            for item in items:
                self._merge_news_item(deduped_by_url, item)

        return list(deduped_by_url.values())

    def fetch_recent_for_ticker(self, ticker: str, *, hours: int = 24) -> list[NewsItem]:
        if hours < 1:
            raise ValueError("hours must be >= 1")

        if self._rss_sources:
            return self._fetch_recent_from_rss_sources([ticker], hours=hours)

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

    @property
    def _rss_sources(self) -> list[SourceConfig]:
        return [
            source
            for source in self.sources
            if source.enabled and source.type.lower() == "rss" and (source.url or "").strip()
        ]

    @staticmethod
    def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for raw in tickers:
            ticker = raw.strip()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            deduped.append(ticker)
        return deduped

    def _fetch_recent_from_rss_sources(
        self, tickers: Iterable[str], *, hours: int
    ) -> list[NewsItem]:
        cutoff = now_in_seoul() - timedelta(hours=hours)
        deduped_by_url: dict[str, NewsItem] = {}
        normalized_tickers = self._normalize_tickers(tickers)

        for source in self._rss_sources:
            source_url = (source.url or "").strip()
            if "{ticker}" in source_url:
                if not normalized_tickers:
                    continue
                for ticker in normalized_tickers:
                    rss_url = self._format_source_url(source_url, ticker=ticker)
                    if rss_url is None:
                        continue
                    self._collect_rss_entries(
                        deduped_by_url=deduped_by_url,
                        source_name=source.name,
                        rss_url=rss_url,
                        cutoff=cutoff,
                        default_tickers=[ticker],
                        available_tickers=normalized_tickers,
                    )
                continue

            self._collect_rss_entries(
                deduped_by_url=deduped_by_url,
                source_name=source.name,
                rss_url=source_url,
                cutoff=cutoff,
                default_tickers=[],
                available_tickers=normalized_tickers,
            )

        return list(deduped_by_url.values())

    def _collect_rss_entries(
        self,
        *,
        deduped_by_url: dict[str, NewsItem],
        source_name: str,
        rss_url: str,
        cutoff,
        default_tickers: list[str],
        available_tickers: list[str],
    ) -> None:
        try:
            xml = self._fetch_text(rss_url)
        except Exception:
            logger.exception("failed to fetch rss source=%s url=%s", source_name, rss_url)
            return

        entries = parse_rss_feed(xml, default_source=source_name)
        for entry in entries:
            if entry.published_at < cutoff:
                continue

            tickers = default_tickers or self._extract_tickers_from_entry(
                entry=entry,
                available_tickers=available_tickers,
            )
            if not tickers:
                continue

            item = self._build_rss_news_item(
                entry=entry,
                source_name=source_name,
                tickers=tickers,
            )
            self._merge_news_item(deduped_by_url, item)

    def _build_rss_news_item(
        self,
        *,
        entry: ParsedRssEntry,
        source_name: str,
        tickers: list[str],
    ) -> NewsItem:
        raw_text = entry.summary.strip() if entry.summary.strip() else ""
        if not raw_text:
            raw_text = f"{SUMMARY_ONLY_PREFIX}{entry.title}"

        return NewsItem(
            id=self._news_id(entry.url, prefix=source_name or "rss"),
            source=entry.source or source_name,
            title=entry.title,
            url=entry.url,
            published_at=entry.published_at,
            raw_text=raw_text,
            tickers_mentioned=sorted(set(tickers)),
        )

    @staticmethod
    def _extract_tickers_from_entry(
        *, entry: ParsedRssEntry, available_tickers: list[str]
    ) -> list[str]:
        if not available_tickers:
            return []
        text = " ".join([entry.title, entry.summary, entry.url])
        return [ticker for ticker in available_tickers if ticker in text]

    @staticmethod
    def _format_source_url(source_url: str, *, ticker: str) -> str | None:
        try:
            return source_url.format(ticker=ticker)
        except (IndexError, KeyError, ValueError):
            logger.exception("invalid source url template=%s", source_url)
            return None

    @staticmethod
    def _merge_news_item(store: dict[str, NewsItem], item: NewsItem) -> None:
        existing = store.get(item.url)
        if existing is None:
            store[item.url] = item
            return

        merged_tickers = sorted(set(existing.tickers_mentioned + item.tickers_mentioned))
        raw_text = (
            item.raw_text
            if len(item.raw_text) > len(existing.raw_text)
            else existing.raw_text
        )
        store[item.url] = existing.model_copy(
            update={
                "tickers_mentioned": merged_tickers,
                "raw_text": raw_text,
            }
        )

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
    def _news_id(url: str, *, prefix: str = "naver") -> str:
        normalized_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", prefix).strip("-").lower()
        if not normalized_prefix:
            normalized_prefix = "rss"
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return f"{normalized_prefix}-{digest[:16]}"
