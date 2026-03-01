from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree

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


@dataclass(slots=True)
class ParsedRssEntry:
    url: str
    title: str
    source: str
    summary: str
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


def parse_rss_feed(xml: str, *, default_source: str) -> list[ParsedRssEntry]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    parsed: list[ParsedRssEntry] = []
    items = _find_elements_by_local_name(root, "item")
    if items:
        for item in items:
            title = _normalize_text(_first_child_text(item, ["title"]))
            link = _extract_rss_link(item)
            published_at = _parse_rss_published_at(
                _first_child_text(item, ["pubDate", "published", "updated", "date"])
            )
            summary = _normalize_text(
                _first_child_text(item, ["description", "summary", "content", "encoded"])
                or title
            )
            source = _normalize_text(_first_child_text(item, ["source"])) or default_source
            if not title or not link or published_at is None:
                continue
            parsed.append(
                ParsedRssEntry(
                    url=link,
                    title=title,
                    source=source,
                    summary=summary or title,
                    published_at=published_at,
                )
            )
        return parsed

    for entry in _find_elements_by_local_name(root, "entry"):
        title = _normalize_text(_first_child_text(entry, ["title"]))
        link = _extract_atom_link(entry)
        published_at = _parse_rss_published_at(
            _first_child_text(entry, ["published", "updated", "date"])
        )
        summary = _normalize_text(
            _first_child_text(entry, ["summary", "content"])
            or title
        )
        source = _normalize_text(_first_child_text(entry, ["source"])) or default_source
        if not title or not link or published_at is None:
            continue
        parsed.append(
            ParsedRssEntry(
                url=link,
                title=title,
                source=source,
                summary=summary or title,
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


def _parse_rss_published_at(text: str) -> datetime | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    try:
        parsed = parsedate_to_datetime(cleaned)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        iso_text = cleaned.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(iso_text)
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SEOUL_TZ)
    return parsed


def _extract_rss_link(node: ElementTree.Element) -> str:
    for tag_name in ("link", "guid"):
        for child in node:
            if _local_name(child.tag) != tag_name:
                continue
            href = (child.attrib.get("href") or "").strip()
            if href:
                return href
            text = _normalize_text("".join(child.itertext()))
            if text:
                return text
    return ""


def _extract_atom_link(node: ElementTree.Element) -> str:
    links = [child for child in node if _local_name(child.tag) == "link"]
    for link in links:
        rel = (link.attrib.get("rel") or "").strip().lower()
        href = (link.attrib.get("href") or "").strip()
        if href and rel in {"", "alternate"}:
            return href

    if not links:
        return ""

    first_link = links[0]
    href = (first_link.attrib.get("href") or "").strip()
    if href:
        return href
    return _normalize_text("".join(first_link.itertext()))


def _normalize_text(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def _find_elements_by_local_name(
    node: ElementTree.Element, name: str
) -> list[ElementTree.Element]:
    return [element for element in node.iter() if _local_name(element.tag) == name]


def _first_child_text(node: ElementTree.Element, names: list[str]) -> str:
    target_names = {name.lower() for name in names}
    for child in node:
        if _local_name(child.tag).lower() in target_names:
            return _normalize_text("".join(child.itertext()))
    return ""


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", maxsplit=1)[-1]
    if ":" in tag:
        return tag.rsplit(":", maxsplit=1)[-1]
    return tag
