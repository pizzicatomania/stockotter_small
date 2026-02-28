from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, model_validator

from stockotter_v2.schemas import DTOBase, now_in_seoul


class PositionState(StrEnum):
    ENTRY = "ENTRY"
    PARTIAL_TP = "PARTIAL_TP"
    TRAILING = "TRAILING"
    EXITED = "EXITED"


class PaperEventType(StrEnum):
    PARTIAL_TP = "PARTIAL_TP"
    TRAILING_STOP = "TRAILING_STOP"
    STOP_LOSS = "STOP_LOSS"
    SIDEWAYS_EXIT = "SIDEWAYS_EXIT"


class PaperPosition(DTOBase):
    ticker: str
    state: PositionState
    entry_price: float = Field(gt=0.0)
    qty_total: float = Field(gt=0.0)
    qty_remaining: float = Field(ge=0.0)
    entry_date: date
    last_close: float = Field(gt=0.0)
    updated_at: datetime = Field(default_factory=now_in_seoul)
    highest_close_since_tp: float | None = Field(default=None, gt=0.0)
    exit_price: float | None = Field(default=None, gt=0.0)
    exit_date: date | None = None
    sideways_days: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_consistency(self) -> PaperPosition:
        if self.qty_remaining > self.qty_total:
            raise ValueError("qty_remaining must be <= qty_total")

        if self.state == PositionState.EXITED:
            if self.qty_remaining != 0.0:
                raise ValueError("EXITED state requires qty_remaining == 0")
            if self.exit_price is None or self.exit_date is None:
                raise ValueError("EXITED state requires exit_price and exit_date")
        return self


class PaperEvent(DTOBase):
    ticker: str
    event_date: date
    event_type: PaperEventType
    price: float = Field(gt=0.0)
    quantity: float = Field(gt=0.0)
    state_before: PositionState
    state_after: PositionState
    note: str = ""


def create_entry_position(
    *,
    ticker: str,
    entry_price: float,
    entry_date: date,
    qty_total: float = 1.0,
) -> PaperPosition:
    return PaperPosition(
        ticker=ticker,
        state=PositionState.ENTRY,
        entry_price=entry_price,
        qty_total=qty_total,
        qty_remaining=qty_total,
        entry_date=entry_date,
        last_close=entry_price,
        updated_at=now_in_seoul(),
    )
