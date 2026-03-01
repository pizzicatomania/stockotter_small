from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    enabled: bool = True
    url: str | None = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> SourceConfig:
        if self.enabled and self.type.lower() == "rss" and not (self.url or "").strip():
            raise ValueError("sources[].url is required when type is rss and enabled is true")
        return self


class CachingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    directory: str = "data/cache"
    ttl_minutes: int = Field(default=60, ge=0)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str = "gemini-2.5-flash"
    fallback_model: str = "gemini-2.5-flash-lite"
    api_key_env: str = "GEMINI_API_KEY"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_retries: int = Field(default=1, ge=0)
    prompt_template: str | None = None

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("llm.api_key_env must not be empty")
        return normalized

    @field_validator("model", "fallback_model")
    @classmethod
    def validate_model_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("llm model fields must not be empty")
        return normalized


class NewsQualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    ticker_map_path: str = "data/ticker_map.json"
    noise_patterns: list[str] = Field(
        default_factory=lambda: [
            "광고",
            "협찬",
            "리포트 전문",
            "주가전망",
            "전문가 추천",
            "오늘의 추천주",
        ]
    )
    min_title_length: int = Field(default=10, ge=1)
    drop_duplicate_titles: bool = True

    @field_validator("ticker_map_path")
    @classmethod
    def validate_ticker_map_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("news_quality.ticker_map_path must not be empty")
        return normalized


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_score: float = 0.0
    weights: dict[str, float] = Field(default_factory=dict)


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str = "KR"
    tickers: list[str] = Field(default_factory=list)
    max_candidates: int = Field(default=20, ge=1)
    min_price: float = Field(default=1_000.0, ge=0.0)
    max_price: float = Field(default=100_000.0, ge=0.0)
    min_value_traded_5d_avg: float = Field(default=10_000_000_000.0, ge=0.0)
    exclude_managed: bool = True

    @model_validator(mode="after")
    def validate_price_bounds(self) -> UniverseConfig:
        if self.max_price < self.min_price:
            raise ValueError("universe.max_price must be greater than or equal to min_price")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Seoul"
    sources: list[SourceConfig]
    caching: CachingConfig
    llm: LLMConfig
    news_quality: NewsQualityConfig = Field(default_factory=NewsQualityConfig)
    scoring: ScoringConfig
    universe: UniverseConfig

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value


def load_config(path: str | Path) -> AppConfig:
    raw = Path(path).read_text(encoding="utf-8")
    payload = _parse_yaml_or_json(raw)
    try:
        return AppConfig.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration: {exc}") from exc


def _parse_yaml_or_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = _parse_yaml(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Configuration root must be an object.")
    return parsed


def _parse_yaml(raw: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise ValueError(
            "YAML parsing requires PyYAML. Use JSON-compatible YAML or install pyyaml."
        ) from exc

    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Configuration root must be an object.")
    return parsed
