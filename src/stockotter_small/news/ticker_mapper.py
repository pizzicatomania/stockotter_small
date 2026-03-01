from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

_WORD_CHARS = "0-9A-Za-z가-힣"
_TRAILING_PARTICLES = "은는이가을를의와과도에에서로으로"
_WHITESPACE_PATTERN = re.compile(r"\s+")


def load_ticker_map(path: str | Path | None = None) -> dict[str, str]:
    target_path = Path(path) if path is not None else _default_map_path()
    return _load_ticker_map_cached(str(target_path.resolve()))


def map_news_to_tickers(
    title: str,
    summary: str,
    *,
    ticker_map: Mapping[str, str] | None = None,
) -> list[str]:
    lookup = dict(ticker_map) if ticker_map is not None else load_ticker_map()
    if not lookup:
        return []

    text = _normalize_text(f"{title} {summary}")
    if not text:
        return []

    matched: list[str] = []
    candidates = sorted(
        ((ticker, name.strip()) for ticker, name in lookup.items() if name.strip()),
        key=lambda pair: (-len(pair[1]), pair[0]),
    )
    for ticker, stock_name in candidates:
        if _contains_stock_name(text=text, stock_name=stock_name):
            matched.append(ticker)

    return sorted(set(matched))


@lru_cache(maxsize=8)
def _load_ticker_map_cached(path: str) -> dict[str, str]:
    target = Path(path)
    if not target.exists():
        return {}

    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ticker map file must contain a JSON object.")

    normalized: dict[str, str] = {}
    for ticker, name in payload.items():
        if not isinstance(ticker, str) or not isinstance(name, str):
            continue
        clean_ticker = ticker.strip()
        clean_name = name.strip()
        if not clean_ticker or not clean_name:
            continue
        normalized[clean_ticker] = clean_name
    return normalized


def _contains_stock_name(*, text: str, stock_name: str) -> bool:
    normalized_name = _normalize_text(stock_name)
    if not normalized_name:
        return False

    pattern = re.compile(
        rf"(?<![{_WORD_CHARS}])"
        rf"{re.escape(normalized_name)}"
        rf"(?=$|[^{_WORD_CHARS}]|[{_TRAILING_PARTICLES}])",
        flags=re.IGNORECASE,
    )
    return pattern.search(text) is not None


def _normalize_text(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip().lower()


def _default_map_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "ticker_map.json"
