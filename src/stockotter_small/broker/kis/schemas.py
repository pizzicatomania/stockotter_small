from __future__ import annotations

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
