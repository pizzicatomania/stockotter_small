from __future__ import annotations

import json

import pytest
import requests

from stockotter_small.broker.kis.client import (
    KISAPIError,
    KISAuthError,
    KISClient,
    KISRateLimitError,
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
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise RuntimeError("no response queued")
        return self.responses.pop(0)


class _FakeTokenManager:
    def __init__(
        self,
        *,
        session: _FakeSession,
        environment: str = "paper",
        account: str = "12345678-01",
    ) -> None:
        self.environment = environment
        self.base_url = "https://example.kis.test"
        self.app_key = "app-key"
        self.app_secret = "app-secret"
        self.account = account
        self.session = session

    def build_bearer_token(self) -> str:
        return "Bearer fake-token"


class _BrokenTokenManager(_FakeTokenManager):
    def build_bearer_token(self) -> str:
        raise ValueError("token unavailable")


def test_kis_client_get_price_maps_payload_to_dto() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "rt_cd": "0",
                    "msg_cd": "MCA00000",
                    "msg1": "정상 처리되었습니다",
                    "output": {
                        "hts_kor_isnm": "삼성전자",
                        "stck_prpr": "70,000",
                        "stck_sdpr": "69,000",
                        "prdy_vrss": "1000",
                        "prdy_ctrt": "1.45",
                    },
                },
            )
        ]
    )
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    quote = client.get_price("005930")

    assert quote.ticker == "005930"
    assert quote.name == "삼성전자"
    assert quote.current_price == 70000
    assert quote.previous_close == 69000
    assert quote.change == 1000
    assert quote.change_rate == 1.45
    assert session.calls[0]["params"] == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
    }


def test_kis_client_get_balance_and_positions_maps_payload() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "rt_cd": "0",
                    "output2": [
                        {
                            "pchs_amt_smtl_amt": "1,000,000",
                            "tot_evlu_amt": "1,200,000",
                            "evlu_pfls_smtl_amt": "200,000",
                            "tot_evlu_pfls_rt": "20.0",
                            "dnca_tot_amt": "500,000",
                        }
                    ],
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "rt_cd": "0",
                    "output1": [
                        {
                            "pdno": "005930",
                            "prdt_name": "삼성전자",
                            "hldg_qty": "10",
                            "ord_psbl_qty": "8",
                            "pchs_avg_pric": "65000",
                            "prpr": "70000",
                            "evlu_amt": "700000",
                            "evlu_pfls_amt": "50000",
                            "evlu_pfls_rt": "7.69",
                        },
                        {
                            "pdno": "",
                            "prdt_name": "invalid-row",
                        },
                    ],
                },
            ),
        ]
    )
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    balance = client.get_balance()
    positions = client.get_positions()

    assert balance.total_purchase_amount == 1000000
    assert balance.total_eval_amount == 1200000
    assert balance.total_profit_loss_amount == 200000
    assert balance.total_profit_loss_rate == 20.0
    assert balance.cash_available == 500000

    assert len(positions) == 1
    position = positions[0]
    assert position.ticker == "005930"
    assert position.name == "삼성전자"
    assert position.quantity == 10
    assert position.orderable_quantity == 8
    assert position.average_buy_price == 65000
    assert position.current_price == 70000
    assert position.eval_amount == 700000
    assert position.profit_loss_amount == 50000
    assert position.profit_loss_rate == 7.69


def test_kis_client_maps_http_auth_error() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=401,
                payload={
                    "msg_cd": "EGW00001",
                    "msg1": "invalid token",
                },
            )
        ]
    )
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    with pytest.raises(KISAuthError) as exc_info:
        client.get_price("005930")

    assert exc_info.value.status_code == 401


def test_kis_client_maps_http_rate_limit_error() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=429,
                payload={
                    "msg_cd": "EGW00123",
                    "msg1": "too many requests",
                },
            )
        ]
    )
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    with pytest.raises(KISRateLimitError) as exc_info:
        client.get_price("005930")

    assert exc_info.value.status_code == 429


def test_kis_client_maps_business_rate_limit_error() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "rt_cd": "1",
                    "msg_cd": "EGW00201",
                    "msg1": "호출 횟수 초과",
                },
            )
        ]
    )
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    with pytest.raises(KISRateLimitError):
        client.get_price("005930")


def test_kis_client_maps_business_auth_error() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "rt_cd": "9",
                    "msg_cd": "EGW00001",
                    "msg1": "유효하지 않은 접근 토큰",
                },
            )
        ]
    )
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    with pytest.raises(KISAuthError):
        client.get_price("005930")


def test_kis_client_accepts_8_digit_account_and_defaults_product_code() -> None:
    session = _FakeSession(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "rt_cd": "0",
                    "output2": [
                        {
                            "pchs_amt_smtl_amt": "0",
                            "tot_evlu_amt": "0",
                            "evlu_pfls_smtl_amt": "0",
                            "tot_evlu_pfls_rt": "0.0",
                        }
                    ],
                },
            )
        ]
    )
    token_manager = _FakeTokenManager(session=session, account="12345678")
    client = KISClient(token_manager=token_manager, session=session)

    _ = client.get_balance()

    assert session.calls[0]["params"]["CANO"] == "12345678"
    assert session.calls[0]["params"]["ACNT_PRDT_CD"] == "01"


def test_kis_client_maps_token_build_error_to_auth_error() -> None:
    session = _FakeSession(responses=[])
    client = KISClient(token_manager=_BrokenTokenManager(session=session), session=session)

    with pytest.raises(KISAuthError):
        client.get_price("005930")


class _TransportErrorSession(_FakeSession):
    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeResponse:
        _ = url, params, headers, timeout
        raise requests.ConnectionError("network down")


def test_kis_client_maps_transport_error_to_api_error() -> None:
    session = _TransportErrorSession(responses=[])
    client = KISClient(token_manager=_FakeTokenManager(session=session), session=session)

    with pytest.raises(KISAPIError):
        client.get_price("005930")
