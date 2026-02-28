from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from stockotter_small.cli import app
from stockotter_v2.universe import filter_market_snapshot

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def test_filter_market_snapshot_with_fixture_csv() -> None:
    result = filter_market_snapshot(
        FIXTURE_DIR / "kr_snapshot.sample.csv",
        min_price=5_000.0,
        max_price=100_000.0,
        min_value_traded_5d_avg=10_000_000_000.0,
        exclude_managed=True,
    )

    assert result.total_rows == 11
    assert result.eligible_tickers == ["005930", "678901"]
    assert result.excluded_counts == {
        "below_min_value_traded_5d_avg": 1,
        "invalid_price": 1,
        "invalid_value_traded_5d_avg": 1,
        "managed_stock": 1,
        "missing_is_managed": 1,
        "missing_price": 1,
        "missing_ticker": 1,
        "missing_value_traded_5d_avg": 1,
        "price_out_of_range": 1,
    }


def test_cli_universe_filter_writes_output_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "timezone": "Asia/Seoul",
                "sources": [{"name": "test", "type": "mock", "enabled": True}],
                "caching": {"enabled": True, "directory": "data/cache", "ttl_minutes": 60},
                "llm": {
                    "provider": "gemini",
                    "model": "gemini-2.0-flash-lite",
                    "temperature": 0.0,
                    "max_retries": 1,
                    "prompt_template": None,
                },
                "scoring": {"min_score": 0.0, "weights": {}},
                "universe": {
                    "market": "KR",
                    "tickers": [],
                    "max_candidates": 20,
                    "min_price": 5000,
                    "max_price": 100000,
                    "min_value_traded_5d_avg": 10000000000,
                    "exclude_managed": True,
                },
            }
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "eligible_tickers.txt"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "universe",
            "filter",
            "--market-snapshot",
            str(FIXTURE_DIR / "kr_snapshot.sample.csv"),
            "--output-path",
            str(output_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "eligible=2 total=11" in result.output
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").splitlines() == ["005930", "678901"]
