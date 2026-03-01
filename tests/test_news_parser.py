from __future__ import annotations

from pathlib import Path

from stockotter_v2.news.parser import (
    extract_article_raw_text,
    extract_article_summary,
    parse_news_listing,
    parse_rss_feed,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_parse_news_listing_extracts_required_fields() -> None:
    html = _fixture("naver_news_list.sample.html")
    items = parse_news_listing(html)

    assert len(items) == 2
    assert items[0].title == "삼성전자, AI 반도체 수요 기대"
    assert items[0].source == "연합뉴스"
    assert items[0].url.startswith(
        "https://finance.naver.com/item/news_read.naver?article_id=0000001"
    )
    assert items[0].published_at.strftime("%Y-%m-%d %H:%M %z") == "2026-02-28 10:15 +0900"


def test_extract_article_raw_text_uses_news_body() -> None:
    html = _fixture("naver_news_article.sample.html")
    raw_text = extract_article_raw_text(html)

    assert "첫 번째 문장입니다." in raw_text
    assert "두 번째 문장입니다." in raw_text
    assert "ignore" not in raw_text


def test_extract_article_summary_fallback() -> None:
    html = _fixture("naver_news_article.summary_only.sample.html")

    assert extract_article_raw_text(html) == ""
    assert extract_article_summary(html) == "본문 추출 실패 시 사용할 요약 문장"


def test_parse_rss_feed_extracts_required_fields() -> None:
    xml = _fixture("rss_feed.sample.xml")

    items = parse_rss_feed(xml, default_source="google_news")

    assert len(items) == 2
    assert items[0].title == "삼성전자(005930) 관련 기사"
    assert items[0].url == "https://example.com/news/005930-1"
    assert items[0].source == "테스트언론"
    assert items[0].summary == "삼성전자 주가 관련 요약"
    assert items[0].published_at.strftime("%Y-%m-%d %H:%M %z") == "2026-02-28 01:15 +0000"

    assert items[1].title == "SK하이닉스(000660) 관련 기사"
    assert items[1].url == "https://example.com/news/000660-1"
    assert items[1].source == "google_news"
