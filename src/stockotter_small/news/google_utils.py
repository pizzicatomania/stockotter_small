from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Iterable
from typing import TypeVar
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_TITLE_NORMALIZE_PATTERN = re.compile(r"[^0-9a-zA-Z가-힣]+")
_TRACKING_PARAM_KEYS = {
    "oc",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "igshid",
    "mkt_tok",
    "spm",
}
_EMBEDDED_URL_KEYS = ("url", "u", "q")


def normalize_google_url(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout_seconds: float = 8.0,
) -> str:
    """Normalize Google RSS URL into canonical article URL when possible."""
    normalized_input = (url or "").strip()
    if not normalized_input:
        return url

    embedded = _extract_embedded_url(normalized_input)
    if embedded is not None:
        return remove_tracking_parameters(embedded)

    parsed = urlsplit(normalized_input)
    if (
        parsed.netloc.lower().endswith("news.google.com")
        and parsed.path.startswith("/rss/articles/")
    ):
        resolved = _resolve_google_redirect(
            normalized_input,
            session=session,
            timeout_seconds=timeout_seconds,
        )
        if resolved is not None:
            return remove_tracking_parameters(resolved)

    return remove_tracking_parameters(normalized_input)


def remove_tracking_parameters(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    cleaned_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered_key = key.lower()
        if lowered_key in _TRACKING_PARAM_KEYS:
            continue
        if lowered_key.startswith("utm_"):
            continue
        cleaned_pairs.append((key, value))

    cleaned_pairs.sort(key=lambda pair: (pair[0], pair[1]))
    query = urlencode(cleaned_pairs, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def normalize_title_for_dedupe(title: str) -> str:
    lowered = title.lower()
    normalized = _TITLE_NORMALIZE_PATTERN.sub(" ", lowered)
    return " ".join(normalized.split())


def dedupe_exact_by_normalized_title(
    items: Iterable[_T],
    *,
    get_title: Callable[[_T], str],
) -> tuple[list[_T], int]:
    seen_hashes: set[str] = set()
    unique: list[_T] = []
    dropped = 0

    for item in items:
        digest = hashlib.sha1(
            normalize_title_for_dedupe(get_title(item)).encode("utf-8")
        ).hexdigest()
        if digest in seen_hashes:
            dropped += 1
            continue
        seen_hashes.add(digest)
        unique.append(item)
    return unique, dropped


def _extract_embedded_url(url: str) -> str | None:
    parsed = urlsplit(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() not in _EMBEDDED_URL_KEYS:
            continue

        candidate = value.strip()
        if not candidate:
            continue
        decoded = unquote(candidate)
        if _is_http_url(decoded):
            return decoded
        if _is_http_url(candidate):
            return candidate
    return None


def _resolve_google_redirect(
    url: str,
    *,
    session: requests.Session | None,
    timeout_seconds: float,
) -> str | None:
    requester = session or requests.Session()
    try:
        response = requester.get(
            url,
            timeout=timeout_seconds,
            allow_redirects=True,
            headers={"User-Agent": "stockotter-small/0.1.0"},
        )
        response.raise_for_status()
    except Exception:
        logger.debug("google redirect resolution failed url=%s", url, exc_info=True)
        return None

    if _is_http_url(response.url):
        return response.url
    return None


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
