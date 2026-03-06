from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from typing import TypeVar
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

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
_BATCH_EXECUTE_URL = (
    "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je"
)


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

    if _is_http_url(response.url) and not _is_google_rss_article_url(response.url):
        return response.url

    decoded = _decode_google_article_url(
        page_html=response.text,
        referer_url=response.url,
        session=requester,
        timeout_seconds=timeout_seconds,
    )
    if decoded is not None:
        return decoded

    if _is_http_url(response.url):
        return response.url
    return None


def _decode_google_article_url(
    *,
    page_html: str,
    referer_url: str,
    session: requests.Session,
    timeout_seconds: float,
) -> str | None:
    article_meta = _extract_google_article_meta(page_html)
    if article_meta is None:
        return None

    article_id, article_ts, article_sig = article_meta
    f_req_payload = _build_batch_request_payload(
        article_id=article_id,
        article_ts=article_ts,
        article_sig=article_sig,
    )

    try:
        response = session.post(
            _BATCH_EXECUTE_URL,
            data={"f.req": f_req_payload},
            timeout=timeout_seconds,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": referer_url,
                "User-Agent": "stockotter-small/0.1.0",
            },
        )
        response.raise_for_status()
    except Exception:
        logger.debug("google batch decode failed referer=%s", referer_url, exc_info=True)
        return None

    return _parse_batch_execute_redirect(response.text)


def _extract_google_article_meta(page_html: str) -> tuple[str, str, str] | None:
    soup = BeautifulSoup(page_html, "html.parser")
    node = soup.select_one(
        'div[jscontroller="aLI87"][data-n-a-id][data-n-a-ts][data-n-a-sg]'
    )
    if node is None:
        return None

    article_id = str(node.get("data-n-a-id", "")).strip()
    article_ts = str(node.get("data-n-a-ts", "")).strip()
    article_sig = str(node.get("data-n-a-sg", "")).strip()
    if not article_id or not article_ts or not article_sig:
        return None
    if not article_ts.isdigit():
        return None

    return article_id, article_ts, article_sig


def _build_batch_request_payload(
    *,
    article_id: str,
    article_ts: str,
    article_sig: str,
) -> str:
    # Empirically stable payload for resolving Google News article redirection.
    inner = [
        "garturlreq",
        [
            [
                "en-US",
                "US",
                ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"],
                None,
                None,
                1,
                1,
                "US:en",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                False,
            ],
            "en-US",
            "US",
            1,
            [2, 3, 4, 8],
            1,
            0,
            "655000234",
            0,
            0,
            None,
            0,
        ],
        article_id,
        int(article_ts),
        article_sig,
    ]
    rpc = [["Fbv4je", json.dumps(inner, separators=(",", ":")), None, "generic"]]
    return json.dumps([rpc], separators=(",", ":"))


def _parse_batch_execute_redirect(payload: str) -> str | None:
    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(")]}'"):
            continue
        try:
            rows = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(rows, list):
            continue

        for row in rows:
            if not isinstance(row, list) or len(row) < 3:
                continue
            if row[0] != "wrb.fr":
                continue
            raw_result = row[2]
            if not isinstance(raw_result, str) or "garturlres" not in raw_result:
                continue
            try:
                parsed = json.loads(raw_result)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(parsed, list)
                and len(parsed) >= 2
                and parsed[0] == "garturlres"
                and isinstance(parsed[1], str)
                and _is_http_url(parsed[1])
            ):
                return parsed[1]
    return None


def _is_google_rss_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.netloc.lower().endswith("news.google.com") and parsed.path.startswith(
        "/rss/articles/"
    )


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
