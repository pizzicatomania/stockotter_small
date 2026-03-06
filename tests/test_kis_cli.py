from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from typer.testing import CliRunner

import stockotter_small.cli as cli_module
from stockotter_small.broker.kis import (
    KISAccountBalance,
    KISAuthError,
    KISPosition,
    KISPriceQuote,
)


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

    def get_price(self, ticker: str) -> KISPriceQuote:
        _ = ticker
        return KISPriceQuote(
            ticker="005930",
            name="삼성전자",
            current_price=70000,
            previous_close=69000,
            change=1000,
            change_rate=1.45,
        )

    def get_balance(self) -> KISAccountBalance:
        return KISAccountBalance(
            total_purchase_amount=1000000,
            total_eval_amount=1100000,
            total_profit_loss_amount=100000,
            total_profit_loss_rate=10.0,
            cash_available=500000,
        )

    def get_positions(self) -> list[KISPosition]:
        return [
            KISPosition(
                ticker="005930",
                name="삼성전자",
                quantity=3,
                current_price=70000,
                profit_loss_amount=30000,
                profit_loss_rate=16.6,
            )
        ]


class _Fake404KISClient(_FakeKISClient):
    def auth_test_quote(self, *, ticker: str = "005930") -> object:
        _ = ticker

        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError("not found")
        error.response = response
        raise error


class _FakeAuthErrorKISClient(_FakeKISClient):
    def get_price(self, ticker: str) -> KISPriceQuote:
        _ = ticker
        raise KISAuthError("KIS auth error status=401 msg_cd=EGW00001 msg=invalid token")


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


def test_cli_kis_price_success(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.KISClient,
        "from_env",
        staticmethod(lambda cache_path=None: _FakeKISClient()),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["kis", "price", "005930"])

    assert result.exit_code == 0
    assert "price ticker=005930" in result.output
    assert "current=70000" in result.output


def test_cli_kis_positions_success(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.KISClient,
        "from_env",
        staticmethod(lambda cache_path=None: _FakeKISClient()),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["kis", "positions"])

    assert result.exit_code == 0
    assert "balance purchase=1000000 eval=1100000" in result.output
    assert "ticker | name" in result.output
    assert "005930 | 삼성전자" in result.output


def test_cli_kis_price_handles_auth_error(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.KISClient,
        "from_env",
        staticmethod(lambda cache_path=None: _FakeAuthErrorKISClient()),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["kis", "price", "005930"])

    assert result.exit_code == 1
    assert "kis_error=auth" in result.output
