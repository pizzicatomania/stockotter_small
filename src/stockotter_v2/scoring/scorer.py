from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from stockotter_v2.schemas import Candidate, NewsItem, StructuredEvent
from stockotter_v2.storage import Repository

from .weights import ScoreWeights, build_score_weights

_DIRECTION_LABEL = {
    "positive": "긍정",
    "negative": "부정",
    "neutral": "중립",
    "mixed": "혼합",
}


@dataclass(frozen=True)
class RepresentativeStructuredEvent:
    news: NewsItem
    event: StructuredEvent


@dataclass(slots=True)
class _CandidateAccumulator:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    reason_seen: set[str] = field(default_factory=set)
    supporting_news_ids: list[str] = field(default_factory=list)
    supporting_news_seen: set[str] = field(default_factory=set)
    themes: set[str] = field(default_factory=set)
    risk_flags: set[str] = field(default_factory=set)


class RuleBasedScorer:
    def __init__(
        self,
        *,
        weights: ScoreWeights | None = None,
        min_score: float = 0.0,
        max_reasons: int = 3,
    ) -> None:
        if max_reasons < 1:
            raise ValueError("max_reasons must be >= 1")
        self.weights = weights if weights is not None else build_score_weights()
        self.min_score = min_score
        self.max_reasons = max_reasons

    def score_event(self, event: StructuredEvent) -> float:
        score = 0.0
        score += self.weights.event_type_weight(event.event_type)
        score += (
            self.weights.direction_weight(event.direction)
            * event.confidence
            * self.weights.confidence_multiplier
        )
        score += self.weights.horizon_weight(event.horizon)
        score += sum(self.weights.risk_penalty(flag) for flag in event.risk_flags)
        return score

    def rank(
        self,
        events: Iterable[RepresentativeStructuredEvent],
        *,
        top: int | None = None,
    ) -> list[Candidate]:
        if top is not None and top < 1:
            raise ValueError("top must be >= 1")

        candidates = self._aggregate(events)
        ranked = sorted(candidates, key=lambda item: (-item.score, item.ticker))
        if top is None:
            return ranked
        return ranked[:top]

    def score_since_hours(
        self,
        *,
        repo: Repository,
        since_hours: int,
        top: int | None = None,
    ) -> list[Candidate]:
        rows = repo.list_representative_structured_events_since_hours(since_hours=since_hours)
        events = [
            RepresentativeStructuredEvent(news=news_item, event=event)
            for news_item, event in rows
        ]
        return self.rank(events, top=top)

    def _aggregate(self, events: Iterable[RepresentativeStructuredEvent]) -> list[Candidate]:
        accumulators: dict[str, _CandidateAccumulator] = {}

        for record in events:
            if not record.news.tickers_mentioned:
                continue

            event_score = self.score_event(record.event)
            reason = self._build_reason(record.news, record.event)
            tickers = sorted(set(record.news.tickers_mentioned))

            for ticker in tickers:
                accumulator = accumulators.setdefault(ticker, _CandidateAccumulator())
                accumulator.score += event_score

                if reason not in accumulator.reason_seen:
                    accumulator.reason_seen.add(reason)
                    accumulator.reasons.append(reason)

                if record.news.id not in accumulator.supporting_news_seen:
                    accumulator.supporting_news_seen.add(record.news.id)
                    accumulator.supporting_news_ids.append(record.news.id)

                accumulator.themes.update(record.event.themes)
                accumulator.risk_flags.update(record.event.risk_flags)

        candidates: list[Candidate] = []
        for ticker, accumulator in accumulators.items():
            if accumulator.score < self.min_score:
                continue

            candidates.append(
                Candidate(
                    ticker=ticker,
                    score=round(accumulator.score, 6),
                    reasons=accumulator.reasons[: self.max_reasons],
                    supporting_news_ids=accumulator.supporting_news_ids,
                    themes=sorted(accumulator.themes),
                    risk_flags=sorted(accumulator.risk_flags),
                )
            )

        return candidates

    def _build_reason(self, news_item: NewsItem, event: StructuredEvent) -> str:
        direction_label = _DIRECTION_LABEL.get(event.direction, event.direction)
        title = " ".join(news_item.title.split())
        if len(title) > 50:
            title = f"{title[:47]}..."

        reason = (
            f"{event.event_type}/{direction_label} "
            f"(신뢰도 {event.confidence:.2f}, {event.horizon}) | {title}"
        )
        if event.risk_flags:
            risk_text = ",".join(event.risk_flags[:2])
            reason = f"{reason} | 리스크:{risk_text}"
        return reason
