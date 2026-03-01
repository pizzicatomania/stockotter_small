from __future__ import annotations

from stockotter_small.news.noise_filter import is_noise_article, title_hash


def test_is_noise_article_filters_pattern_match() -> None:
    assert is_noise_article("오늘의 추천주: 급등 예상 종목")


def test_is_noise_article_filters_short_title() -> None:
    assert is_noise_article("짧은 제목")


def test_is_noise_article_filters_duplicate_title_hash() -> None:
    seen: set[str] = set()
    first = is_noise_article(
        "삼성전자 장중 상승 전환",
        seen_title_hashes=seen,
    )
    second = is_noise_article(
        "삼성전자  장중   상승 전환",
        seen_title_hashes=seen,
    )

    assert first is False
    assert second is True


def test_title_hash_is_stable_for_equivalent_titles() -> None:
    assert title_hash("SK하이닉스, 실적 개선") == title_hash(" SK하이닉스 실적 개선 ")
