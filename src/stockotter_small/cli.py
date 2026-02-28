from __future__ import annotations

import logging
from pathlib import Path

import typer

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


def main() -> None:
    """CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
