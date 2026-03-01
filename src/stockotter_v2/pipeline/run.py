from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from stockotter_v2.clusterer import TfidfClusterer
from stockotter_v2.llm import LLMStructurer
from stockotter_v2.news.naver_fetcher import NaverNewsFetcher
from stockotter_v2.schemas import Candidate, NewsItem, now_in_seoul
from stockotter_v2.scoring import RuleBasedScorer
from stockotter_v2.storage import Repository

logger = logging.getLogger(__name__)

_STATUS_RAN = "ran"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"


@dataclass(slots=True, frozen=True)
class PipelineStageSummary:
    name: str
    status: str
    processed: int
    errors: int
    duration_seconds: float
    note: str = ""


@dataclass(slots=True, frozen=True)
class CandidateReportRow:
    ticker: str
    score: float
    themes: list[str]
    risk_flags: list[str]
    headlines: list[str]


@dataclass(slots=True, frozen=True)
class PipelineRunResult:
    stages: list[PipelineStageSummary]
    report_rows: list[CandidateReportRow]
    top_candidates: list[Candidate]
    json_out: Path
    duration_seconds: float
    error_count: int


def run_pipeline(
    *,
    tickers: list[str],
    since_hours: int,
    top: int,
    json_out: Path,
    repo: Repository,
    fetcher: NaverNewsFetcher,
    structurer: LLMStructurer,
    clusterer: TfidfClusterer,
    scorer: RuleBasedScorer,
) -> PipelineRunResult:
    started_at = perf_counter()
    normalized_tickers = _dedupe_tickers(tickers)

    stages: list[PipelineStageSummary] = []

    fetch_stage = _run_fetch_stage(
        repo=repo,
        fetcher=fetcher,
        tickers=normalized_tickers,
        since_hours=since_hours,
    )
    stages.append(fetch_stage)
    fetch_ran = fetch_stage.status == _STATUS_RAN

    structure_stage = _run_structure_stage(
        repo=repo,
        structurer=structurer,
        since_hours=since_hours,
    )
    stages.append(structure_stage)
    structure_ran = structure_stage.status == _STATUS_RAN

    cluster_stage = _run_cluster_stage(
        repo=repo,
        clusterer=clusterer,
        since_hours=since_hours,
    )
    stages.append(cluster_stage)
    cluster_ran = cluster_stage.status == _STATUS_RAN

    score_stage, top_candidates = _run_score_stage(
        repo=repo,
        scorer=scorer,
        since_hours=since_hours,
        top=top,
        data_changed=fetch_ran or structure_ran or cluster_ran,
    )
    stages.append(score_stage)

    report_rows = _build_report_rows(repo=repo, candidates=top_candidates)

    duration_seconds = perf_counter() - started_at
    error_count = sum(stage.errors for stage in stages)

    result = PipelineRunResult(
        stages=stages,
        report_rows=report_rows,
        top_candidates=top_candidates,
        json_out=json_out,
        duration_seconds=duration_seconds,
        error_count=error_count,
    )
    _write_json_report(
        result=result,
        since_hours=since_hours,
        top=top,
    )
    return result


def render_report_table(rows: list[CandidateReportRow]) -> str:
    if not rows:
        return "no candidates found"

    headers = ("rank", "ticker", "score", "themes", "risk_flags", "headlines")
    line_rows = [
        (
            str(index),
            row.ticker,
            f"{row.score:.3f}",
            _truncate(",".join(row.themes) or "-", limit=28),
            _truncate(",".join(row.risk_flags) or "-", limit=28),
            _truncate(" / ".join(row.headlines) or "-", limit=72),
        )
        for index, row in enumerate(rows, start=1)
    ]
    return _render_table(headers=headers, rows=line_rows)


def render_stage_table(stages: list[PipelineStageSummary]) -> str:
    headers = ("stage", "status", "processed", "errors", "seconds", "note")
    line_rows = [
        (
            stage.name,
            stage.status,
            str(stage.processed),
            str(stage.errors),
            f"{stage.duration_seconds:.3f}",
            _truncate(stage.note or "-", limit=72),
        )
        for stage in stages
    ]
    return _render_table(headers=headers, rows=line_rows)


