from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from typer.testing import CliRunner

import stockotter_small.cli as cli_module
from stockotter_v2.schemas import NewsItem, now_in_seoul


class QueueClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise RuntimeError("no queued responses")
        return self.responses.pop(0)


def _write_config(path: Path) -> None:
    path.write_text(
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
                "universe": {"market": "KR", "tickers": [], "max_candidates": 20},
            }
        ),
        encoding="utf-8",
    )


def _mock_news_item(*, ticker: str, offset_minutes: int) -> NewsItem:
    base_time = now_in_seoul() - timedelta(minutes=20)
    return NewsItem(
        id=f"news-{ticker}",
        source="mock",
        title=f"{ticker} 모의 뉴스",
        url=f"https://example.com/news/{ticker}",
        published_at=base_time + timedelta(minutes=offset_minutes),
        raw_text=f"{ticker} 관련 호재",
        tickers_mentioned=[ticker],
    )


def test_cli_run_pipeline_e2e_smoke_with_mocks(monkeypatch, tmp_path) -> None:
    tickers_file = tmp_path / "tickers.txt"
    tickers_file.write_text("005930\n000660\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    _write_config(config_path)
    db_path = tmp_path / "storage.db"
    json_out = tmp_path / "run-report.json"

    def _fake_fetch_recent_for_ticker(
        self, ticker: str, *, hours: int = 24
    ) -> list[NewsItem]:
        assert hours == 24
        if ticker == "005930":
            return [_mock_news_item(ticker=ticker, offset_minutes=1)]
        return [_mock_news_item(ticker=ticker, offset_minutes=2)]

    monkeypatch.setattr(
        cli_module.NaverNewsFetcher,
        "fetch_recent_for_ticker",
        _fake_fetch_recent_for_ticker,
    )

    client = QueueClient(
        [
            json.dumps(
                {
                    "event_type": "demand",
                    "direction": "positive",
                    "confidence": 0.9,
                    "horizon": "short_term",
                    "themes": ["semiconductor"],
                    "entities": ["Samsung Electronics"],
                    "risk_flags": [],
                }
            ),
            json.dumps(
                {
                    "event_type": "demand",
                    "direction": "positive",
                    "confidence": 0.6,
                    "horizon": "short_term",
                    "themes": ["memory"],
                    "entities": ["SK hynix"],
                    "risk_flags": ["volatility"],
                }
            ),
        ]
    )

    def _fake_from_env(cls, **_: object) -> QueueClient:
        return client

    monkeypatch.setattr(
        cli_module.GeminiClient,
        "from_env",
        classmethod(_fake_from_env),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--tickers-file",
            str(tickers_file),
            "--since-hours",
            "24",
            "--top",
            "2",
            "--db-path",
            str(db_path),
            "--config",
            str(config_path),
            "--sleep-seconds",
            "0",
            "--json-out",
            str(json_out),
        ],
    )

    assert result.exit_code == 0
    assert "rank" in result.output
    assert "005930" in result.output
    assert "000660" in result.output
    assert json_out.exists()

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["summary"]["error_count"] == 0
    assert [stage["status"] for stage in payload["stages"]] == [
        "ran",
        "ran",
        "ran",
        "ran",
    ]
    assert sorted(candidate["ticker"] for candidate in payload["candidates"]) == [
        "000660",
        "005930",
    ]
    assert len(payload["candidates"][0]["headlines"]) == 1


def test_cli_run_pipeline_skips_stages_when_data_exists(monkeypatch, tmp_path) -> None:
    tickers_file = tmp_path / "tickers.txt"
    tickers_file.write_text("005930\n000660\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    _write_config(config_path)
    db_path = tmp_path / "storage.db"
    first_json_out = tmp_path / "run-first.json"
    second_json_out = tmp_path / "run-second.json"

    fetch_calls = {"count": 0}

    def _fake_fetch_recent_for_ticker(
        self, ticker: str, *, hours: int = 24
    ) -> list[NewsItem]:
        fetch_calls["count"] += 1
        assert hours == 24
        if ticker == "005930":
            return [_mock_news_item(ticker=ticker, offset_minutes=1)]
        return [_mock_news_item(ticker=ticker, offset_minutes=2)]

    monkeypatch.setattr(
        cli_module.NaverNewsFetcher,
        "fetch_recent_for_ticker",
        _fake_fetch_recent_for_ticker,
    )

    def _fake_from_env(cls, **_: object) -> QueueClient:
        return QueueClient(
            [
                json.dumps(
                    {
                        "event_type": "demand",
                        "direction": "positive",
                        "confidence": 0.7,
                        "horizon": "short_term",
                        "themes": ["semiconductor"],
                        "entities": ["Samsung Electronics"],
                        "risk_flags": [],
                    }
                ),
                json.dumps(
                    {
                        "event_type": "demand",
                        "direction": "positive",
                        "confidence": 0.6,
                        "horizon": "short_term",
                        "themes": ["memory"],
                        "entities": ["SK hynix"],
                        "risk_flags": [],
                    }
                ),
            ]
        )

    monkeypatch.setattr(
        cli_module.GeminiClient,
        "from_env",
        classmethod(_fake_from_env),
    )

    runner = CliRunner()
    first = runner.invoke(
        cli_module.app,
        [
            "run",
            "--tickers-file",
            str(tickers_file),
            "--since-hours",
            "24",
            "--top",
            "2",
            "--db-path",
            str(db_path),
            "--config",
            str(config_path),
            "--sleep-seconds",
            "0",
            "--json-out",
            str(first_json_out),
        ],
    )
    assert first.exit_code == 0
    assert fetch_calls["count"] == 2

    second = runner.invoke(
        cli_module.app,
        [
            "run",
            "--tickers-file",
            str(tickers_file),
            "--since-hours",
            "24",
            "--top",
            "2",
            "--db-path",
            str(db_path),
            "--config",
            str(config_path),
            "--sleep-seconds",
            "0",
            "--json-out",
            str(second_json_out),
        ],
    )
    assert second.exit_code == 0
    assert fetch_calls["count"] == 2

    payload = json.loads(second_json_out.read_text(encoding="utf-8"))
    assert [stage["status"] for stage in payload["stages"]] == [
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
