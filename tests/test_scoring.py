from __future__ import annotations

import json
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from stockotter_small.cli import app
from stockotter_v2.schemas import Cluster, NewsItem, StructuredEvent, now_in_seoul
from stockotter_v2.scoring import (
    RepresentativeStructuredEvent,
    RuleBasedScorer,
    build_score_weights,
)
from stockotter_v2.storage import Repository


def _build_news_item(
    *,
    news_id: str,
    ticker: str,
    title: str,
    minutes_from_base: int,
) -> NewsItem:
    base_time = now_in_seoul() - timedelta(hours=1)
    return NewsItem(
        id=news_id,
        source="unit-test",
        title=title,
        url=f"https://example.com/{news_id}",
        published_at=base_time + timedelta(minutes=minutes_from_base),
        raw_text=f"{title} 원문",
        tickers_mentioned=[ticker],
    )


def _build_event(
    *,
    news_id: str,
    event_type: str,
    direction: str,
    confidence: float,
    horizon: str,
    risk_flags: list[str] | None = None,
) -> StructuredEvent:
    return StructuredEvent(
        news_id=news_id,
        event_type=event_type,
        direction=direction,
        confidence=confidence,
        horizon=horizon,
        themes=["semiconductor"],
        entities=["Company A"],
        risk_flags=risk_flags or [],
    )


def _test_weights() -> dict[str, float]:
    return {
        "confidence_multiplier": 1.0,
        "direction_positive": 1.0,
        "direction_negative": -1.0,
        "direction_neutral": 0.0,
        "direction_mixed": -0.2,
        "event_type_default": 0.0,
        "horizon_default": 0.0,
        "horizon_intraday": 0.0,
        "risk_flag_penalty": -1.0,
        "severe_risk_flag_penalty": -3.0,
        "unknown_event_type_weight": 0.0,
        "unknown_horizon_weight": 0.0,
    }


def test_rule_based_scorer_event_score_matches_formula() -> None:
    weights = build_score_weights(
        {
            "confidence_multiplier": 2.0,
            "direction_positive": 1.0,
            "horizon_intraday": 0.5,
            "event_type_earnings_guidance": 0.3,
            "risk_flag_penalty": -1.0,
            "severe_risk_flag_penalty": -3.0,
            "unknown_event_type_weight": 0.0,
            "unknown_horizon_weight": 0.0,
        }
    )
    scorer = RuleBasedScorer(weights=weights, min_score=-100.0)
    event = _build_event(
        news_id="news-001",
        event_type="earnings_guidance",
        direction="positive",
        confidence=0.8,
        horizon="intraday",
    )

    assert scorer.score_event(event) == pytest.approx(2.4)


def test_rule_based_scorer_applies_severe_risk_penalty() -> None:
    scorer = RuleBasedScorer(
        weights=build_score_weights(_test_weights()),
        min_score=-100.0,
    )
    event = _build_event(
        news_id="news-risk",
        event_type="unknown",
        direction="positive",
        confidence=0.6,
        horizon="intraday",
        risk_flags=["cb_issue"],
    )

    assert scorer.score_event(event) == pytest.approx(-2.4)


def test_rule_based_scorer_rank_orders_top_n() -> None:
    scorer = RuleBasedScorer(
        weights=build_score_weights(_test_weights()),
        min_score=-100.0,
    )
    news_a = _build_news_item(
        news_id="news-a",
        ticker="111111",
        title="A 기사",
        minutes_from_base=0,
    )
    news_b = _build_news_item(
        news_id="news-b",
        ticker="222222",
        title="B 기사",
        minutes_from_base=1,
    )
    entries = [
        RepresentativeStructuredEvent(
            news=news_a,
            event=_build_event(
                news_id="news-a",
                event_type="unknown",
                direction="positive",
                confidence=0.3,
                horizon="intraday",
            ),
        ),
        RepresentativeStructuredEvent(
            news=news_b,
            event=_build_event(
                news_id="news-b",
                event_type="unknown",
                direction="positive",
                confidence=0.7,
                horizon="intraday",
            ),
        ),
    ]

    ranked = scorer.rank(entries, top=1)

    assert len(ranked) == 1
    assert ranked[0].ticker == "222222"
    assert ranked[0].score == pytest.approx(0.7)


def test_cli_score_uses_cluster_representative_and_exports_json(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")

    rep_news = _build_news_item(
        news_id="news-rep",
        ticker="111111",
        title="대표 기사",
        minutes_from_base=0,
    )
    duplicate_news = _build_news_item(
        news_id="news-dup",
        ticker="111111",
        title="중복 기사",
        minutes_from_base=1,
    )
    other_news = _build_news_item(
        news_id="news-other",
        ticker="222222",
        title="다른 종목 기사",
        minutes_from_base=2,
    )

    for item in [rep_news, duplicate_news, other_news]:
        repo.upsert_news_item(item)

    repo.upsert_structured_event(
        _build_event(
            news_id=rep_news.id,
            event_type="unknown",
            direction="positive",
            confidence=0.4,
            horizon="intraday",
        )
    )
    repo.upsert_structured_event(
        _build_event(
            news_id=duplicate_news.id,
            event_type="unknown",
            direction="positive",
            confidence=0.99,
            horizon="intraday",
        )
    )
    repo.upsert_structured_event(
        _build_event(
            news_id=other_news.id,
            event_type="unknown",
            direction="positive",
            confidence=0.8,
            horizon="intraday",
        )
    )

    repo.upsert_cluster(
        Cluster(
            cluster_id="cluster-1",
            representative_news_id=rep_news.id,
            member_news_ids=[rep_news.id, duplicate_news.id],
            summary="중복 뉴스 묶음",
        )
    )
    repo.upsert_cluster(
        Cluster(
            cluster_id="cluster-2",
            representative_news_id=other_news.id,
            member_news_ids=[other_news.id],
            summary="단일 뉴스 묶음",
        )
    )

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
                "scoring": {"min_score": 0.0, "weights": _test_weights()},
                "universe": {"market": "KR", "tickers": [], "max_candidates": 20},
            }
        ),
        encoding="utf-8",
    )

    json_out = tmp_path / "score_top1.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "score",
            "--since-hours",
            "24",
            "--top",
            "1",
            "--db-path",
            str(repo.db_path),
            "--config",
            str(config_path),
            "--json-out",
            str(json_out),
        ],
    )

    assert result.exit_code == 0
    assert "rank" in result.output
    assert "ticker" in result.output
    assert "222222" in result.output

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["ticker"] == "222222"

    stored = repo.list_candidates()
    assert [candidate.ticker for candidate in stored] == ["222222", "111111"]
    assert stored[0].score == pytest.approx(0.8)
    assert stored[1].score == pytest.approx(0.4)