def _run_fetch_stage(
    *,
    repo: Repository,
    fetcher: NaverNewsFetcher,
    tickers: list[str],
    since_hours: int,
) -> PipelineStageSummary:
    stage_started = perf_counter()
    missing_tickers = _find_missing_tickers(
        repo=repo,
        tickers=tickers,
        since_hours=since_hours,
    )
    if not missing_tickers:
        return PipelineStageSummary(
            name="fetch",
            status=_STATUS_SKIPPED,
            processed=0,
            errors=0,
            duration_seconds=perf_counter() - stage_started,
            note=f"all {len(tickers)} tickers already have recent news",
        )

    deduped_by_url: dict[str, NewsItem] = {}
    errors = 0
    try:
        fetched_items = fetcher.fetch_recent_for_tickers(missing_tickers, hours=since_hours)
    except Exception:
        logger.exception("failed to fetch missing tickers")
        return PipelineStageSummary(
            name="fetch",
            status=_STATUS_FAILED,
            processed=0,
            errors=1,
            duration_seconds=perf_counter() - stage_started,
            note=f"target_tickers={len(missing_tickers)}",
        )

    for item in fetched_items:
        existing = deduped_by_url.get(item.url)
        if existing is None:
            deduped_by_url[item.url] = item
            continue

        merged_tickers = sorted(set(existing.tickers_mentioned + item.tickers_mentioned))
        deduped_by_url[item.url] = existing.model_copy(
            update={"tickers_mentioned": merged_tickers}
        )

    stored = 0
    for item in deduped_by_url.values():
        try:
            repo.upsert_news_item(item)
            stored += 1
        except Exception:
            logger.exception("failed to upsert news url=%s", item.url)
            errors += 1

    return PipelineStageSummary(
        name="fetch",
        status=_STATUS_RAN,
        processed=stored,
        errors=errors,
        duration_seconds=perf_counter() - stage_started,
        note=f"target_tickers={len(missing_tickers)} fetched={len(deduped_by_url)}",
    )


def _run_structure_stage(
    *,
    repo: Repository,
    structurer: LLMStructurer,
    since_hours: int,
) -> PipelineStageSummary:
    stage_started = perf_counter()
    pending_items = repo.list_news_items_without_event(since_hours=since_hours)
    if not pending_items:
        return PipelineStageSummary(
            name="structure",
            status=_STATUS_SKIPPED,
            processed=0,
            errors=0,
            duration_seconds=perf_counter() - stage_started,
            note="no unstructured news in range",
        )

    try:
        stats = structurer.structure_items(pending_items)
    except Exception:
        logger.exception("failed to run structuring stage")
        return PipelineStageSummary(
            name="structure",
            status=_STATUS_FAILED,
            processed=0,
            errors=1,
            duration_seconds=perf_counter() - stage_started,
            note=f"input={len(pending_items)}",
        )

    return PipelineStageSummary(
        name="structure",
        status=_STATUS_RAN,
        processed=stats.processed,
        errors=stats.failed,
        duration_seconds=perf_counter() - stage_started,
        note=f"input={len(pending_items)} skipped={stats.skipped}",
    )


def _run_cluster_stage(
    *,
    repo: Repository,
    clusterer: TfidfClusterer,
    since_hours: int,
) -> PipelineStageSummary:
    stage_started = perf_counter()
    recent_items = repo.list_news_items_since_hours(since_hours=since_hours)
    if not recent_items:
        return PipelineStageSummary(
            name="cluster",
            status=_STATUS_SKIPPED,
            processed=0,
            errors=0,
            duration_seconds=perf_counter() - stage_started,
            note="no recent news in range",
        )

    unstructured_ids = {
        item.id for item in repo.list_news_items_without_event(since_hours=since_hours)
    }
    structured_news_ids = {item.id for item in recent_items if item.id not in unstructured_ids}
    if not structured_news_ids:
        return PipelineStageSummary(
            name="cluster",
            status=_STATUS_SKIPPED,
            processed=0,
            errors=0,
            duration_seconds=perf_counter() - stage_started,
            note="no structured news in range",
        )

    clustered_member_ids = {
        news_id
        for cluster in repo.list_clusters()
        for news_id in cluster.member_news_ids
    }
    if structured_news_ids.issubset(clustered_member_ids):
        return PipelineStageSummary(
            name="cluster",
            status=_STATUS_SKIPPED,
            processed=0,
            errors=0,
            duration_seconds=perf_counter() - stage_started,
            note=f"already clustered structured_news={len(structured_news_ids)}",
        )

    clusters = clusterer.cluster(recent_items)
    errors = 0
    stored = 0
    for cluster in clusters:
        try:
            repo.upsert_cluster(cluster)
            stored += 1
        except Exception:
            logger.exception("failed to upsert cluster=%s", cluster.cluster_id)
            errors += 1

    return PipelineStageSummary(
        name="cluster",
        status=_STATUS_RAN,
        processed=stored,
        errors=errors,
        duration_seconds=perf_counter() - stage_started,
        note=f"news={len(recent_items)} clusters={len(clusters)}",
    )


