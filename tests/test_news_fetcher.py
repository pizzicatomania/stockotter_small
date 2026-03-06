from __future__ import annotations

from datetime import datetime
from pathlib import Path

from stockotter_v2.news.naver_fetcher import NaverNewsFetcher
from stockotter_v2.news.parser import ParsedRssEntry
from stockotter_v2.schemas import SEOUL_TZ

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _rss_entry(*, title: str, summary: str) -> ParsedRssEntry:
    return ParsedRssEntry(
        url="https://news.google.com/rss/articles/sample",
        title=title,
        source="google_news",
        summary=summary,
        published_at=datetime(2026, 3, 1, 9, 0, tzinfo=SEOUL_TZ),
    )


def test_build_rss_news_item_fetches_article_when_summary_is_title_like(monkeypatch) -> None:
    fetcher = NaverNewsFetcher(sleep_seconds=0.0, timeout_seconds=1.0)
    entry = _rss_entry(
        title="삼성전자, AI 반도체 수요 기대",
        summary="삼성전자, AI 반도체 수요 기대",
    )

    monkeypatch.setattr(
        fetcher,
        "_fetch_text",
        lambda url: _fixture("naver_news_article.sample.html"),
    )

    item = fetcher._build_rss_news_item(
        entry=entry,
        source_name="google_news",
        tickers=["005930"],
        canonical_url="https://example.com/news/005930",
    )

    assert "첫 번째 문장입니다." in item.raw_text
    assert "두 번째 문장입니다." in item.raw_text


def test_build_rss_news_item_keeps_summary_when_not_title_like(monkeypatch) -> None:
    fetcher = NaverNewsFetcher(sleep_seconds=0.0, timeout_seconds=1.0)
    entry = _rss_entry(
        title="삼성전자, AI 반도체 수요 기대",
        summary="메모리 업황 반등과 수주 개선이 핵심 변수로 제시됐다.",
    )

    def _fail_if_called(url: str) -> str:
        raise AssertionError(f"_fetch_text should not be called url={url}")

    monkeypatch.setattr(fetcher, "_fetch_text", _fail_if_called)

    item = fetcher._build_rss_news_item(
        entry=entry,
        source_name="google_news",
        tickers=["005930"],
        canonical_url="https://example.com/news/005930",
    )

    assert item.raw_text == "메모리 업황 반등과 수주 개선이 핵심 변수로 제시됐다."


def test_build_rss_news_item_fallbacks_to_summary_only_prefix_on_empty_content(monkeypatch) -> None:
    fetcher = NaverNewsFetcher(sleep_seconds=0.0, timeout_seconds=1.0)
    entry = _rss_entry(
        title="삼성전자, AI 반도체 수요 기대",
        summary="",
    )

    monkeypatch.setattr(
        fetcher,
        "_fetch_text",
        lambda url: _fixture("naver_news_article.summary_only.sample.html"),
    )

    item = fetcher._build_rss_news_item(
        entry=entry,
        source_name="google_news",
        tickers=["005930"],
        canonical_url="https://example.com/news/005930",
    )

    assert item.raw_text.startswith("[summary_only] ")
    assert "본문 추출 실패 시 사용할 요약 문장" in item.raw_text
