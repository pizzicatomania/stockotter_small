from __future__ import annotations

import json

import pytest
import requests

from stockotter_v2.llm.gemini_client import GeminiClient


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        error = requests.HTTPError(f"{self.status_code} error")
        error.response = self  # type: ignore[assignment]
        raise error

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def post(
        self,
        url: str,
        *,
        params: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> _FakeResponse:
        _ = params, json, timeout
        self.urls.append(url)
        if not self.responses:
            raise RuntimeError("no response queued")
        return self.responses.pop(0)


def _ok_payload(text: str) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text}],
                }
            }
        ]
    }


def test_gemini_client_fallbacks_to_flash_lite_on_quota_exhausted() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=429,
                payload={
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": "quota exceeded",
                    }
                },
            ),
            _FakeResponse(status_code=200, payload=_ok_payload('{"ok": true}')),
        ]
    )
    client = GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        fallback_model="gemini-2.5-flash-lite",
        session=session,  # type: ignore[arg-type]
    )

    output = client.generate("hello")

    assert output == '{"ok": true}'
    assert len(session.urls) == 2
    assert session.urls[0].endswith("/models/gemini-2.5-flash:generateContent")
    assert session.urls[1].endswith("/models/gemini-2.5-flash-lite:generateContent")


def test_gemini_client_does_not_fallback_on_non_quota_error() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=500,
                payload={
                    "error": {
                        "code": 500,
                        "status": "INTERNAL",
                        "message": "internal error",
                    }
                },
            )
        ]
    )
    client = GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        fallback_model="gemini-2.5-flash-lite",
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(requests.HTTPError):
        client.generate("hello")

    assert len(session.urls) == 1
    assert session.urls[0].endswith("/models/gemini-2.5-flash:generateContent")


def test_gemini_client_without_fallback_model_raises_quota_error() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=429,
                payload={
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": "quota exceeded",
                    }
                },
            )
        ]
    )
    client = GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        fallback_model=None,
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(requests.HTTPError):
        client.generate("hello")

    assert len(session.urls) == 1
