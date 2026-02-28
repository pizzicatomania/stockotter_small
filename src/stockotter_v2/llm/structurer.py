from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from stockotter_v2.schemas import NewsItem, StructuredEvent
from stockotter_v2.storage import Repository

from .prompts import build_repair_prompt, build_structured_event_prompt

logger = logging.getLogger(__name__)


class TextGenerationClient(Protocol):
    def generate(self, prompt: str) -> str:
        """Generate response text from a prompt."""


@dataclass(slots=True)
class StructuringStats:
    processed: int = 0
    failed: int = 0
    skipped: int = 0


class LLMStructurer:
    def __init__(
        self,
        *,
        repo: Repository,
        client: TextGenerationClient,
        prompt_template: str | None = None,
        max_retries: int = 1,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0.")
        self.repo = repo
        self.client = client
        self.prompt_template = prompt_template
        self.max_retries = max_retries

    def run_since_hours(self, since_hours: int) -> StructuringStats:
        items = self.repo.list_news_items_without_event(since_hours=since_hours)
        return self.structure_items(items)

    def structure_items(self, items: Iterable[NewsItem]) -> StructuringStats:
        stats = StructuringStats()
        seen_news_ids: set[str] = set()

        for item in items:
            if item.id in seen_news_ids:
                stats.skipped += 1
                continue
            seen_news_ids.add(item.id)

            if not item.raw_text.strip():
                logger.warning("skip empty raw_text news_id=%s", item.id)
                stats.skipped += 1
                continue

            try:
                event = self._extract_with_retry(item)
                self.repo.upsert_structured_event(event)
                stats.processed += 1
            except Exception:
                logger.exception("failed to structure news_id=%s", item.id)
                stats.failed += 1

        return stats

    def _extract_with_retry(self, item: NewsItem) -> StructuredEvent:
        prompt = build_structured_event_prompt(item, template=self.prompt_template)
        response_text = self.client.generate(prompt)

        try:
            return _validate_structured_event_json(news_id=item.id, response_text=response_text)
        except Exception as exc:
            logger.warning(
                "validation failed news_id=%s; attempting repair retry once: %s",
                item.id,
                exc,
            )

        if self.max_retries < 1:
            raise ValueError("validation failed and retries disabled.")

        repair_prompt = build_repair_prompt(response_text)
        repaired_response_text = self.client.generate(repair_prompt)
        return _validate_structured_event_json(
            news_id=item.id,
            response_text=repaired_response_text,
        )


def _validate_structured_event_json(*, news_id: str, response_text: str) -> StructuredEvent:
    payload = _load_json_object(response_text)
    payload["news_id"] = news_id
    try:
        return StructuredEvent.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("response JSON does not match StructuredEvent schema.") from exc


def _load_json_object(response_text: str) -> dict[str, object]:
    candidate = _strip_code_fence(response_text.strip())
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError:
        loaded = json.loads(_extract_json_block(candidate))

    if not isinstance(loaded, dict):
        raise ValueError("LLM response root must be a JSON object.")
    return loaded


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return text


def _extract_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response does not contain a JSON object.")
    return text[start : end + 1]
