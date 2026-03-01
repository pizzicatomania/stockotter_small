from __future__ import annotations

from stockotter_v2.llm.eval_harness import evaluate_samples, load_eval_samples


def test_p1_1_subset_regression_improves_event_type_accuracy() -> None:
    samples = load_eval_samples("data/llm_eval/p1_1_subset.json")

    baseline = evaluate_samples(samples, mode="recorded", recorded_field="baseline_output")
    improved = evaluate_samples(samples, mode="recorded", recorded_field="recorded_output")

    assert improved["metrics"]["event_type_accuracy"] > baseline["metrics"]["event_type_accuracy"]
    assert improved["metrics"]["direction_accuracy"] >= baseline["metrics"]["direction_accuracy"]
    assert improved["metrics"]["horizon_accuracy"] >= baseline["metrics"]["horizon_accuracy"]
