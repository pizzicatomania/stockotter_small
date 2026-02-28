from __future__ import annotations

from textwrap import dedent

from stockotter_v2.schemas import NewsItem

DEFAULT_STRUCTURER_PROMPT = dedent(
    """
    당신은 뉴스 구조화 추출기입니다.
    반드시 JSON 객체 하나만 출력하세요. 마크다운, 코드펜스, 설명 문장 금지.

    아래 필드만 포함하세요:
    - event_type: string
    - direction: one of ["positive", "negative", "neutral", "mixed"]
    - confidence: float (0.0 ~ 1.0)
    - horizon: string
    - themes: string[]
    - entities: string[]
    - risk_flags: string[]

    news_title: __TITLE__
    news_source: __SOURCE__
    news_url: __URL__
    news_published_at: __PUBLISHED_AT__
    news_raw_text:
    __RAW_TEXT__

    출력 예시:
    {
      "event_type": "earnings_guidance",
      "direction": "positive",
      "confidence": 0.82,
      "horizon": "short_term",
      "themes": ["semiconductor"],
      "entities": ["Samsung Electronics"],
      "risk_flags": ["macro_uncertainty"]
    }
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
