from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stockotter_v2.storage import Repository


@dataclass(frozen=True)
class BriefingCandidate:
    ticker: str
    score: float
    headlines: list[str]


def build_briefing_candidates(
    *,
    repo: Repository,
    asof: date,
    limit: int = 10,
) -> list[BriefingCandidate]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    snapshot_date = repo.get_candidate_snapshot_date()
    if snapshot_date is None:
        raise ValueError("no candidate snapshot found in database")
    if snapshot_date != asof:
        raise ValueError(
            "candidate snapshot date mismatch "
            f"snapshot_date={snapshot_date.isoformat()} asof={asof.isoformat()}"
        )

    briefing_candidates: list[BriefingCandidate] = []
    for candidate in repo.list_candidates(limit=limit):
        headlines = _select_headlines(repo=repo, supporting_news_ids=candidate.supporting_news_ids)
        if not headlines:
            continue
        briefing_candidates.append(
            BriefingCandidate(
                ticker=candidate.ticker,
                score=candidate.score,
                headlines=headlines,
            )
        )
    if not briefing_candidates:
        raise ValueError("no candidates with headlines available for telegram briefing")
    return briefing_candidates


def format_briefing_message(*, asof: date, candidates: list[BriefingCandidate]) -> str:
    if not candidates:
        raise ValueError("candidates must not be empty")

    lines = [
        "[StockOtter] Morning Briefing",
        f"asof: {asof.isoformat()}",
        f"candidates: {len(candidates)}",
        "",
    ]
    for index, candidate in enumerate(candidates, start=1):
        lines.append(f"{index}. {candidate.ticker} | score {candidate.score:.3f}")
        for headline in candidate.headlines[:2]:
            lines.append(f"- {_truncate(headline, limit=140)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _select_headlines(*, repo: Repository, supporting_news_ids: list[str]) -> list[str]:
    headlines: list[str] = []
    seen: set[str] = set()
    for news_id in supporting_news_ids:
        item = repo.get_news_item(news_id)
        if item is None:
            continue
        title = " ".join(item.title.split())
        if not title or title in seen:
            continue
        seen.add(title)
        headlines.append(title)
        if len(headlines) >= 2:
            break
    return headlines


def _truncate(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."
