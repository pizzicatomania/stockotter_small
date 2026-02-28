from __future__ import annotations

import logging

import typer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = typer.Typer(help="StockOtter Small CLI")


@app.command()
def hello(name: str = typer.Option("world", "--name", "-n", help="Name to greet.")) -> None:
    """Simple smoke command."""
    logging.info("hello command invoked")
    typer.echo(f"hello, {name}")


def main() -> None:
    """CLI entrypoint."""
    app()


if __name__ == "__main__":
    main()
