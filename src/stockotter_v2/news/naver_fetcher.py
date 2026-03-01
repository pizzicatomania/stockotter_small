from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import requests

from stockotter_small.news.google_utils import normalize_google_url
from stockotter_small.news.noise_filter import is_noise_article
from stockotter_small.news.ticker_mapper import load_ticker_map, map_news_to_tickers
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


@dataclass(slots=True)
class _RssIngestStats:
    fetched_articles: int = 0
    mapped_articles: int = 0
    dropped_noise: int = 0
    dropped_exact_dedupe: int = 0
    kept_articles: int = 0


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
        ticker_map_path: str | Path | None = None,
        noise_patterns: list[str] | None = None,
        noise_min_title_length: int = 10,
        enable_noise_filter: bool = True,
        drop_duplicate_titles: bool = True,
    ) -> None:
        if sleep_seconds < 0:
            raise ValueError("sleep_seconds must be >= 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be >= 0")
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        if noise_min_title_length < 1:
            raise ValueError("noise_min_title_length must be >= 1")

        self.cache = cache
        self.session = session or requests.Session()
        self.sleep_seconds = sleep_seconds
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_pages = max_pages
        self.sources = list(sources or [])
        self.noise_patterns = list(noise_patterns or [])
        self.noise_min_title_length = noise_min_title_length
        self.enable_noise_filter = enable_noise_filter
        self.drop_duplicate_titles = drop_duplicate_titles

        self.ticker_map = load_ticker_map(ticker_map_path)
        if not self.ticker_map:
            logger.warning("ticker map is empty path=%s", ticker_map_path)

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
        ingest_stats = _RssIngestStats()
        seen_title_hashes: set[str] = set()

        for source in self._rss_sources:
            source_url = (source.url or "").strip()
            for rss_url, default_tickers in self._iter_source_urls(
                source_url=source_url,
                tickers=normalized_tickers,
            ):
                self._collect_rss_entries(
                    deduped_by_url=deduped_by_url,
                    source_name=source.name,
                    rss_url=rss_url,
                    cutoff=cutoff,
                    default_tickers=default_tickers,
                    available_tickers=normalized_tickers,
                    ingest_stats=ingest_stats,
                    seen_title_hashes=seen_title_hashes,
                )

        logger.info(
            (
                "rss_ingest_stats fetched=%d mapped=%d noise_dropped=%d "
                "exact_dedupe_dropped=%d kept=%d"
            ),
            ingest_stats.fetched_articles,
            ingest_stats.mapped_articles,
            ingest_stats.dropped_noise,
            ingest_stats.dropped_exact_dedupe,
            ingest_stats.kept_articles,
            extra={
                "fetched": ingest_stats.fetched_articles,
                "mapped": ingest_stats.mapped_articles,
                "noise_dropped": ingest_stats.dropped_noise,
                "exact_dedupe_dropped": ingest_stats.dropped_exact_dedupe,
                "kept": ingest_stats.kept_articles,
            },
        )
        return list(deduped_by_url.values())

    def _iter_source_urls(
        self,
        *,
        source_url: str,
        tickers: list[str],
    ) -> list[tuple[str, list[str]]]:
        if not source_url:
            return []

        targets: list[tuple[str, list[str]]] = []
        has_ticker_placeholder = "{ticker}" in source_url
        has_name_placeholder = (
            "{stock_name}" in source_url or "{stock_name_urlencoded}" in source_url
        )

        if has_ticker_placeholder or has_name_placeholder:
            for ticker in tickers:
                stock_name = self.ticker_map.get(ticker, "")
                if has_name_placeholder and not stock_name:
                    continue

                formatted = self._format_source_url(
                    source_url,
                    ticker=ticker,
                    stock_name=stock_name,
                )
                if formatted is None:
                    continue
                targets.append((formatted, [ticker]))
            return targets

        return [(source_url, [])]

    def _collect_rss_entries(
        self,
        *,
        deduped_by_url: dict[str, NewsItem],
        source_name: str,
        rss_url: str,
        cutoff: datetime,
        default_tickers: list[str],
        available_tickers: list[str],
        ingest_stats: _RssIngestStats,
        seen_title_hashes: set[str],
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
            ingest_stats.fetched_articles += 1

            tickers = sorted(
                set(
                    default_tickers
                    or self._extract_tickers_from_entry(
                        entry=entry,
                        available_tickers=available_tickers,
                    )
                )
            )
            if not tickers:
                continue
            ingest_stats.mapped_articles += 1

            if self.enable_noise_filter and is_noise_article(
                entry.title,
                patterns=self.noise_patterns,
                min_title_length=self.noise_min_title_length,
                seen_title_hashes=(
                    seen_title_hashes if self.drop_duplicate_titles else None
                ),
            ):
                ingest_stats.dropped_noise += 1
                continue

            canonical_url = normalize_google_url(
                entry.url,
                session=self.session,
                timeout_seconds=min(self.timeout_seconds, 8.0),
            )
            item = self._build_rss_news_item(
                entry=entry,
                source_name=source_name,
                tickers=tickers,
                canonical_url=canonical_url,
            )
            inserted = self._merge_news_item(deduped_by_url, item)
            if inserted:
                ingest_stats.kept_articles += 1
            else:
                ingest_stats.dropped_exact_dedupe += 1

    def _build_rss_news_item(
        self,
        *,
        entry: ParsedRssEntry,
        source_name: str,
        tickers: list[str],
        canonical_url: str,
    ) -> NewsItem:
        raw_text = entry.summary.strip() if entry.summary.strip() else ""
        if not raw_text:
            raw_text = f"{SUMMARY_ONLY_PREFIX}{entry.title}"

        return NewsItem(
            id=self._news_id(canonical_url, prefix=source_name or "rss"),
            source=entry.source or source_name,
            title=entry.title,
            url=canonical_url,
            published_at=entry.published_at,
            raw_text=raw_text,
            tickers_mentioned=sorted(set(tickers)),
        )

    def _extract_tickers_from_entry(
        self,
        *,
        entry: ParsedRssEntry,
        available_tickers: list[str],
    ) -> list[str]:
        if not self.ticker_map:
            return []

        if available_tickers:
            lookup = {
                ticker: self.ticker_map[ticker]
                for ticker in available_tickers
                if ticker in self.ticker_map
            }
        else:
            lookup = self.ticker_map

        mapped = map_news_to_tickers(
            title=entry.title,
            summary=entry.summary,
            ticker_map=lookup,
        )
        if mapped:
            return mapped

        text = " ".join([entry.title, entry.summary, entry.url])
        return [ticker for ticker in available_tickers if ticker in text]

    @staticmethod
    def _format_source_url(
        source_url: str,
        *,
        ticker: str,
        stock_name: str = "",
    ) -> str | None:
        try:
            return source_url.format(
                ticker=ticker,
                stock_name=stock_name,
                stock_name_urlencoded=quote_plus(stock_name),
            )
        except (IndexError, KeyError, ValueError):
            logger.exception("invalid source url template=%s", source_url)
            return None

    @staticmethod
    def _merge_news_item(store: dict[str, NewsItem], item: NewsItem) -> bool:
        existing = store.get(item.url)
        if existing is None:
            store[item.url] = item
            return True

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
        return False

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
