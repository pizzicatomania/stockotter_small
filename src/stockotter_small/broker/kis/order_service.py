from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from stockotter_small.broker.kis.client import KISClient, KISClientError
from stockotter_v2.schemas import BrokerOrder, OrderSide, OrderStatus, OrderType, now_in_seoul
from stockotter_v2.storage import Repository

_SENSITIVE_KEYS = {
    "authorization",
    "appkey",
    "appsecret",
    "cano",
    "acnt_prdt_cd",
}


class OrderService:
    """Paper-order service with dry-run default and local persistence."""

    def __init__(
        self,
        *,
        client: KISClient,
        repo: Repository,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.repo = repo
        self.now_fn = now_fn or now_in_seoul

    @classmethod
    def from_env(
        cls,
        *,
        db_path: Path,
        cache_path: Path | None = None,
        timeout_seconds: float = 10.0,
        refresh_margin_seconds: int = 60,
    ) -> OrderService:
        repo = Repository(db_path)
        client = KISClient.from_env(
            cache_path=cache_path,
            timeout_seconds=timeout_seconds,
            refresh_margin_seconds=refresh_margin_seconds,
        )
        return cls(client=client, repo=repo)

    def place_buy_market(
        self,
        ticker: str,
        cash_amount: int,
        *,
        confirm: bool = False,
    ) -> BrokerOrder:
        cash_value = _normalize_positive_int(cash_amount, field_name="cash_amount")
        quote = self.client.get_price(ticker)
        quantity = cash_value // quote.current_price
        if quantity < 1:
            raise ValueError(
                "cash_amount is too small for "
                f"ticker={quote.ticker} current_price={quote.current_price}"
            )
        note = f"estimated_qty_from_cash quote_price={quote.current_price}"
        return self._place_order(
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            ticker=quote.ticker,
            quantity=quantity,
            price=None,
            cash_amount=cash_value,
            confirm=confirm,
            note=note,
        )

    def place_buy_limit(
        self,
        ticker: str,
        qty: int,
        price: int,
        *,
        confirm: bool = False,
    ) -> BrokerOrder:
        return self._place_order(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            ticker=ticker,
            quantity=_normalize_positive_int(qty, field_name="qty"),
            price=_normalize_positive_int(price, field_name="price"),
            cash_amount=None,
            confirm=confirm,
            note="",
        )

    def place_sell_market(self, ticker: str, qty: int, *, confirm: bool = False) -> BrokerOrder:
        return self._place_order(
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            ticker=ticker,
            quantity=_normalize_positive_int(qty, field_name="qty"),
            price=None,
            cash_amount=None,
            confirm=confirm,
            note="",
        )

    def place_sell_limit(
        self,
        ticker: str,
        qty: int,
        price: int,
        *,
        confirm: bool = False,
    ) -> BrokerOrder:
        return self._place_order(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            ticker=ticker,
            quantity=_normalize_positive_int(qty, field_name="qty"),
            price=_normalize_positive_int(price, field_name="price"),
            cash_amount=None,
            confirm=confirm,
            note="",
        )

    def _place_order(
        self,
        *,
        side: OrderSide,
        order_type: OrderType,
        ticker: str,
        quantity: int,
        price: int | None,
        cash_amount: int | None,
        confirm: bool,
        note: str,
    ) -> BrokerOrder:
        ticker_code = _normalize_ticker_code(ticker)
        now = self.now_fn()
        order_id = _build_order_id(now=now, ticker=ticker_code)
        request_payload = self._build_request_payload(
            side=side,
            order_type=order_type,
            ticker=ticker_code,
            quantity=quantity,
            price=price,
        )
        base_order = BrokerOrder(
            order_id=order_id,
            broker="kis",
            environment=self.client.environment,
            ticker=ticker_code,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            cash_amount=cash_amount,
            status=OrderStatus.DRY_RUN,
            is_dry_run=not confirm,
            request_payload=_redact_payload(request_payload),
            response_payload={},
            note=note or "dry-run",
            created_at=now,
            updated_at=now,
        )

        if not confirm:
            self.repo.upsert_order(base_order)
            return base_order

        if self.client.environment != "paper":
            raise ValueError("actual order sending is only enabled in paper environment")

        pending = base_order.model_copy(
            update={
                "status": OrderStatus.PENDING_SUBMISSION,
                "is_dry_run": False,
                "note": note or "pending_submission",
            }
        )
        self.repo.upsert_order(pending)

        try:
            response = self.client.place_order(
                side=side.value,
                ticker=ticker_code,
                quantity=quantity,
                order_type=order_type.value,
                price=price,
            )
        except KISClientError as exc:
            rejected = pending.model_copy(
                update={
                    "status": OrderStatus.REJECTED,
                    "updated_at": self.now_fn(),
                    "submitted_at": self.now_fn(),
                    "response_payload": {
                        "error_type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                    "note": _join_note(note, str(exc)),
                }
            )
            self.repo.upsert_order(rejected)
            return rejected

        submitted = pending.model_copy(
            update={
                "status": OrderStatus.SUBMITTED,
                "updated_at": self.now_fn(),
                "submitted_at": self.now_fn(),
                "response_payload": _redact_payload(response.raw_payload),
                "external_order_id": response.order_no,
                "external_order_time": response.order_time,
                "note": _join_note(note, response.output_message or "submitted"),
            }
        )
        self.repo.upsert_order(submitted)
        return submitted

    def _build_request_payload(
        self,
        *,
        side: OrderSide,
        order_type: OrderType,
        ticker: str,
        quantity: int,
        price: int | None,
    ) -> dict[str, Any]:
        return {
            "method": "POST",
            "path": "/uapi/domestic-stock/v1/trading/order-cash",
            "environment": self.client.environment,
            "body": {
                "CANO": self.client._cano,
                "ACNT_PRDT_CD": self.client._acnt_prdt_cd,
                "PDNO": ticker,
                "ORD_DVSN": "01" if order_type is OrderType.MARKET else "00",
                "ORD_QTY": str(quantity),
                "ORD_UNPR": "0" if price is None else str(price),
            },
            "meta": {
                "side": side.value,
                "order_type": order_type.value,
            },
        }


def _normalize_positive_int(value: int, *, field_name: str) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if numeric <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return numeric


def _normalize_ticker_code(ticker: str) -> str:
    ticker_code = ticker.strip()
    if not ticker_code:
        raise ValueError("ticker must not be empty")
    if not ticker_code.isdigit():
        raise ValueError("ticker must be numeric")
    if len(ticker_code) not in {5, 6}:
        raise ValueError("ticker must be 5 or 6 digits")
    return ticker_code.zfill(6)


def _build_order_id(*, now: datetime, ticker: str) -> str:
    return f"order-{now.strftime('%Y%m%d%H%M%S%f')}-{ticker}"


def _join_note(*parts: str) -> str:
    normalized = [part.strip() for part in parts if part and part.strip()]
    return " | ".join(normalized)


def _redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if key.strip().lower() in _SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [_redact_payload(item) for item in payload]
    return payload
