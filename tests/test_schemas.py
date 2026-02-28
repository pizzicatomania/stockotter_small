from __future__ import annotations

from pathlib import Path

from stockotter_v2.config import AppConfig, load_config
from stockotter_v2.schemas import (
    Candidate,
    Cluster,
    NewsItem,
    StructuredEvent,
    json_schema_for,
    validate_json,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def test_news_item_json_roundtrip_from_fixture() -> None:
    raw = (FIXTURE_DIR / "news_item.sample.json").read_text(encoding="utf-8")
    item = validate_json(NewsItem, raw)

    assert item.published_at.tzinfo is not None
    assert item.fetched_at.tzinfo is not None

    encoded = item.model_dump_json()
    decoded = NewsItem.model_validate_json(encoded)
    assert decoded == item


def test_event_cluster_candidate_roundtrip() -> None:
    event = StructuredEvent(
        news_id="news-001",
        event_type="earnings_guidance",
        direction="positive",
        confidence=0.9,
        horizon="short_term",
        themes=["semiconductor"],
        entities=["Samsung Electronics"],
        risk_flags=["macro_volatility"],
    )
    cluster = Cluster(
        cluster_id="cluster-001",
        representative_news_id="news-001",
        member_news_ids=["news-001", "news-002"],
        summary="반도체 업황 회복 뉴스 묶음",
    )
    candidate_raw = (FIXTURE_DIR / "candidate.sample.json").read_text(encoding="utf-8")
    candidate = validate_json(Candidate, candidate_raw)

    for model in [event, cluster, candidate]:
        restored = type(model).model_validate_json(model.model_dump_json())
        assert restored == model


def test_json_schema_utility() -> None:
    schema = json_schema_for(Candidate)
    assert schema["title"] == "Candidate"
    assert "properties" in schema
    assert "ticker" in schema["properties"]


def test_config_example_load_and_validate() -> None:
    cfg_path = ROOT / "config" / "config.example.yaml"
    config = load_config(cfg_path)

    assert isinstance(config, AppConfig)
    assert config.timezone == "Asia/Seoul"
    assert len(config.sources) > 0
    assert config.llm.provider == "openai"