def _run_score_stage(
    *,
    repo: Repository,
    scorer: RuleBasedScorer,
    since_hours: int,
    top: int,
    data_changed: bool,
) -> tuple[PipelineStageSummary, list[Candidate]]:
    stage_started = perf_counter()
    if not data_changed:
        existing_top = repo.list_candidates(limit=top)
        if existing_top:
            stage = PipelineStageSummary(
                name="score",
                status=_STATUS_SKIPPED,
                processed=0,
                errors=0,
                duration_seconds=perf_counter() - stage_started,
                note=f"reused existing candidates={len(existing_top)}",
            )
            return stage, existing_top

    try:
        ranked = scorer.score_since_hours(repo=repo, since_hours=since_hours)
        repo.replace_candidates(ranked)
    except Exception:
        logger.exception("failed to score candidates")
        fallback_top = repo.list_candidates(limit=top)
        stage = PipelineStageSummary(
            name="score",
            status=_STATUS_FAILED,
            processed=0,
            errors=1,
            duration_seconds=perf_counter() - stage_started,
            note=f"fallback candidates={len(fallback_top)}",
        )
        return stage, fallback_top

    top_candidates = ranked[:top]
    stage = PipelineStageSummary(
        name="score",
        status=_STATUS_RAN,
        processed=len(ranked),
        errors=0,
        duration_seconds=perf_counter() - stage_started,
        note=f"top={len(top_candidates)} total={len(ranked)}",
    )
    return stage, top_candidates


def _build_report_rows(
    *,
    repo: Repository,
    candidates: list[Candidate],
) -> list[CandidateReportRow]:
    rows: list[CandidateReportRow] = []
    for candidate in candidates:
        headlines = _load_headlines(repo=repo, candidate=candidate)
        rows.append(
            CandidateReportRow(
                ticker=candidate.ticker,
                score=candidate.score,
                themes=list(candidate.themes),
                risk_flags=list(candidate.risk_flags),
                headlines=headlines,
            )
        )
    return rows


def _load_headlines(*, repo: Repository, candidate: Candidate) -> list[str]:
    headlines: list[str] = []
    seen: set[str] = set()
    for news_id in candidate.supporting_news_ids:
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


def _write_json_report(
    *,
    result: PipelineRunResult,
    since_hours: int,
    top: int,
) -> None:
    payload = {
        "generated_at": now_in_seoul().isoformat(),
        "since_hours": since_hours,
        "top": top,
        "summary": {
            "duration_seconds": round(result.duration_seconds, 3),
            "error_count": result.error_count,
            "candidate_count": len(result.report_rows),
        },
        "stages": [asdict(stage) for stage in result.stages],
        "candidates": [asdict(row) for row in result.report_rows],
    }

    result.json_out.parent.mkdir(parents=True, exist_ok=True)
    result.json_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_missing_tickers(
    *,
    repo: Repository,
    tickers: list[str],
    since_hours: int,
) -> list[str]:
    recent_items = repo.list_news_items_since_hours(since_hours=since_hours)
    covered = {
        ticker
        for item in recent_items
        for ticker in item.tickers_mentioned
        if ticker
    }
    return [ticker for ticker in tickers if ticker not in covered]


def _dedupe_tickers(tickers: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        normalized = ticker.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _render_table(
    *,
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
) -> str:
    if not rows:
        return "no rows"

    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]

    def _line(values: tuple[str, ...]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        )

    divider = "-+-".join("-" * width for width in widths)
    body = [_line(headers), divider]
    body.extend(_line(row) for row in rows)
    return "\n".join(body)


def _truncate(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."
