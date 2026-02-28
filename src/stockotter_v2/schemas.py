from __future__ import annotations

from datetime import datetime
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
    event_type: str
    direction: str
    confidence: float = Field(ge=0.0, le=1.0)
    horizon: str
    themes: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


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
