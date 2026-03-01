from __future__ import annotations

from stockotter_v2.llm.postprocess import normalize_structured_event_payload


def test_normalize_structured_event_payload_maps_synonyms_and_clamps() -> None:
    payload = {
        "event_type": "guidance",
        "direction": "bullish",
        "horizon": "long",
        "confidence": 1.7,
        "risk_flags": ["macro_uncertainty"],
    }

    normalized = normalize_structured_event_payload(payload)

    assert normalized["event_type"] == "earnings_guidance"
    assert normalized["direction"] == "positive"
    assert normalized["horizon"] == "long_term"
    assert normalized["confidence"] == 1.0


def test_normalize_structured_event_payload_drops_hallucinated_ticker_keys() -> None:
    payload = {
        "event_type": "demand",
        "direction": "neutral",
        "horizon": "short_term",
        "confidence": -0.1,
        "ticker": "123456",
        "tickers_mentioned": ["654321"],
    }

    normalized = normalize_structured_event_payload(payload)

    assert "ticker" not in normalized
    assert "tickers_mentioned" not in normalized
    assert normalized["confidence"] == 0.0
