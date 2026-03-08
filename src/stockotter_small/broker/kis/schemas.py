from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class KISPriceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    name: str
    current_price: int
    previous_close: int | None = None
    change: int | None = None
    change_rate: float | None = None

    @field_validator("ticker", "name", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("required text field is empty")
        return text

    @field_validator("current_price", "previous_close", "change", mode="before")
    @classmethod
    def _parse_int_like(cls, value: object) -> int | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError as exc:
            raise ValueError(f"invalid int-like value: {value}") from exc

    @field_validator("change_rate", mode="before")
    @classmethod
    def _parse_float_like(cls, value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"invalid float-like value: {value}") from exc


class KISAccountBalance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_purchase_amount: int
    total_eval_amount: int
    total_profit_loss_amount: int
    total_profit_loss_rate: float
    cash_available: int | None = None

    @field_validator(
        "total_purchase_amount",
        "total_eval_amount",
        "total_profit_loss_amount",
        "cash_available",
        mode="before",
    )
    @classmethod
    def _parse_int_like(cls, value: object) -> int | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError as exc:
            raise ValueError(f"invalid int-like value: {value}") from exc

    @field_validator("total_profit_loss_rate", mode="before")
    @classmethod
    def _parse_float_like(cls, value: object) -> float:
        text = str(value).strip().replace(",", "")
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"invalid float-like value: {value}") from exc


class KISPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str
    name: str
    quantity: int
    orderable_quantity: int | None = None
    average_buy_price: int | None = None
    current_price: int | None = None
    eval_amount: int | None = None
    profit_loss_amount: int | None = None
    profit_loss_rate: float | None = None

    @field_validator("ticker", "name", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator(
        "quantity",
        "orderable_quantity",
        "average_buy_price",
        "current_price",
        "eval_amount",
        "profit_loss_amount",
        mode="before",
    )
    @classmethod
    def _parse_int_like(cls, value: object) -> int | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError as exc:
            raise ValueError(f"invalid int-like value: {value}") from exc

    @field_validator("profit_loss_rate", mode="before")
    @classmethod
    def _parse_float_like(cls, value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"invalid float-like value: {value}") from exc


class KISOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_code: int
    output_code: str | None = None
    output_message: str | None = None
    order_org_no: str | None = None
    order_no: str | None = None
    order_time: str | None = None
    raw_payload: dict[str, Any]

    @field_validator("status_code", mode="before")
    @classmethod
    def _parse_status_code(cls, value: object) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid status code: {value}") from exc

    @field_validator(
        "output_code",
        "output_message",
        "order_org_no",
        "order_no",
        "order_time",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text

    @field_validator("raw_payload", mode="before")
    @classmethod
    def _validate_raw_payload(cls, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("raw_payload must be a dict")
        return value
