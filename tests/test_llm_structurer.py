from __future__ import annotations

import json
from datetime import timedelta

from typer.testing import CliRunner

import stockotter_small.cli as cli_module
from stockotter_v2.llm.structurer import LLMStructurer
from stockotter_v2.schemas import NewsItem, StructuredEvent, now_in_seoul
from stockotter_v2.storage import Repository


class QueueClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise RuntimeError("no queued responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _build_news_item(*, news_id: str, raw_text: str = "기사 원문") -> NewsItem:
    base_time = now_in_seoul() - timedelta(minutes=3)
    return NewsItem(
        id=news_id,
        source="unit-test",
        title=f"{news_id} title",
        url=f"https://example.com/{news_id}",
        published_at=base_time,
        raw_text=raw_text,
        tickers_mentioned=["005930"],
    )


def test_llm_structurer_processes_only_unstructured_news(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")

    target = _build_news_item(news_id="news-001")
    already_done = _build_news_item(news_id="news-002")
    repo.upsert_news_item(target)
    repo.upsert_news_item(already_done)
    repo.upsert_structured_event(
        StructuredEvent(
            news_id=already_done.id,
            event_type="already_done",
            direction="neutral",
            confidence=0.5,
            horizon="short_term",
            themes=[],
            entities=[],
            risk_flags=[],
        )
    )

    client = QueueClient(
        [
            json.dumps(
                {
                    "event_type": "guidance",
                    "direction": "positive",
                    "confidence": 0.91,
                    "horizon": "short_term",
                    "themes": ["semiconductor"],
                    "entities": ["Samsung Electronics"],
                    "risk_flags": [],
                }
            )
        ]
    )
    structurer = LLMStructurer(repo=repo, client=client, max_retries=1)

    stats = structurer.run_since_hours(24)

    assert stats.processed == 1
    assert stats.failed == 0
    assert stats.skipped == 0
    assert len(client.prompts) == 1

    events = repo.list_events_by_date(target.published_at.date().isoformat())
    assert len(events) == 2
    assert any(event.news_id == target.id for event in events)


def test_invalid_json_repair_retry_succeeds(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    item = _build_news_item(news_id="news-repair")
    repo.upsert_news_item(item)

    client = QueueClient(
        [
            "not json response",
            json.dumps(
                {
                    "event_type": "supply_chain",
                    "direction": "mixed",
                    "confidence": 0.62,
                    "horizon": "mid_term",
                    "themes": ["memory"],
                    "entities": ["SK hynix"],
                    "risk_flags": ["demand_uncertainty"],
                }
            ),
        ]
    )
    structurer = LLMStructurer(repo=repo, client=client, max_retries=1)

    stats = structurer.run_since_hours(24)

    assert stats.processed == 1
    assert stats.failed == 0
    assert stats.skipped == 0
    assert len(client.prompts) == 2

    events = repo.list_events_by_date(item.published_at.date().isoformat())
    assert len(events) == 1
    assert events[0].news_id == item.id
    assert events[0].event_type == "supply_chain"


def test_cli_llm_structure_prints_counts(monkeypatch, tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    repo.upsert_news_item(_build_news_item(news_id="cli-001", raw_text="첫 번째 기사"))
    repo.upsert_news_item(_build_news_item(news_id="cli-002", raw_text="   "))
    repo.upsert_news_item(_build_news_item(news_id="cli-003", raw_text="세 번째 기사"))

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
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

    client = QueueClient(
        [
            json.dumps(
                {
                    "event_type": "demand",
                    "direction": "positive",
                    "confidence": 0.8,
                    "horizon": "short_term",
                    "themes": ["chip"],
                    "entities": ["Company A"],
                    "risk_flags": [],
                }
            ),
            "invalid json",
            "still invalid",
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
            "llm-structure",
            "--since-hours",
            "24",
            "--db-path",
            str(repo.db_path),
            "--config",
            str(cfg_path),
        ],
    )

    assert result.exit_code == 0
    assert "processed=1 failed=1 skipped=1" in result.output
