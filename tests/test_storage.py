from __future__ import annotations

from typer.testing import CliRunner

import stockotter_v2.storage.cache as cache_module
from stockotter_small.cli import app
from stockotter_v2.schemas import NewsItem, StructuredEvent
from stockotter_v2.storage import FileCache, Repository


def test_file_cache_ttl_behavior(monkeypatch, tmp_path) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(cache_module.time, "time", lambda: clock["now"])

    cache = FileCache(tmp_path / "raw-cache")
    key = "https://example.com/news/ttl-test"
    cache.set(key, "cached payload", ttl_seconds=5)

    assert cache.get(key) == "cached payload"

    clock["now"] = 1_006.0
    assert cache.get(key) is None


def test_repository_upsert_idempotent(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    item = NewsItem(
        id="news-001",
        source="unit-test",
        title="초기 제목",
        url="https://example.com/news/001",
        published_at="2026-02-28T09:00:00+09:00",
        raw_text="원문",
        tickers_mentioned=["005930"],
    )

    repo.upsert_news_item(item)
    repo.upsert_news_item(item)
    assert len(repo.list_news_items()) == 1

    updated = item.model_copy(update={"title": "수정 제목"})
    repo.upsert_news_item(updated)
    items = repo.list_news_items()
    assert len(items) == 1
    assert items[0].title == "수정 제목"

    event = StructuredEvent(
        news_id=item.id,
        event_type="guidance",
        direction="positive",
        confidence=0.9,
        horizon="short_term",
        themes=["semiconductor"],
        entities=["Samsung Electronics"],
        risk_flags=[],
    )
    repo.upsert_structured_event(event)
    repo.upsert_structured_event(event.model_copy(update={"confidence": 0.7}))

    events = repo.list_events_by_date("2026-02-28")
    assert len(events) == 1
    assert events[0].confidence == 0.7


def test_cli_debug_storage_smoke(tmp_path) -> None:
    runner = CliRunner()
    db_path = tmp_path / "storage.db"
    cache_dir = tmp_path / "cache"

    result = runner.invoke(
        app,
        ["debug", "storage", "--db-path", str(db_path), "--cache-dir", str(cache_dir)],
    )

    assert result.exit_code == 0
    assert "storage ok" in result.output
    assert db_path.exists()
    assert cache_dir.exists()
