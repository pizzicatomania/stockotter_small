from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from stockotter_v2.schemas import SEOUL_TZ

_DATETIME_PATTERN = re.compile(r"\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_ARTICLE_SELECTORS = (
    "#news_read",
    "#newsct_article",
    "#articeBody",
    "#dic_area",
    ".articleCont",
    ".scr01",
)


@dataclass(slots=True)
class ParsedNewsLink:
    url: str
    title: str
    source: str
    published_at: datetime


def parse_news_listing(
    html: str, *, base_url: str = "https://finance.naver.com"
) -> list[ParsedNewsLink]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table.type5 tr")
    if not rows:
        rows = soup.find_all("tr")

    parsed: list[ParsedNewsLink] = []
    for row in rows:
        anchor = row.select_one("td.title a[href]") or row.select_one(
            "a[href*='news_read.naver']"
        )
        if anchor is None:
            continue

        title = _normalize_text(anchor.get_text(" ", strip=True))
        href = anchor.get("href")
        if not title or not href:
            continue

        published_at = _extract_published_at(row.get_text(" ", strip=True))
        if published_at is None:
            continue

        source_cell = row.select_one("td.info")
        source = (
            _normalize_text(source_cell.get_text(" ", strip=True))
            if source_cell
            else "naver_finance"
        )
        parsed.append(
            ParsedNewsLink(
                url=urljoin(base_url, href),
                title=title,
                source=source or "naver_finance",
                published_at=published_at,
            )
        )

    return parsed


def extract_article_raw_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in _ARTICLE_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        for tag in node.select("script, style"):
            tag.decompose()
        text = _normalize_text(node.get_text(" ", strip=True))
        if text:
            return text
    return ""


def extract_article_summary(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for attrs in [{"property": "og:description"}, {"name": "description"}]:
        tag = soup.find("meta", attrs=attrs)
        if tag is None:
            continue
        content = tag.get("content")
        if not isinstance(content, str):
            continue
        summary = _normalize_text(content)
        if summary:
            return summary
    return ""


def _extract_published_at(text: str) -> datetime | None:
    match = _DATETIME_PATTERN.search(text)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group(0), "%Y.%m.%d %H:%M")
    except ValueError:
        return None
    return parsed.replace(tzinfo=SEOUL_TZ)


def _normalize_text(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip()
