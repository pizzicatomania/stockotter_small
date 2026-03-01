from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

SEOUL_TZ = ZoneInfo("Asia/Seoul")
TModel = TypeVar("TModel", bound=BaseModel)


def now_in_seoul() -> datetime:
    return datetime.now(tz=SEOUL_TZ)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=SEOUL_TZ)
    return value.astimezone(SEOUL_TZ)


class DTOBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventType(StrEnum):
    EARNINGS_GUIDANCE = "earnings_guidance"
    CONTRACT_WIN = "contract_win"
    SUPPLY_CHAIN = "supply_chain"
    DEMAND = "demand"
    REGULATORY_APPROVAL = "regulatory_approval"
    INVESTIGATION = "investigation"
    LITIGATION = "litigation"
    UNKNOWN = "UNKNOWN"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class Horizon(StrEnum):
    INTRADAY = "intraday"
    ONE_TO_THREE_DAYS = "1_3d"
    SHORT_TERM = "short_term"
    MID_TERM = "mid_term"
    LONG_TERM = "long_term"


class NewsItem(DTOBase):
    id: str
    source: str
    title: str
    url: str
    published_at: datetime
    raw_text: str
    tickers_mentioned: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=now_in_seoul)

    @field_validator("published_at", "fetched_at", mode="after")
    @classmethod
    def validate_datetime_fields(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class StructuredEvent(DTOBase):
    news_id: str
    event_type: EventType
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    horizon: Horizon
    themes: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: str) -> str:
        normalized = _normalize_enum_key(value)
        event_type_map = {
            "earningsguidance": EventType.EARNINGS_GUIDANCE.value,
            "guidance": EventType.EARNINGS_GUIDANCE.value,
            "earnings": EventType.EARNINGS_GUIDANCE.value,
            "contractwin": EventType.CONTRACT_WIN.value,
            "contract": EventType.CONTRACT_WIN.value,
            "order": EventType.CONTRACT_WIN.value,
            "supplychain": EventType.SUPPLY_CHAIN.value,
            "supply": EventType.SUPPLY_CHAIN.value,
            "demand": EventType.DEMAND.value,
            "regulatoryapproval": EventType.REGULATORY_APPROVAL.value,
            "approval": EventType.REGULATORY_APPROVAL.value,
            "investigation": EventType.INVESTIGATION.value,
            "litigation": EventType.LITIGATION.value,
            "lawsuit": EventType.LITIGATION.value,
            "alreadydone": EventType.UNKNOWN.value,
            "unknown": EventType.UNKNOWN.value,
        }
        return event_type_map.get(normalized, EventType.UNKNOWN.value)

    @field_validator("direction", mode="before")
    @classmethod
    def normalize_direction(cls, value: str) -> str:
        normalized = _normalize_enum_key(value)
        direction_map = {
            "positive": Direction.POSITIVE.value,
            "up": Direction.POSITIVE.value,
            "bullish": Direction.POSITIVE.value,
            "negative": Direction.NEGATIVE.value,
            "down": Direction.NEGATIVE.value,
            "bearish": Direction.NEGATIVE.value,
            "neutral": Direction.NEUTRAL.value,
            "flat": Direction.NEUTRAL.value,
            "sideways": Direction.NEUTRAL.value,
            "mixed": Direction.MIXED.value,
            "volatile": Direction.MIXED.value,
        }
        return direction_map.get(normalized, Direction.NEUTRAL.value)

    @field_validator("horizon", mode="before")
    @classmethod
    def normalize_horizon(cls, value: str) -> str:
        normalized = _normalize_enum_key(value)
        horizon_map = {
            "intraday": Horizon.INTRADAY.value,
            "13d": Horizon.ONE_TO_THREE_DAYS.value,
            "1to3d": Horizon.ONE_TO_THREE_DAYS.value,
            "shortterm": Horizon.SHORT_TERM.value,
            "short": Horizon.SHORT_TERM.value,
            "midterm": Horizon.MID_TERM.value,
            "mid": Horizon.MID_TERM.value,
            "longterm": Horizon.LONG_TERM.value,
            "long": Horizon.LONG_TERM.value,
        }
        return horizon_map.get(normalized, Horizon.SHORT_TERM.value)

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        if numeric < 0.0:
            return 0.0
        if numeric > 1.0:
            return 1.0
        return numeric


class Cluster(DTOBase):
    cluster_id: str
    representative_news_id: str
    member_news_ids: list[str] = Field(default_factory=list)
    summary: str


class Candidate(DTOBase):
    ticker: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    supporting_news_ids: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


def json_schema_for(model_cls: type[TModel]) -> dict[str, Any]:
    return model_cls.model_json_schema()


def validate_json(model_cls: type[TModel], payload: str | bytes | bytearray) -> TModel:
    return model_cls.model_validate_json(payload)


def _normalize_enum_key(value: object) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
        .replace(".", "")
    )
