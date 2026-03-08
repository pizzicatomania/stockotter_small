from __future__ import annotations

from datetime import datetime

import pytest

from stockotter_small.broker.kis import KISAPIError
from stockotter_small.broker.kis.order_service import OrderService
from stockotter_small.broker.kis.schemas import KISOrderResponse, KISPriceQuote
from stockotter_v2.schemas import OrderStatus
from stockotter_v2.storage import Repository


class _FakeClient:
    def __init__(self, *, environment: str = "paper") -> None:
        self.environment = environment
        self._cano = "12345678"
        self._acnt_prdt_cd = "01"

    def get_price(self, ticker: str) -> KISPriceQuote:
        return KISPriceQuote(
            ticker=ticker.zfill(6),
            name="삼성전자",
            current_price=70000,
            previous_close=69000,
            change=1000,
            change_rate=1.45,
        )

    def place_order(
        self,
        *,
        side: str,
        ticker: str,
        quantity: int,
        order_type: str,
        price: int | None = None,
    ) -> KISOrderResponse:
        _ = side, ticker, quantity, order_type, price
        return KISOrderResponse(
            status_code=200,
            output_code="0",
            output_message="주문 전송 완료 되었습니다.",
            order_org_no="91252",
            order_no="0001234567",
            order_time="103000",
            raw_payload={
                "rt_cd": "0",
                "msg1": "주문 전송 완료 되었습니다.",
                "output": {
                    "ODNO": "0001234567",
                    "ORD_TMD": "103000",
                },
            },
        )


class _RejectingClient(_FakeClient):
    def place_order(
        self,
        *,
        side: str,
        ticker: str,
        quantity: int,
        order_type: str,
        price: int | None = None,
    ) -> KISOrderResponse:
        _ = side, ticker, quantity, order_type, price
        raise KISAPIError("KIS API business error rt_cd=1 msg_cd=OPSQ0000 msg=reject")


def test_order_service_buy_market_dry_run_persists_redacted_order(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    service = OrderService(client=_FakeClient(), repo=repo)

    order = service.place_buy_market("005930", 150000, confirm=False)
    stored = repo.get_order(order.order_id)

    assert stored is not None
    assert stored.status == OrderStatus.DRY_RUN
    assert stored.is_dry_run is True
    assert stored.quantity == 2
    assert stored.cash_amount == 150000
    assert stored.request_payload["body"]["CANO"] == "[REDACTED]"
    assert stored.request_payload["body"]["ACNT_PRDT_CD"] == "[REDACTED]"


def test_order_service_confirmed_limit_order_updates_to_submitted(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    timestamps = iter(
        [
            datetime.fromisoformat("2026-03-08T09:00:00+09:00"),
            datetime.fromisoformat("2026-03-08T09:00:01+09:00"),
            datetime.fromisoformat("2026-03-08T09:00:02+09:00"),
        ]
    )
    service = OrderService(client=_FakeClient(), repo=repo, now_fn=lambda: next(timestamps))

    order = service.place_buy_limit("005930", qty=3, price=70000, confirm=True)
    stored = repo.get_order(order.order_id)

    assert stored is not None
    assert stored.status == OrderStatus.SUBMITTED
    assert stored.is_dry_run is False
    assert stored.external_order_id == "0001234567"
    assert stored.submitted_at is not None
    assert stored.updated_at > stored.created_at
    assert stored.response_payload["output"]["ODNO"] == "0001234567"


def test_order_service_confirmed_order_records_rejected_status(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    timestamps = iter(
        [
            datetime.fromisoformat("2026-03-08T09:10:00+09:00"),
            datetime.fromisoformat("2026-03-08T09:10:01+09:00"),
            datetime.fromisoformat("2026-03-08T09:10:02+09:00"),
        ]
    )
    service = OrderService(
        client=_RejectingClient(),
        repo=repo,
        now_fn=lambda: next(timestamps),
    )

    order = service.place_sell_market("005930", qty=2, confirm=True)
    stored = repo.get_order(order.order_id)

    assert stored is not None
    assert stored.status == OrderStatus.REJECTED
    assert stored.is_dry_run is False
    assert stored.submitted_at is not None
    assert stored.response_payload["error_type"] == "KISAPIError"
    assert "reject" in stored.note


def test_order_service_blocks_confirmed_orders_outside_paper_env(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    service = OrderService(client=_FakeClient(environment="live"), repo=repo)

    with pytest.raises(ValueError):
        service.place_buy_limit("005930", qty=1, price=70000, confirm=True)
