from __future__ import annotations

import json

from stockotter_small.news.ticker_mapper import load_ticker_map, map_news_to_tickers


def test_load_ticker_map_reads_json_dict(tmp_path) -> None:
    mapping_path = tmp_path / "ticker_map.json"
    mapping_path.write_text(
        json.dumps(
            {
                "005930": "삼성전자",
                "000660": "SK하이닉스",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_ticker_map(mapping_path)

    assert loaded == {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
    }


def test_map_news_to_tickers_matches_full_name_first() -> None:
    ticker_map = {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
    }
    matched = map_news_to_tickers(
        title="삼성전자가 AI 반도체 수요 기대감으로 상승",
        summary="시장에서는 SK하이닉스도 주목 중",
        ticker_map=ticker_map,
    )

    assert matched == ["000660", "005930"]


def test_map_news_to_tickers_is_case_insensitive() -> None:
    ticker_map = {
        "035420": "NAVER",
    }
    matched = map_news_to_tickers(
        title="Naver가 장중 강세",
        summary="광고 매출 회복 기대",
        ticker_map=ticker_map,
    )

    assert matched == ["035420"]


def test_map_news_to_tickers_avoids_partial_false_positive() -> None:
    ticker_map = {
        "005930": "삼성전자",
    }
    matched = map_news_to_tickers(
        title="삼성전기 실적 전망",
        summary="전자업종 전반 회복 기대",
        ticker_map=ticker_map,
    )

    assert matched == []
