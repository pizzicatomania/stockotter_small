from __future__ import annotations

import json

from typer.testing import CliRunner

import stockotter_small.cli as cli_module


def test_cli_llm_eval_runs_offline_with_recorded_output(tmp_path) -> None:
    dataset_path = tmp_path / "eval_dataset.json"
    report_path = tmp_path / "eval_report.json"

    dataset_payload = [
        {
            "news_id": "eval-0001",
            "title": "삼성전자 실적 개선",
            "snippet": "실적 가이던스 상향",
            "raw_text": "기사 본문",
            "expected": {
                "event_type": "earnings_guidance",
                "direction": "positive",
                "horizon": "short_term",
                "risk_flags": [],
            },
            "recorded_output": {
                "event_type": "earnings_guidance",
                "direction": "positive",
                "horizon": "short_term",
                "risk_flags": [],
            },
        }
    ]
    dataset_path.write_text(
        json.dumps(dataset_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "llm-eval",
            "--dataset",
            str(dataset_path),
            "--report",
            str(report_path),
            "--mode",
            "recorded",
        ],
    )

    assert result.exit_code == 0
    assert "event_type_acc=1.000" in result.output
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["sample_count"] == 1
    assert payload["metrics"]["error_count"] == 0
