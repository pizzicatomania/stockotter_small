"""Paper trading state machine models and rules."""

from .positions import (
    PaperEvent,
    PaperEventType,
    PaperPosition,
    PositionState,
    create_entry_position,
)
from .rules import (
    DEFAULT_SIDEWAYS_BAND_PCT,
    DEFAULT_SIDEWAYS_DAYS,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    DEFAULT_TRAILING_STOP_PCT,
    apply_eod_rules,
)

__all__ = [
    "DEFAULT_SIDEWAYS_BAND_PCT",
    "DEFAULT_SIDEWAYS_DAYS",
    "DEFAULT_STOP_LOSS_PCT",
    "DEFAULT_TAKE_PROFIT_PCT",
    "DEFAULT_TRAILING_STOP_PCT",
    "PaperEvent",
    "PaperEventType",
    "PaperPosition",
    "PositionState",
    "apply_eod_rules",
    "create_entry_position",
]
