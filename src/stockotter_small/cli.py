from __future__ import annotations

import logging
from pathlib import Path

import typer

from stockotter_v2 import load_config
from stockotter_v2.llm import GeminiClient, LLMStructurer
from stockotter_v2.news.naver_fetcher import NaverNewsFetcher
from stockotter_v2.schemas import NewsItem, now_in_seoul
from stockotter_v2.storage import FileCache, Repository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = typer.Typer(help="StockOtter Small CLI")
debug_app = typer.Typer(help="Debug commands")
app.add_typer(debug_app, name="debug")


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


def main() -> None:
    """CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
