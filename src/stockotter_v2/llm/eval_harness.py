from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class EvalExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    direction: str
    horizon: str
    risk_flags: list[str] = Field(default_factory=list)


class EvalSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    news_id: str
    title: str
    snippet: str = ""
    raw_text: str = ""
    expected: EvalExpected
    recorded_output: dict[str, Any] | str | None = None
    mock_output: dict[str, Any] | str | None = None


class EvalPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    direction: str
    horizon: str
    risk_flags: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class EvalMetrics:
    sample_count: int
    evaluated_count: int
    error_count: int
    event_type_accuracy: float
    direction_accuracy: float
    horizon_accuracy: float
    risk_flags_precision: float
    risk_flags_recall: float


def load_eval_samples(dataset_glob: str) -> list[EvalSample]:
    paths = sorted(Path(path) for path in glob.glob(dataset_glob))
    if not paths:
        raise ValueError(f"No dataset files matched: {dataset_glob}")

    samples: list[EvalSample] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw_sample in _iter_raw_samples(payload):
            samples.append(EvalSample.model_validate(raw_sample))

    samples.sort(key=lambda sample: sample.news_id)
    return samples


def evaluate_samples(
    samples: list[EvalSample],
    *,
    mode: Literal["recorded", "mock"] = "recorded",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    evaluated = 0
    errors = 0
    event_type_correct = 0
    direction_correct = 0
    horizon_correct = 0
    tp = 0
    fp = 0
    fn = 0

    for sample in samples:
        row = {
            "news_id": sample.news_id,
            "title": sample.title,
            "expected": sample.expected.model_dump(mode="json"),
        }

        try:
            predicted = _predict_sample(sample=sample, mode=mode)
            row["predicted"] = predicted.model_dump(mode="json")
            evaluated += 1
        except Exception as exc:
            errors += 1
            row["error"] = str(exc)
            rows.append(row)
            continue

        event_type_match = _normalize(predicted.event_type) == _normalize(
            sample.expected.event_type
        )
        direction_match = _normalize(predicted.direction) == _normalize(
            sample.expected.direction
        )
        horizon_match = _normalize(predicted.horizon) == _normalize(sample.expected.horizon)

        expected_flags = {_normalize(flag) for flag in sample.expected.risk_flags if flag.strip()}
        predicted_flags = {
            flag
            for flag in (_normalize(flag) for flag in predicted.risk_flags)
            if flag
        }

        sample_tp = len(expected_flags & predicted_flags)
        sample_fp = len(predicted_flags - expected_flags)
        sample_fn = len(expected_flags - predicted_flags)
        tp += sample_tp
        fp += sample_fp
        fn += sample_fn

        if event_type_match:
            event_type_correct += 1
        if direction_match:
            direction_correct += 1
        if horizon_match:
            horizon_correct += 1

        row.update(
            {
                "event_type_match": event_type_match,
                "direction_match": direction_match,
                "horizon_match": horizon_match,
                "risk_flags_tp": sorted(expected_flags & predicted_flags),
                "risk_flags_fp": sorted(predicted_flags - expected_flags),
                "risk_flags_fn": sorted(expected_flags - predicted_flags),
            }
        )
        rows.append(row)

    metrics = _build_metrics(
        sample_count=len(samples),
        evaluated=evaluated,
        errors=errors,
        event_type_correct=event_type_correct,
        direction_correct=direction_correct,
        horizon_correct=horizon_correct,
        risk_tp=tp,
        risk_fp=fp,
        risk_fn=fn,
    )

    return {
        "mode": mode,
        "metrics": {
            "sample_count": metrics.sample_count,
            "evaluated_count": metrics.evaluated_count,
            "error_count": metrics.error_count,
            "event_type_accuracy": metrics.event_type_accuracy,
            "direction_accuracy": metrics.direction_accuracy,
            "horizon_accuracy": metrics.horizon_accuracy,
            "risk_flags_precision": metrics.risk_flags_precision,
            "risk_flags_recall": metrics.risk_flags_recall,
        },
        "samples": rows,
    }


def _build_metrics(
    *,
    sample_count: int,
    evaluated: int,
    errors: int,
    event_type_correct: int,
    direction_correct: int,
    horizon_correct: int,
    risk_tp: int,
    risk_fp: int,
    risk_fn: int,
) -> EvalMetrics:
    denominator = max(evaluated, 1)
    precision_denominator = risk_tp + risk_fp
    recall_denominator = risk_tp + risk_fn
    precision = (
        1.0 if precision_denominator == 0 else round(risk_tp / precision_denominator, 6)
    )
    recall = 1.0 if recall_denominator == 0 else round(risk_tp / recall_denominator, 6)

    return EvalMetrics(
        sample_count=sample_count,
        evaluated_count=evaluated,
        error_count=errors,
        event_type_accuracy=round(event_type_correct / denominator, 6),
        direction_accuracy=round(direction_correct / denominator, 6),
        horizon_accuracy=round(horizon_correct / denominator, 6),
        risk_flags_precision=precision,
        risk_flags_recall=recall,
    )


def _predict_sample(
    *,
    sample: EvalSample,
    mode: Literal["recorded", "mock"],
) -> EvalPrediction:
    if mode == "recorded":
        if sample.recorded_output is None:
            raise ValueError("recorded_output is missing")
        return _parse_prediction_payload(sample.recorded_output)
    if mode == "mock":
        if sample.mock_output is not None:
            return _parse_prediction_payload(sample.mock_output)
        return _mock_prediction(sample)
    raise ValueError(f"Unsupported mode: {mode}")


def _parse_prediction_payload(payload: dict[str, Any] | str) -> EvalPrediction:
    if isinstance(payload, dict):
        loaded = payload
    else:
        text = _FENCE_PATTERN.sub("", payload.strip())
        loaded = _load_json_object(text)
    return EvalPrediction.model_validate(
        {
            "event_type": str(loaded.get("event_type", "")),
            "direction": str(loaded.get("direction", "")),
            "horizon": str(loaded.get("horizon", "")),
            "risk_flags": _to_str_list(loaded.get("risk_flags", [])),
        }
    )


def _mock_prediction(sample: EvalSample) -> EvalPrediction:
    title = sample.title.lower()
    body = sample.raw_text.lower()
    text = f"{title} {body}"

    event_type = "demand"
    direction = "neutral"
    horizon = "short_term"
    risk_flags: list[str] = []

    if any(keyword in text for keyword in ["수주", "계약", "공급"]):
        event_type = "contract_win"
        direction = "positive"
        horizon = "mid_term"
    if any(keyword in text for keyword in ["수사", "조사", "징계"]):
        event_type = "investigation"
        direction = "negative"
        risk_flags.append("regulatory_risk")
    if any(keyword in text for keyword in ["소송", "법원", "판결"]):
        event_type = "litigation"
        direction = "negative"
        horizon = "long_term"
        risk_flags.append("litigation_risk")
    if any(keyword in text for keyword in ["전망", "가이던스", "실적"]):
        event_type = "earnings_guidance"
        direction = "positive"
    if any(keyword in text for keyword in ["혼조", "엇갈", "변동성"]):
        direction = "mixed"
        risk_flags.append("investor_sentiment_divergence")

    return EvalPrediction(
        event_type=event_type,
        direction=direction,
        horizon=horizon,
        risk_flags=sorted(set(risk_flags)),
    )


def _iter_raw_samples(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw_samples = payload.get("samples")
        if isinstance(raw_samples, list):
            return [item for item in raw_samples if isinstance(item, dict)]
        return [payload]
    raise ValueError("Dataset JSON must be an object or array.")


def _load_json_object(text: str) -> dict[str, Any]:
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError("Prediction JSON root must be an object.")
    return loaded


def _to_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for element in value:
        if isinstance(element, str) and element.strip():
            result.append(element.strip())
    return result


def _normalize(value: str) -> str:
    return value.strip().lower()
