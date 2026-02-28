from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_WEIGHT_OVERRIDES: dict[str, float] = {
    "confidence_multiplier": 1.8,
    "risk_flag_penalty": -1.2,
    "severe_risk_flag_penalty": -2.8,
    "unknown_event_type_weight": 0.0,
    "unknown_horizon_weight": 0.0,
    "direction_positive": 1.0,
    "direction_negative": -1.0,
    "direction_neutral": 0.0,
    "direction_mixed": -0.2,
    "horizon_intraday": 0.5,
    "horizon_1_3d": 0.4,
    "horizon_short_term": 0.35,
    "horizon_mid_term": 0.15,
    "horizon_long": 0.05,
    "horizon_long_term": 0.05,
    "event_type_earnings_guidance": 0.4,
    "event_type_contract_win": 0.45,
    "event_type_supply_chain": 0.25,
    "event_type_demand": 0.2,
    "event_type_regulatory_approval": 0.35,
    "event_type_investigation": -0.7,
    "event_type_litigation": -0.6,
}

DEFAULT_SEVERE_RISK_KEYWORDS = frozenset(
    {
        "rightsissue",
        "paidincapitalincrease",
        "capitalincrease",
        "convertiblebond",
        "cb",
        "warrant",
        "embezzlement",
        "fraud",
        "횡령",
        "배임",
        "증자",
        "전환사채",
    }
)


@dataclass(frozen=True)
class ScoreWeights:
    confidence_multiplier: float
    risk_flag_penalty: float
    severe_risk_flag_penalty: float
    unknown_event_type_weight: float
    unknown_horizon_weight: float
    direction_weights: dict[str, float]
    event_type_weights: dict[str, float]
    horizon_weights: dict[str, float]
    severe_risk_keywords: frozenset[str]

    def direction_weight(self, direction: str) -> float:
        return self.direction_weights.get(_normalize_key(direction), 0.0)

    def event_type_weight(self, event_type: str) -> float:
        return self.event_type_weights.get(
            _normalize_key(event_type),
            self.unknown_event_type_weight,
        )

    def horizon_weight(self, horizon: str) -> float:
        return self.horizon_weights.get(
            _normalize_key(horizon),
            self.unknown_horizon_weight,
        )

    def risk_penalty(self, risk_flag: str) -> float:
        normalized = _normalize_key(risk_flag)
        for keyword in self.severe_risk_keywords:
            if keyword and keyword in normalized:
                return self.severe_risk_flag_penalty
        return self.risk_flag_penalty


def build_score_weights(overrides: Mapping[str, float] | None = None) -> ScoreWeights:
    merged = dict(DEFAULT_WEIGHT_OVERRIDES)
    if overrides:
        merged.update(overrides)

    direction_weights = {
        _normalize_key(name.removeprefix("direction_")): value
        for name, value in merged.items()
        if name.startswith("direction_")
    }
    event_type_weights = {
        _normalize_key(name.removeprefix("event_type_")): value
        for name, value in merged.items()
        if name.startswith("event_type_")
    }
    horizon_weights = {
        _normalize_key(name.removeprefix("horizon_")): value
        for name, value in merged.items()
        if name.startswith("horizon_")
    }

    severe_keywords = {_normalize_key(value) for value in DEFAULT_SEVERE_RISK_KEYWORDS}

    return ScoreWeights(
        confidence_multiplier=merged["confidence_multiplier"],
        risk_flag_penalty=merged["risk_flag_penalty"],
        severe_risk_flag_penalty=merged["severe_risk_flag_penalty"],
        unknown_event_type_weight=merged["unknown_event_type_weight"],
        unknown_horizon_weight=merged["unknown_horizon_weight"],
        direction_weights=direction_weights,
        event_type_weights=event_type_weights,
        horizon_weights=horizon_weights,
        severe_risk_keywords=frozenset(severe_keywords),
    )


def _normalize_key(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace("/", "")
    )
