from __future__ import annotations

from datetime import date

from stockotter_v2.schemas import now_in_seoul

from .positions import (
    PaperEvent,
    PaperEventType,
    PaperPosition,
    PositionState,
)

DEFAULT_TAKE_PROFIT_PCT = 0.08
DEFAULT_TRAILING_STOP_PCT = 0.06
DEFAULT_STOP_LOSS_PCT = 0.07
DEFAULT_SIDEWAYS_DAYS = 3
DEFAULT_SIDEWAYS_BAND_PCT = 0.01


def apply_eod_rules(
    position: PaperPosition,
    *,
    close: float,
    asof: date,
    take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
    enable_sideways_exit: bool = True,
    sideways_days: int = DEFAULT_SIDEWAYS_DAYS,
    sideways_band_pct: float = DEFAULT_SIDEWAYS_BAND_PCT,
) -> tuple[PaperPosition, list[PaperEvent]]:
    if close <= 0.0:
        raise ValueError("close must be > 0")

    if asof < position.entry_date:
        raise ValueError("asof must be >= entry_date")

    updated = position.model_copy(deep=True)
    updated.last_close = close
    updated.updated_at = now_in_seoul()
    events: list[PaperEvent] = []

    if updated.state == PositionState.EXITED:
        return updated, events

    stop_loss_price = updated.entry_price * (1.0 - stop_loss_pct)
    if close <= stop_loss_price:
        qty_to_sell = updated.qty_remaining
        previous_state = updated.state
        updated.state = PositionState.EXITED
        updated.qty_remaining = 0.0
        updated.exit_price = close
        updated.exit_date = asof
        events.append(
            PaperEvent(
                ticker=updated.ticker,
                event_date=asof,
                event_type=PaperEventType.STOP_LOSS,
                price=close,
                quantity=qty_to_sell,
                state_before=previous_state,
                state_after=PositionState.EXITED,
                note=f"entry={updated.entry_price:.4f} stop={stop_loss_price:.4f}",
            )
        )
        return updated, events

    take_profit_price = updated.entry_price * (1.0 + take_profit_pct)
    if updated.state == PositionState.ENTRY and close >= take_profit_price:
        qty_to_sell = updated.qty_total * 0.5
        updated.qty_remaining = max(updated.qty_remaining - qty_to_sell, 0.0)
        updated.state = PositionState.PARTIAL_TP
        updated.highest_close_since_tp = close
        updated.sideways_days = 0
        events.append(
            PaperEvent(
                ticker=updated.ticker,
                event_date=asof,
                event_type=PaperEventType.PARTIAL_TP,
                price=close,
                quantity=qty_to_sell,
                state_before=PositionState.ENTRY,
                state_after=PositionState.PARTIAL_TP,
                note=f"entry={updated.entry_price:.4f} tp={take_profit_price:.4f}",
            )
        )
        return updated, events

    if updated.state == PositionState.PARTIAL_TP:
        updated.state = PositionState.TRAILING

    if updated.state == PositionState.TRAILING:
        highest = updated.highest_close_since_tp or close
        updated.highest_close_since_tp = max(highest, close)
        trailing_price = updated.highest_close_since_tp * (1.0 - trailing_stop_pct)
        if close <= trailing_price:
            qty_to_sell = updated.qty_remaining
            updated.state = PositionState.EXITED
            updated.qty_remaining = 0.0
            updated.exit_price = close
            updated.exit_date = asof
            events.append(
                PaperEvent(
                    ticker=updated.ticker,
                    event_date=asof,
                    event_type=PaperEventType.TRAILING_STOP,
                    price=close,
                    quantity=qty_to_sell,
                    state_before=PositionState.TRAILING,
                    state_after=PositionState.EXITED,
                    note=(
                        f"highest={updated.highest_close_since_tp:.4f} "
                        f"stop={trailing_price:.4f}"
                    ),
                )
            )
            return updated, events

    if enable_sideways_exit and updated.state == PositionState.ENTRY:
        lower = updated.entry_price * (1.0 - sideways_band_pct)
        upper = updated.entry_price * (1.0 + sideways_band_pct)
        if lower <= close <= upper:
            updated.sideways_days += 1
        else:
            updated.sideways_days = 0

        if updated.sideways_days >= sideways_days:
            qty_to_sell = updated.qty_remaining
            updated.state = PositionState.EXITED
            updated.qty_remaining = 0.0
            updated.exit_price = close
            updated.exit_date = asof
            events.append(
                PaperEvent(
                    ticker=updated.ticker,
                    event_date=asof,
                    event_type=PaperEventType.SIDEWAYS_EXIT,
                    price=close,
                    quantity=qty_to_sell,
                    state_before=PositionState.ENTRY,
                    state_after=PositionState.EXITED,
                    note=f"range=[{lower:.4f}, {upper:.4f}] days={updated.sideways_days}",
                )
            )
            return updated, events

    return updated, events
