from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path

import typer

from stockotter_v2 import load_config
from stockotter_v2.clusterer import TfidfClusterer
from stockotter_v2.llm import GeminiClient, LLMStructurer
from stockotter_v2.news.naver_fetcher import NaverNewsFetcher
from stockotter_v2.paper import apply_eod_rules, create_entry_position
from stockotter_v2.pipeline import (
    render_report_table,
    render_stage_table,
    run_pipeline,
)
from stockotter_v2.schemas import Candidate, NewsItem, now_in_seoul
from stockotter_v2.scoring import RuleBasedScorer, build_score_weights
from stockotter_v2.storage import FileCache, Repository
from stockotter_v2.universe import filter_market_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = typer.Typer(help="StockOtter Small CLI")
debug_app = typer.Typer(help="Debug commands")
universe_app = typer.Typer(help="Universe commands")
paper_app = typer.Typer(help="Paper trading commands")
app.add_typer(debug_app, name="debug")
app.add_typer(universe_app, name="universe")
app.add_typer(paper_app, name="paper")


@app.command()
def hello(name: str = typer.Option("world", "--name", "-n", help="Name to greet.")) -> None:
    """Simple smoke command."""
    logging.info("hello command invoked")
    typer.echo(f"hello, {name}")


@app.command("fetch-news")
def fetch_news(
    tickers_file: Path = typer.Option(
        ...,
        "--tickers-file",
        help="Path to text file with seed tickers (one per line).",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    hours: int = typer.Option(
        24,
        "--hours",
        help="Only fetch articles newer than N hours.",
        min=1,
    ),
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
    cache_dir: Path = typer.Option(
        Path("data/cache/raw"),
        "--cache-dir",
        help="File cache directory for fetched HTML.",
    ),
    sleep_seconds: float = typer.Option(
        0.6,
        "--sleep-seconds",
        min=0.0,
        help="Sleep interval between uncached HTTP requests.",
    ),
) -> None:
    """Fetch Naver Finance stock news and store into SQLite."""
    tickers = _load_tickers(tickers_file)
    if not tickers:
        typer.echo("no valid tickers found in file", err=True)
        raise typer.Exit(code=1)

    repo = Repository(db_path)
    cache = FileCache(cache_dir)
    fetcher = NaverNewsFetcher(cache=cache, sleep_seconds=sleep_seconds)
    items = fetcher.fetch_recent_for_tickers(tickers, hours=hours)

    stored = 0
    for item in items:
        try:
            repo.upsert_news_item(item)
            stored += 1
        except Exception:
            logging.exception("failed to upsert news url=%s", item.url)

    summary_only_count = sum(
        1 for item in items if item.raw_text.startswith("[summary_only] ")
    )
    typer.echo(
        "stored "
        f"{stored} items "
        f"(fetched={len(items)}, summary_only={summary_only_count}, tickers={len(tickers)})"
    )


@app.command("llm-structure")
def llm_structure(
    since_hours: int = typer.Option(
        24,
        "--since-hours",
        help="Only structure articles newer than N hours.",
        min=1,
    ),
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
    config_path: Path = typer.Option(
        Path("config/config.example.yaml"),
        "--config",
        help="Config file path.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    api_key_env: str = typer.Option(
        "GEMINI_API_KEY",
        "--api-key-env",
        help="Environment variable name for Gemini API key.",
    ),
) -> None:
    """Structure news_items into StructuredEvent rows via Gemini JSON output."""
    config = load_config(config_path)
    repo = Repository(db_path)

    if config.llm.provider.lower() != "gemini":
        logging.warning(
            "llm.provider=%s, but llm-structure command currently uses Gemini client.",
            config.llm.provider,
        )

    try:
        client = GeminiClient.from_env(
            model=config.llm.model,
            temperature=config.llm.temperature,
            env_var=api_key_env,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    structurer = LLMStructurer(
        repo=repo,
        client=client,
        prompt_template=config.llm.prompt_template,
        max_retries=config.llm.max_retries,
    )
    stats = structurer.run_since_hours(since_hours)
    typer.echo(
        f"processed={stats.processed} failed={stats.failed} skipped={stats.skipped}"
    )


@app.command("cluster")
def cluster_news(
    since_hours: int = typer.Option(
        24,
        "--since-hours",
        help="Only cluster articles newer than N hours.",
        min=1,
    ),
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
    similarity_threshold: float = typer.Option(
        0.35,
        "--similarity-threshold",
        help="Cosine similarity threshold for TF-IDF clustering.",
        min=0.0,
        max=1.0,
    ),
    representative_policy: str = typer.Option(
        "earliest",
        "--representative-policy",
        help="Representative policy: earliest or keyword.",
    ),
) -> None:
    """Cluster similar news and store cluster rows."""
    repo = Repository(db_path)
    try:
        clusterer = TfidfClusterer(
            similarity_threshold=similarity_threshold,
            representative_policy=representative_policy,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    items = repo.list_news_items_since_hours(since_hours=since_hours)
    clusters = clusterer.cluster(items)
    for cluster in clusters:
        repo.upsert_cluster(cluster)

    typer.echo(f"clusters={len(clusters)} news={len(items)}")


@app.command("score")
def score_candidates(
    since_hours: int = typer.Option(
        24,
        "--since-hours",
        help="Only score representative articles newer than N hours.",
        min=1,
    ),
    top: int = typer.Option(
        10,
        "--top",
        help="Output top N candidates.",
        min=1,
    ),
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
    config_path: Path = typer.Option(
        Path("config/config.example.yaml"),
        "--config",
        help="Config file path.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    json_out: Path | None = typer.Option(
        None,
        "--json-out",
        help="Optional output path for top candidates JSON.",
    ),
) -> None:
    """Score clustered representative events and rank candidates."""
    config = load_config(config_path)
    repo = Repository(db_path)
    scorer = RuleBasedScorer(
        weights=build_score_weights(config.scoring.weights),
        min_score=config.scoring.min_score,
    )

    ranked = scorer.score_since_hours(repo=repo, since_hours=since_hours)
    repo.replace_candidates(ranked)
    top_candidates = ranked[:top]

    typer.echo(_render_candidate_table(top_candidates))
    typer.echo(f"top={len(top_candidates)} total={len(ranked)}")

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = [candidate.model_dump(mode="json") for candidate in top_candidates]
        json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"json_out={json_out}")


@app.command("run")
def run_one_command_pipeline(
    tickers_file: Path = typer.Option(
        ...,
        "--tickers-file",
        help="Path to text file with seed tickers (one per line).",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    since_hours: int = typer.Option(
        24,
        "--since-hours",
        help="Only process data newer than N hours.",
        min=1,
    ),
    top: int = typer.Option(
        10,
        "--top",
        help="Output top N candidates.",
        min=1,
    ),
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
    cache_dir: Path = typer.Option(
        Path("data/cache/raw"),
        "--cache-dir",
        help="File cache directory for fetched HTML.",
    ),
    sleep_seconds: float = typer.Option(
        0.6,
        "--sleep-seconds",
        min=0.0,
        help="Sleep interval between uncached HTTP requests.",
    ),
    config_path: Path = typer.Option(
        Path("config/config.example.yaml"),
        "--config",
        help="Config file path.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    api_key_env: str = typer.Option(
        "GEMINI_API_KEY",
        "--api-key-env",
        help="Environment variable name for Gemini API key.",
    ),
    similarity_threshold: float = typer.Option(
        0.35,
        "--similarity-threshold",
        help="Cosine similarity threshold for TF-IDF clustering.",
        min=0.0,
        max=1.0,
    ),
    representative_policy: str = typer.Option(
        "earliest",
        "--representative-policy",
        help="Representative policy: earliest or keyword.",
    ),
    json_out: Path = typer.Option(
        Path("data/reports/candidates_top.json"),
        "--json-out",
        help="Output JSON report path.",
    ),
) -> None:
    """Run fetch->structure->cluster->score and output top candidates."""
    tickers = _load_tickers(tickers_file)
    if not tickers:
        typer.echo("no valid tickers found in file", err=True)
        raise typer.Exit(code=1)

    config = load_config(config_path)
    repo = Repository(db_path)
    cache = FileCache(cache_dir)
    fetcher = NaverNewsFetcher(cache=cache, sleep_seconds=sleep_seconds)

    if config.llm.provider.lower() != "gemini":
        logging.warning(
            "llm.provider=%s, but run command currently uses Gemini client.",
            config.llm.provider,
        )

    try:
        client = GeminiClient.from_env(
            model=config.llm.model,
            temperature=config.llm.temperature,
            env_var=api_key_env,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    structurer = LLMStructurer(
        repo=repo,
        client=client,
        prompt_template=config.llm.prompt_template,
        max_retries=config.llm.max_retries,
    )
    try:
        clusterer = TfidfClusterer(
            similarity_threshold=similarity_threshold,
            representative_policy=representative_policy,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    scorer = RuleBasedScorer(
        weights=build_score_weights(config.scoring.weights),
        min_score=config.scoring.min_score,
    )

    result = run_pipeline(
        tickers=tickers,
        since_hours=since_hours,
        top=top,
        json_out=json_out,
        repo=repo,
        fetcher=fetcher,
        structurer=structurer,
        clusterer=clusterer,
        scorer=scorer,
    )

    typer.echo(render_report_table(result.report_rows))
    typer.echo(render_stage_table(result.stages))
    typer.echo(
        "summary "
        f"duration={result.duration_seconds:.3f}s "
        f"errors={result.error_count} "
        f"top={len(result.report_rows)}"
    )
    typer.echo(f"json_out={result.json_out}")


@paper_app.command("step")
def paper_step(
    prices: Path = typer.Option(
        ...,
        "--prices",
        help="CSV path with ticker,date,close columns.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    asof: str = typer.Option(
        ...,
        "--asof",
        help="As-of date (YYYY-MM-DD).",
    ),
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
) -> None:
    """Apply EOD paper-trading state transitions from daily close CSV."""
    repo = Repository(db_path)
    try:
        asof_date = date.fromisoformat(asof)
    except ValueError as exc:
        typer.echo(f"invalid --asof date: {asof}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        price_by_ticker = _load_daily_close_for_date(prices, asof=asof_date)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not price_by_ticker:
        typer.echo(f"asof={asof_date.isoformat()} prices=0 updated=0 new_entries=0 events=0")
        return

    updated_count = 0
    new_entries = 0
    event_count = 0
    for ticker in sorted(price_by_ticker):
        close = price_by_ticker[ticker]
        position = repo.get_paper_position(ticker)
        if position is None:
            repo.upsert_paper_position(
                create_entry_position(
                    ticker=ticker,
                    entry_price=close,
                    entry_date=asof_date,
                )
            )
            updated_count += 1
            new_entries += 1
            continue

        next_position, events = apply_eod_rules(position, close=close, asof=asof_date)
        repo.upsert_paper_position(next_position)
        for event in events:
            repo.insert_paper_event(event)
        updated_count += 1
        event_count += len(events)

    typer.echo(
        "asof="
        f"{asof_date.isoformat()} "
        f"prices={len(price_by_ticker)} "
        f"updated={updated_count} "
        f"new_entries={new_entries} "
        f"events={event_count}"
    )


@universe_app.command("filter")
def universe_filter(
    market_snapshot: Path = typer.Option(
        ...,
        "--market-snapshot",
        help="Path to CSV file with ticker, price, value_traded_5d_avg, is_managed.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    output_path: Path = typer.Option(
        Path("data/eligible_tickers.txt"),
        "--output-path",
        help="Output txt file path for eligible tickers.",
    ),
    config_path: Path = typer.Option(
        Path("config/config.example.yaml"),
        "--config",
        help="Config file path.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Filter tickers from a market snapshot CSV."""
    config = load_config(config_path)
    universe_config = config.universe
    try:
        result = filter_market_snapshot(
            market_snapshot,
            min_price=universe_config.min_price,
            max_price=universe_config.max_price,
            min_value_traded_5d_avg=universe_config.min_value_traded_5d_avg,
            exclude_managed=universe_config.exclude_managed,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_body = "\n".join(result.eligible_tickers)
    if output_body:
        output_body = f"{output_body}\n"
    output_path.write_text(output_body, encoding="utf-8")

    for reason, count in sorted(result.excluded_counts.items()):
        logging.info("universe filter excluded reason=%s count=%d", reason, count)

    typer.echo(
        f"eligible={len(result.eligible_tickers)} total={result.total_rows} output={output_path}"
    )


@debug_app.command("storage")
def debug_storage(
    db_path: Path = typer.Option(
        Path("data/storage/stockotter.db"),
        "--db-path",
        help="SQLite DB file path.",
    ),
    cache_dir: Path = typer.Option(
        Path("data/cache/raw"),
        "--cache-dir",
        help="Raw content file cache directory.",
    ),
) -> None:
    """Run storage smoke test."""
    repo = Repository(db_path)
    cache = FileCache(cache_dir)

    cache_key = "debug:storage"
    cache_value = "smoke_ok"
    cache.set(cache_key, cache_value, ttl_seconds=60)
    cached_value = cache.get(cache_key)

    sample = NewsItem(
        id="debug-news-item",
        source="debug",
        title="storage smoke test",
        url="https://example.com/debug-storage",
        published_at=now_in_seoul(),
        raw_text="storage smoke raw text",
        tickers_mentioned=["005930"],
    )
    repo.upsert_news_item(sample)
    stored_item = repo.get_news_item(sample.id)

    if cached_value != cache_value or stored_item is None:
        typer.echo("storage smoke test failed", err=True)
        raise typer.Exit(code=1)

    typer.echo("storage ok")


def _render_candidate_table(candidates: list[Candidate]) -> str:
    if not candidates:
        return "no candidates found"

    headers = ("rank", "ticker", "score", "reason")
    rows = [
        (
            str(index),
            candidate.ticker,
            f"{candidate.score:.3f}",
            _truncate(candidate.reasons[0] if candidate.reasons else "-", limit=90),
        )
        for index, candidate in enumerate(candidates, start=1)
    ]

    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]

    def _line(values: tuple[str, str, str, str]) -> str:
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


def _load_tickers(path: Path) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        tickers.append(line)
    return tickers


def _load_daily_close_for_date(path: Path, *, asof: date) -> dict[str, float]:
    with path.open(encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        required_columns = {"ticker", "date", "close"}
        if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
            raise ValueError("prices csv must include columns: ticker,date,close")

        date_key = asof.isoformat()
        prices: dict[str, float] = {}
        for row in reader:
            row_date = (row.get("date") or "").strip()
            if row_date != date_key:
                continue

            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                raise ValueError("prices csv has empty ticker row")
            if ticker in prices:
                raise ValueError(f"duplicate ticker for asof date: {ticker}")

            close_raw = (row.get("close") or "").strip()
            try:
                close = float(close_raw)
            except ValueError as exc:
                raise ValueError(f"invalid close value for ticker={ticker}: {close_raw}") from exc
            if close <= 0.0:
                raise ValueError(f"close must be > 0 for ticker={ticker}")
            prices[ticker] = close
    return prices


def main() -> None:
    """CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
