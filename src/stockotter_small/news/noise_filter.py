from __future__ import annotations

import hashlib
from collections.abc import Sequence

from .google_utils import normalize_title_for_dedupe

DEFAULT_NOISE_PATTERNS = [
    "광고",
    "협찬",
    "리포트 전문",
    "주가전망",
    "전문가 추천",
    "오늘의 추천주",
]


def is_noise_article(
    title: str,
    *,
    patterns: Sequence[str] | None = None,
    min_title_length: int = 10,
    seen_title_hashes: set[str] | None = None,
) -> bool:
    normalized_title = " ".join(title.split())
    if len(normalized_title) < min_title_length:
        return True

    lowered_title = normalized_title.lower()
    active_patterns = DEFAULT_NOISE_PATTERNS if patterns is None else patterns
    for pattern in active_patterns:
        if pattern and pattern.lower() in lowered_title:
            return True

    if seen_title_hashes is not None:
        digest = title_hash(normalized_title)
        if digest in seen_title_hashes:
            return True
        seen_title_hashes.add(digest)

    return False


def title_hash(title: str) -> str:
    normalized = normalize_title_for_dedupe(title)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
