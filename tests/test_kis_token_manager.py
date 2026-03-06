from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import requests

from stockotter_small.broker.kis.token_manager import (
    TokenManager,
    resolve_kis_base_url,
    split_kis_account,
)


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
        self.post_calls = 0

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> _FakeResponse:
        _ = url, json, timeout
        self.post_calls += 1
        if not self.responses:
            raise RuntimeError("no response queued")
        return self.responses.pop(0)


class _ErrorSession:
    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> _FakeResponse:
        _ = url, json, timeout
        raise requests.ConnectionError("network down")


def test_resolve_kis_base_url_switch() -> None:
    assert resolve_kis_base_url("paper").endswith(":29443")
    assert resolve_kis_base_url("live").endswith(":9443")

    with pytest.raises(ValueError):
        resolve_kis_base_url("sandbox")


def test_split_kis_account_accepts_8_digits_and_defaults_product_code() -> None:
    cano, acnt_prdt_cd = split_kis_account("12345678")
    assert cano == "12345678"
    assert acnt_prdt_cd == "01"

    cano2, acnt_prdt_cd2 = split_kis_account("12345678-02")
    assert cano2 == "12345678"
    assert acnt_prdt_cd2 == "02"


def test_token_manager_normalizes_8_digit_account() -> None:
    manager = TokenManager(
        app_key="app-key",
        app_secret="app-secret",
        account="12345678",
        environment="paper",
        session=_FakeSession(responses=[]),  # type: ignore[arg-type]
    )
    assert manager.account == "12345678-01"


def test_token_manager_reuses_cached_token_before_expiry(tmp_path) -> None:
    now = {"value": datetime(2026, 3, 1, 0, 0, tzinfo=UTC)}
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "access_token": "token-first",
                    "expires_in": 3600,
                },
            )
        ]
    )
    manager = TokenManager(
        app_key="app-key",
        app_secret="app-secret",
        account="12345678-01",
        environment="paper",
        cache_path=tmp_path / "kis_token.json",
        session=session,  # type: ignore[arg-type]
        now_fn=lambda: now["value"],
        refresh_margin_seconds=0,
    )

    first = manager.get_token()
    second = manager.get_token()

    assert first.access_token == "token-first"
    assert second.access_token == "token-first"
    assert session.post_calls == 1
    assert manager.cache_path.exists()


def test_token_manager_refreshes_when_token_expired(tmp_path) -> None:
    now = {"value": datetime(2026, 3, 1, 0, 0, tzinfo=UTC)}
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "access_token": "token-first",
                    "expires_in": 10,
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "access_token": "token-second",
                    "expires_in": 3600,
                },
            ),
        ]
    )
    manager = TokenManager(
        app_key="app-key",
        app_secret="app-secret",
        account="12345678-01",
        environment="live",
        cache_path=tmp_path / "kis_token_live.json",
        session=session,  # type: ignore[arg-type]
        now_fn=lambda: now["value"],
        refresh_margin_seconds=0,
    )

    first = manager.get_token()
    now["value"] = now["value"] + timedelta(seconds=15)
    second = manager.get_token()

    assert first.access_token == "token-first"
    assert second.access_token == "token-second"
    assert session.post_calls == 2


def test_token_manager_loads_cache_file_without_http(tmp_path) -> None:
    expires_at = datetime(2026, 3, 1, 3, 0, tzinfo=UTC)
    cache_path = tmp_path / "cached_token.json"
    cache_path.write_text(
        json.dumps(
            {
                "access_token": "cached-token",
                "expires_at": expires_at.isoformat(),
                "environment": "paper",
            }
        ),
        encoding="utf-8",
    )

    now = {"value": datetime(2026, 3, 1, 0, 0, tzinfo=UTC)}
    manager = TokenManager(
        app_key="app-key",
        app_secret="app-secret",
        account="12345678-01",
        environment="paper",
        cache_path=cache_path,
        session=_FakeSession(responses=[]),  # type: ignore[arg-type]
        now_fn=lambda: now["value"],
    )

    token = manager.get_token()

    assert token.access_token == "cached-token"
    assert token.expires_at == expires_at


def test_token_manager_logs_do_not_include_secrets(tmp_path, caplog) -> None:
    now = {"value": datetime(2026, 3, 1, 0, 0, tzinfo=UTC)}
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "access_token": "token-sensitive",
                    "expires_in": 3600,
                },
            )
        ]
    )
    manager = TokenManager(
        app_key="app-key-sensitive",
        app_secret="app-secret-sensitive",
        account="12345678-01",
        environment="paper",
        cache_path=tmp_path / "token_secure.json",
        session=session,  # type: ignore[arg-type]
        now_fn=lambda: now["value"],
    )

    caplog.set_level("INFO")
    manager.get_token()

    assert "app-key-sensitive" not in caplog.text
    assert "app-secret-sensitive" not in caplog.text
    assert "token-sensitive" not in caplog.text


def test_token_manager_wraps_http_error_with_safe_message(tmp_path) -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=403,
                payload={
                    "msg_cd": "EGW00123",
                    "msg1": "Forbidden",
                },
            )
        ]
    )
    manager = TokenManager(
        app_key="app-key",
        app_secret="app-secret",
        account="12345678",
        environment="paper",
        cache_path=tmp_path / "token_http_error.json",
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError) as exc_info:
        manager.get_token(force_refresh=True)

    message = str(exc_info.value)
    assert "KIS token request failed status=403" in message
    assert "app-key" not in message
    assert "app-secret" not in message


def test_token_manager_wraps_network_error_with_safe_message(tmp_path) -> None:
    manager = TokenManager(
        app_key="app-key",
        app_secret="app-secret",
        account="12345678",
        environment="paper",
        cache_path=tmp_path / "token_network_error.json",
        session=_ErrorSession(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError) as exc_info:
        manager.get_token(force_refresh=True)

    message = str(exc_info.value)
    assert "KIS token request failed network_error=ConnectionError" in message
    assert "app-key" not in message
    assert "app-secret" not in message
