from __future__ import annotations

import requests

from stockotter_small.news.google_utils import (
    dedupe_exact_by_normalized_title,
    normalize_google_url,
    remove_tracking_parameters,
)


class _FakeResponse:
    def __init__(self, *, url: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        error = requests.HTTPError(f"{self.status_code} error")
        error.response = self  # type: ignore[assignment]
        raise error


class _FakeSession:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.response = response
        self.called_url: str | None = None

    def get(self, url: str, **_: object) -> _FakeResponse:
        self.called_url = url
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_remove_tracking_parameters_strips_known_keys() -> None:
    url = "https://example.com/news?a=1&utm_source=google&fbclid=abc&b=2#frag"
    normalized = remove_tracking_parameters(url)
    assert normalized == "https://example.com/news?a=1&b=2"


def test_normalize_google_url_uses_embedded_url_param() -> None:
    url = (
        "https://news.google.com/rss/articles/CBMi?"
        "url=https%3A%2F%2Fexample.com%2Farticle%3Futm_campaign%3Dabc%26x%3D1&oc=5"
    )
    normalized = normalize_google_url(url)
    assert normalized == "https://example.com/article?x=1"


def test_normalize_google_url_resolves_google_redirect() -> None:
    session = _FakeSession(
        _FakeResponse(
            url="https://news.example.com/item?utm_source=google&ref=home&id=10"
        )
    )
    normalized = normalize_google_url(
        "https://news.google.com/rss/articles/CBMiQWh0",
        session=session,  # type: ignore[arg-type]
    )

    assert session.called_url == "https://news.google.com/rss/articles/CBMiQWh0"
    assert normalized == "https://news.example.com/item?id=10&ref=home"


def test_normalize_google_url_falls_back_to_original_on_failure() -> None:
    session = _FakeSession(Exception("network failure"))
    original = "https://news.google.com/rss/articles/CBMiQWh0"

    normalized = normalize_google_url(
        original,
        session=session,  # type: ignore[arg-type]
    )
    assert normalized == original


def test_dedupe_exact_by_normalized_title_drops_duplicates() -> None:
    titles = [
        "삼성전자, AI 수요 기대",
        " 삼성전자 AI 수요 기대 ",
        "SK하이닉스, 실적 개선",
    ]
    unique, dropped = dedupe_exact_by_normalized_title(titles, get_title=lambda value: value)

    assert unique == ["삼성전자, AI 수요 기대", "SK하이닉스, 실적 개선"]
    assert dropped == 1
