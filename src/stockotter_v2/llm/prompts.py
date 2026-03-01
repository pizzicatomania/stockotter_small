from __future__ import annotations

from textwrap import dedent

from stockotter_v2.schemas import NewsItem

DEFAULT_STRUCTURER_PROMPT = dedent(
    """
    역할: 한국 금융 뉴스에서 StructuredEvent를 추출하는 분류기입니다.
    출력 규칙: JSON 객체 하나만 출력하세요. 마크다운/코드펜스/설명문장 금지.

    허용 event_type taxonomy (아래 값만 사용):
    - earnings_guidance: 실적 전망/가이던스 상향·하향
    - contract_win: 공급 계약/수주 체결
    - supply_chain: 공급망 차질·개선
    - demand: 수요 변화/판매 흐름
    - regulatory_approval: 인허가/승인
    - investigation: 조사/감리/제재 착수
    - litigation: 소송/판결/법적 분쟁
    - UNKNOWN: 위 분류로 확신할 수 없음

    direction enum:
    - positive
    - negative
    - neutral
    - mixed

    horizon enum:
    - intraday
    - 1_3d
    - short_term
    - mid_term
    - long_term

    확신이 낮거나 분류가 애매하면:
    - event_type을 반드시 "UNKNOWN"으로 설정
    - confidence는 0.40 이하로 설정

    필수 출력 필드(추가 필드 금지):
    - event_type
    - direction
    - confidence
    - horizon
    - themes
    - entities
    - risk_flags

    예시 1:
    {
      "event_type": "contract_win",
      "direction": "positive",
      "confidence": 0.82,
      "horizon": "mid_term",
      "themes": ["battery"],
      "entities": ["LG에너지솔루션"],
      "risk_flags": []
    }

    예시 2:
    {
      "event_type": "UNKNOWN",
      "direction": "neutral",
      "confidence": 0.28,
      "horizon": "short_term",
      "themes": [],
      "entities": [],
      "risk_flags": ["investor_sentiment_divergence"]
    }

    news_title: __TITLE__
    news_source: __SOURCE__
    news_url: __URL__
    news_published_at: __PUBLISHED_AT__
    news_raw_text:
    __RAW_TEXT__

    """
).strip()

DEFAULT_REPAIR_PROMPT = dedent(
    """
    이전 응답은 JSON 검증에 실패했습니다.
    아래 텍스트를 보고 필수 필드를 갖춘 JSON 객체 하나만 다시 출력하세요.
    설명 문장, 코드펜스, 주석 금지.

    필수 필드:
    event_type, direction, confidence, horizon, themes, entities, risk_flags

    broken_response:
    __BROKEN_RESPONSE__
    """
).strip()


def build_structured_event_prompt(item: NewsItem, template: str | None = None) -> str:
    prompt = template if template else DEFAULT_STRUCTURER_PROMPT
    return (
        prompt.replace("__TITLE__", item.title)
        .replace("__SOURCE__", item.source)
        .replace("__URL__", item.url)
        .replace("__PUBLISHED_AT__", item.published_at.isoformat())
        .replace("__RAW_TEXT__", item.raw_text)
    )


def build_repair_prompt(response_text: str, template: str | None = None) -> str:
    prompt = template if template else DEFAULT_REPAIR_PROMPT
    return prompt.replace("__BROKEN_RESPONSE__", response_text)
