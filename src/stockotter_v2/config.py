from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    enabled: bool = True
    url: str | None = None


class CachingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    directory: str = "data/cache"
    ttl_minutes: int = Field(default=60, ge=0)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_retries: int = Field(default=1, ge=0)


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_score: float = 0.0
    weights: dict[str, float] = Field(default_factory=dict)


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str = "KR"
    tickers: list[str] = Field(default_factory=list)
    max_candidates: int = Field(default=20, ge=1)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Seoul"
    sources: list[SourceConfig]
    caching: CachingConfig
    llm: LLMConfig
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
