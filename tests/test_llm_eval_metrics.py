from __future__ import annotations

from stockotter_v2.llm.eval_harness import EvalSample, evaluate_samples


def test_evaluate_samples_calculates_metrics_with_errors() -> None:
    samples = [
        EvalSample.model_validate(
            {
                "news_id": "eval-001",
                "title": "A",
                "snippet": "A",
                "raw_text": "A",
                "expected": {
                    "event_type": "demand",
                    "direction": "positive",
                    "horizon": "short_term",
                    "risk_flags": ["macro_risk", "volatility"],
                },
                "recorded_output": {
                    "event_type": "demand",
                    "direction": "positive",
                    "horizon": "short_term",
                    "risk_flags": ["macro_risk", "volatility"],
                },
            }
        ),
        EvalSample.model_validate(
            {
                "news_id": "eval-002",
                "title": "B",
                "snippet": "B",
                "raw_text": "B",
                "expected": {
                    "event_type": "investigation",
                    "direction": "negative",
                    "horizon": "long_term",
                    "risk_flags": ["regulatory_risk"],
                },
                "recorded_output": {
                    "event_type": "contract_win",
                    "direction": "negative",
                    "horizon": "short_term",
                    "risk_flags": ["regulatory_risk", "extra_risk"],
                },
            }
        ),
        EvalSample.model_validate(
            {
                "news_id": "eval-003",
                "title": "C",
                "snippet": "C",
                "raw_text": "C",
                "expected": {
                    "event_type": "demand",
                    "direction": "neutral",
                    "horizon": "short_term",
                    "risk_flags": [],
                },
            }
        ),
    ]

    report = evaluate_samples(samples, mode="recorded")
    metrics = report["metrics"]

    assert metrics["sample_count"] == 3
    assert metrics["evaluated_count"] == 2
    assert metrics["error_count"] == 1
    assert metrics["event_type_accuracy"] == 0.5
    assert metrics["direction_accuracy"] == 1.0
    assert metrics["horizon_accuracy"] == 0.5
    assert metrics["risk_flags_precision"] == 0.75
    assert metrics["risk_flags_recall"] == 1.0

    errored = [row for row in report["samples"] if row.get("error")]
    assert len(errored) == 1
    assert errored[0]["news_id"] == "eval-003"
