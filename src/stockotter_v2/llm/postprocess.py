from __future__ import annotations

from typing import Any

from stockotter_v2.schemas import Direction, EventType, Horizon

_EVENT_TYPE_SYNONYMS = {
    "guidance": EventType.EARNINGS_GUIDANCE.value,
    "earnings": EventType.EARNINGS_GUIDANCE.value,
    "earningsguidance": EventType.EARNINGS_GUIDANCE.value,
    "contract": EventType.CONTRACT_WIN.value,
    "order": EventType.CONTRACT_WIN.value,
    "supply": EventType.SUPPLY_CHAIN.value,
    "supplychain": EventType.SUPPLY_CHAIN.value,
    "demand": EventType.DEMAND.value,
    "approval": EventType.REGULATORY_APPROVAL.value,
    "regulatoryapproval": EventType.REGULATORY_APPROVAL.value,
    "investigation": EventType.INVESTIGATION.value,
    "litigation": EventType.LITIGATION.value,
    "lawsuit": EventType.LITIGATION.value,
    "alreadydone": EventType.UNKNOWN.value,
    "unknown": EventType.UNKNOWN.value,
}

_DIRECTION_SYNONYMS = {
    "up": Direction.POSITIVE.value,
    "bullish": Direction.POSITIVE.value,
    "down": Direction.NEGATIVE.value,
    "bearish": Direction.NEGATIVE.value,
    "sideways": Direction.NEUTRAL.value,
    "flat": Direction.NEUTRAL.value,
    "volatile": Direction.MIXED.value,
    "mixed": Direction.MIXED.value,
}

_HORIZON_SYNONYMS = {
    "intraday": Horizon.INTRADAY.value,
    "1d": Horizon.ONE_TO_THREE_DAYS.value,
    "1_3d": Horizon.ONE_TO_THREE_DAYS.value,
    "1to3d": Horizon.ONE_TO_THREE_DAYS.value,
    "short": Horizon.SHORT_TERM.value,
    "shortterm": Horizon.SHORT_TERM.value,
    "mid": Horizon.MID_TERM.value,
    "midterm": Horizon.MID_TERM.value,
    "long": Horizon.LONG_TERM.value,
    "longterm": Horizon.LONG_TERM.value,
}

_TICKER_KEYS = {"ticker", "tickers", "tickers_mentioned"}


def normalize_structured_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for key in list(normalized):
        if key.lower() in _TICKER_KEYS:
            normalized.pop(key)

    normalized["event_type"] = _normalize_event_type(normalized.get("event_type"))
    normalized["direction"] = _normalize_direction(normalized.get("direction"))
    normalized["horizon"] = _normalize_horizon(normalized.get("horizon"))
    normalized["confidence"] = _clamp_confidence(normalized.get("confidence"))

    return normalized


def _normalize_event_type(value: Any) -> str:
    normalized = _normalize_text(value)
    if normalized in _EVENT_TYPE_SYNONYMS:
        return _EVENT_TYPE_SYNONYMS[normalized]
    if normalized in {member.value for member in EventType}:
        return normalized
    return EventType.UNKNOWN.value


def _normalize_direction(value: Any) -> str:
    normalized = _normalize_text(value)
    if normalized in _DIRECTION_SYNONYMS:
        return _DIRECTION_SYNONYMS[normalized]
    if normalized in {member.value for member in Direction}:
        return normalized
    return Direction.NEUTRAL.value


def _normalize_horizon(value: Any) -> str:
    normalized = _normalize_text(value)
    if normalized in _HORIZON_SYNONYMS:
        return _HORIZON_SYNONYMS[normalized]
    if normalized in {member.value for member in Horizon}:
        return normalized
    return Horizon.SHORT_TERM.value


def _clamp_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return (
        text.replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
        .replace(".", "")
    )
