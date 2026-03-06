from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from typer.testing import CliRunner

import stockotter_small.cli as cli_module


@dataclass
class _FakeToken:
    access_token: str
    expires_at: datetime


class _FakeTokenManager:
    def __init__(self) -> None:
        self.environment = "paper"

    def get_token(self) -> _FakeToken:
        return _FakeToken(
            access_token="token-ignored",
            expires_at=datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
        )


class _FakeKISClient:
    def __init__(self) -> None:
        self.environment = "paper"
        self.cache_path = "data/cache/kis/token_paper.json"
        self.token_manager = _FakeTokenManager()

    def auth_test_quote(self, *, ticker: str = "005930") -> object:
        _ = ticker

        class _Result:
            status_code = 200
            output_code = "0"
            output_message = "정상처리 되었습니다"
            stock_name = "삼성전자"
            current_price = "70000"

        return _Result()


class _Fake404KISClient(_FakeKISClient):
    def auth_test_quote(self, *, ticker: str = "005930") -> object:
        _ = ticker

        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError("not found")
        error.response = response
        raise error


def test_cli_kis_auth_test_success(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.KISClient,
        "from_env",
        staticmethod(lambda cache_path=None: _FakeKISClient()),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["kis", "auth-test", "--ticker", "005930"])

    assert result.exit_code == 0
    assert "token env=paper" in result.output
    assert "harmless_call=ok" in result.output


def test_cli_kis_auth_test_skips_when_endpoint_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.KISClient,
        "from_env",
        staticmethod(lambda cache_path=None: _Fake404KISClient()),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["kis", "auth-test"])

    assert result.exit_code == 0
    assert "harmless_call=skipped status=404" in result.output
