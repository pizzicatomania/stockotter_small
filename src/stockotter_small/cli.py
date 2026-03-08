from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import date
from pathlib import Path

import requests
import typer

from stockotter_small.broker.kis import (
    KISAPIError,
    KISAuthError,
    KISClient,
    KISClientError,
    KISPosition,
    KISRateLimitError,
    OrderService,
)
from stockotter_small.telegram import (
    CallbackExecutionRequest,
    TelegramClient,
    TelegramClientError,
    build_briefing_candidates,
    build_inline_keyboard_and_actions,
    finalize_callback_execution,
    format_briefing_message,
    parse_callback_update,
    persist_tg_actions,
    process_callback_action,
)
from stockotter_v2 import load_config
from stockotter_v2.clusterer import TfidfClusterer
from stockotter_v2.config import TradingConfig
from stockotter_v2.llm import GeminiClient, LLMStructurer, evaluate_samples, load_eval_samples
from stockotter_v2.news.naver_fetcher import NaverNewsFetcher
from stockotter_v2.paper import apply_eod_rules, create_entry_position
from stockotter_v2.pipeline import (
    render_report_table,
    render_stage_table,
    run_pipeline,
)
from stockotter_v2.schemas import BrokerOrder, Candidate, NewsItem, OrderStatus, now_in_seoul
from stockotter_v2.scoring import RuleBasedScorer, build_score_weights
from stockotter_v2.storage import FileCache, Repository
from stockotter_v2.universe import filter_market_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = typer.Typer(help="StockOtter Small CLI")
debug_app = typer.Typer(help="Debug commands")
tg_app = typer.Typer(help="Telegram commands")
universe_app = typer.Typer(help="Universe commands")
paper_app = typer.Typer(help="Paper trading commands")
kis_app = typer.Typer(help="KIS broker commands")
app.add_typer(debug_app, name="debug")
app.add_typer(tg_app, name="tg")
app.add_typer(universe_app, name="universe")
app.add_typer(paper_app, name="paper")
app.add_typer(kis_app, name="kis")


@app.command()
def hello(name: str = typer.Option("world", "--name", "-n", help="Name to greet.")) -> None:
    """Simple smoke command."""
    logging.info("hello command invoked")
    typer.echo(f"hello, {name}")


@kis_app.command("auth-test")
def kis_auth_test(
    ticker: str = typer.Option(
        "005930",
        "--ticker",
        help="조회할 종목코드 (기본값: 005930).",
    ),
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Test KIS authentication and run a harmless quote endpoint call."""
    client = _build_kis_client_or_exit(cache_path=cache_path)

    token = client.token_manager.get_token()
    typer.echo(
        "token "
        f"env={client.environment} "
        f"expires_at={token.expires_at.isoformat()} "
        f"cache={client.cache_path}"
    )

    try:
        result = client.auth_test_quote(ticker=ticker)
    except requests.HTTPError as exc:
        response = exc.response
        status = response.status_code if response is not None else "unknown"
        if status in {404, 405}:
            typer.echo(f"harmless_call=skipped status={status} reason=endpoint_unavailable")
            return
        typer.echo(f"harmless_call=failed status={status}", err=True)
        raise typer.Exit(code=1) from exc
    except KISAPIError as exc:
        if exc.status_code in {404, 405}:
            typer.echo(
                f"harmless_call=skipped status={exc.status_code} reason=endpoint_unavailable"
            )
            return
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    except KISClientError as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if result.output_code not in {None, "0"}:
        typer.echo(
            "harmless_call=failed "
            f"status={result.status_code} "
            f"rt_cd={result.output_code} "
            f"msg={result.output_message or '-'}",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(
        "harmless_call=ok "
        f"status={result.status_code} "
        f"ticker={ticker} "
        f"name={result.stock_name or '-'} "
        f"price={result.current_price or '-'}"
    )


@kis_app.command("price")
def kis_price(
    ticker: str = typer.Argument(..., help="조회할 종목코드 (예: 005930)."),
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Get current price for a ticker via KIS quote API."""
    client = _build_kis_client_or_exit(cache_path=cache_path)
    try:
        quote = client.get_price(ticker)
    except (ValueError, KISClientError) as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        "price "
        f"ticker={quote.ticker} "
        f"name={quote.name} "
        f"current={quote.current_price} "
        f"prev_close={quote.previous_close if quote.previous_close is not None else '-'} "
        f"change={quote.change if quote.change is not None else '-'} "
        f"change_rate={quote.change_rate if quote.change_rate is not None else '-'}"
    )


@kis_app.command("positions")
def kis_positions(
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Get account balance and holding positions via KIS account inquiry API."""
    client = _build_kis_client_or_exit(cache_path=cache_path)
    try:
        balance = client.get_balance()
        positions = client.get_positions()
    except (KISClientError, ValueError) as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        "balance "
        f"purchase={balance.total_purchase_amount} "
        f"eval={balance.total_eval_amount} "
        f"pnl={balance.total_profit_loss_amount} "
        f"pnl_rate={balance.total_profit_loss_rate} "
        f"cash={balance.cash_available if balance.cash_available is not None else '-'}"
    )

    typer.echo(_render_positions_table(positions))


@kis_app.command("buy-market")
def kis_buy_market(
    ticker: str = typer.Argument(..., help="매수할 종목코드 (예: 005930)."),
    cash_amount: int = typer.Option(..., "--cash-amount", min=1, help="사용할 현금 금액."),
    confirm: bool = typer.Option(False, "--confirm", help="실제 주문을 전송합니다."),
    live: bool = typer.Option(False, "--live", help="live 환경 실주문 실행을 허용합니다."),
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
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Create a buy market order. Default is dry-run until --confirm is set."""
    service = _build_order_service_or_exit(
        db_path=db_path,
        cache_path=cache_path,
        config_path=config_path,
    )
    try:
        order = service.place_buy_market(
            ticker=ticker,
            cash_amount=cash_amount,
            confirm=confirm,
            allow_live=live,
        )
    except (KISClientError, ValueError) as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    _exit_if_order_failed(order)


@kis_app.command("buy-limit")
def kis_buy_limit(
    ticker: str = typer.Argument(..., help="매수할 종목코드 (예: 005930)."),
    qty: int = typer.Option(..., "--qty", min=1, help="주문 수량."),
    price: int = typer.Option(..., "--price", min=1, help="지정가."),
    confirm: bool = typer.Option(False, "--confirm", help="실제 주문을 전송합니다."),
    live: bool = typer.Option(False, "--live", help="live 환경 실주문 실행을 허용합니다."),
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
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Create a buy limit order. Default is dry-run until --confirm is set."""
    service = _build_order_service_or_exit(
        db_path=db_path,
        cache_path=cache_path,
        config_path=config_path,
    )
    try:
        order = service.place_buy_limit(
            ticker=ticker,
            qty=qty,
            price=price,
            confirm=confirm,
            allow_live=live,
        )
    except (KISClientError, ValueError) as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    _exit_if_order_failed(order)


@kis_app.command("sell-market")
def kis_sell_market(
    ticker: str = typer.Argument(..., help="매도할 종목코드 (예: 005930)."),
    qty: int = typer.Option(..., "--qty", min=1, help="주문 수량."),
    confirm: bool = typer.Option(False, "--confirm", help="실제 주문을 전송합니다."),
    live: bool = typer.Option(False, "--live", help="live 환경 실주문 실행을 허용합니다."),
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
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Create a sell market order. Default is dry-run until --confirm is set."""
    service = _build_order_service_or_exit(
        db_path=db_path,
        cache_path=cache_path,
        config_path=config_path,
    )
    try:
        order = service.place_sell_market(
            ticker=ticker,
            qty=qty,
            confirm=confirm,
            allow_live=live,
        )
    except (KISClientError, ValueError) as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    _exit_if_order_failed(order)


@kis_app.command("sell-limit")
def kis_sell_limit(
    ticker: str = typer.Argument(..., help="매도할 종목코드 (예: 005930)."),
    qty: int = typer.Option(..., "--qty", min=1, help="주문 수량."),
    price: int = typer.Option(..., "--price", min=1, help="지정가."),
    confirm: bool = typer.Option(False, "--confirm", help="실제 주문을 전송합니다."),
    live: bool = typer.Option(False, "--live", help="live 환경 실주문 실행을 허용합니다."),
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
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="토큰 캐시 파일 경로 (기본값: data/cache/kis/token_<env>.json).",
    ),
) -> None:
    """Create a sell limit order. Default is dry-run until --confirm is set."""
    service = _build_order_service_or_exit(
        db_path=db_path,
        cache_path=cache_path,
        config_path=config_path,
    )
    try:
        order = service.place_sell_limit(
            ticker=ticker,
            qty=qty,
            price=price,
            confirm=confirm,
            allow_live=live,
        )
    except (KISClientError, ValueError) as exc:
        typer.echo(_format_kis_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    _exit_if_order_failed(order)


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
    config_path: Path = typer.Option(
        Path("config/config.example.yaml"),
        "--config",
        help="Config file path.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Fetch news from configured sources and store into SQLite."""
    tickers = _load_tickers(tickers_file)
    if not tickers:
        typer.echo("no valid tickers found in file", err=True)
        raise typer.Exit(code=1)

    config = load_config(config_path)
    quality = config.news_quality
    repo = Repository(db_path)
    cache = FileCache(cache_dir)
    fetcher = NaverNewsFetcher(
        cache=cache,
        sleep_seconds=sleep_seconds,
        sources=config.sources,
        ticker_map_path=quality.ticker_map_path,
        noise_patterns=quality.noise_patterns,
        noise_min_title_length=quality.min_title_length,
        enable_noise_filter=quality.enabled,
        drop_duplicate_titles=quality.drop_duplicate_titles,
    )
    items = fetcher.fetch_recent_for_tickers(tickers, hours=hours)

    stored = 0
    for item in items:
        try:
            repo.upsert_news_item(item)
            stored += 1
        except Exception:
            logging.exception("failed to upsert news url=%s", item.url)

    summary_only_count = sum(1 for item in items if item.raw_text.startswith("[summary_only] "))
    typer.echo(
        "stored "
        f"{stored} items "
        f"(fetched={len(items)}, summary_only={summary_only_count}, tickers={len(tickers)}) "
        f"sources={len([source for source in config.sources if source.enabled])}"
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
    api_key_env: str | None = typer.Option(
        None,
        "--api-key-env",
        help="Gemini API key 환경변수 이름. 미지정 시 config.llm.api_key_env 사용.",
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

    resolved_api_key_env = (api_key_env or config.llm.api_key_env).strip()
    try:
        client = GeminiClient.from_env(
            model=config.llm.model,
            fallback_model=config.llm.fallback_model,
            temperature=config.llm.temperature,
            env_var=resolved_api_key_env,
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
    typer.echo(f"processed={stats.processed} failed={stats.failed} skipped={stats.skipped}")


@app.command("llm-eval")
def llm_eval(
    dataset: str = typer.Option(
        "data/llm_eval/*.json",
        "--dataset",
        help="Glob pattern for evaluation dataset JSON files.",
    ),
    report: Path = typer.Option(
        Path("out/llm_eval.json"),
        "--report",
        help="Output report JSON path.",
    ),
    mode: str = typer.Option(
        "recorded",
        "--mode",
        help="Evaluation mode: recorded or mock.",
    ),
    recorded_field: str = typer.Option(
        "recorded_output",
        "--recorded-field",
        help="Field name for recorded predictions (e.g. recorded_output, baseline_output).",
    ),
    compare_baseline: bool = typer.Option(
        False,
        "--compare-baseline",
        help="Run baseline comparison using baseline_output field.",
    ),
) -> None:
    """Run StructuredEvent extraction evaluation harness."""
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"recorded", "mock"}:
        typer.echo("mode must be one of: recorded, mock", err=True)
        raise typer.Exit(code=1)

    try:
        samples = load_eval_samples(dataset)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    report_payload = evaluate_samples(
        samples,
        mode=normalized_mode,  # type: ignore[arg-type]
        recorded_field=recorded_field,
    )

    if compare_baseline and normalized_mode == "recorded":
        baseline_payload = evaluate_samples(
            samples,
            mode="recorded",
            recorded_field="baseline_output",
        )
        metrics = report_payload["metrics"]
        baseline_metrics = baseline_payload["metrics"]
        report_payload["baseline"] = {
            "recorded_field": "baseline_output",
            "metrics": baseline_metrics,
            "delta": {
                "event_type_accuracy": round(
                    metrics["event_type_accuracy"] - baseline_metrics["event_type_accuracy"],
                    6,
                ),
                "direction_accuracy": round(
                    metrics["direction_accuracy"] - baseline_metrics["direction_accuracy"],
                    6,
                ),
                "horizon_accuracy": round(
                    metrics["horizon_accuracy"] - baseline_metrics["horizon_accuracy"],
                    6,
                ),
                "risk_flags_precision": round(
                    metrics["risk_flags_precision"] - baseline_metrics["risk_flags_precision"],
                    6,
                ),
                "risk_flags_recall": round(
                    metrics["risk_flags_recall"] - baseline_metrics["risk_flags_recall"],
                    6,
                ),
            },
        }

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metrics = report_payload["metrics"]
    typer.echo(
        "llm_eval "
        f"samples={metrics['sample_count']} "
        f"evaluated={metrics['evaluated_count']} "
        f"errors={metrics['error_count']} "
        f"event_type_acc={metrics['event_type_accuracy']:.3f} "
        f"direction_acc={metrics['direction_accuracy']:.3f} "
        f"horizon_acc={metrics['horizon_accuracy']:.3f} "
        f"risk_precision={metrics['risk_flags_precision']:.3f} "
        f"risk_recall={metrics['risk_flags_recall']:.3f}"
    )
    typer.echo(f"report={report}")


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
    api_key_env: str | None = typer.Option(
        None,
        "--api-key-env",
        help="Gemini API key 환경변수 이름. 미지정 시 config.llm.api_key_env 사용.",
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
    quality = config.news_quality
    repo = Repository(db_path)
    cache = FileCache(cache_dir)
    fetcher = NaverNewsFetcher(
        cache=cache,
        sleep_seconds=sleep_seconds,
        sources=config.sources,
        ticker_map_path=quality.ticker_map_path,
        noise_patterns=quality.noise_patterns,
        noise_min_title_length=quality.min_title_length,
        enable_noise_filter=quality.enabled,
        drop_duplicate_titles=quality.drop_duplicate_titles,
    )

    if config.llm.provider.lower() != "gemini":
        logging.warning(
            "llm.provider=%s, but run command currently uses Gemini client.",
            config.llm.provider,
        )

    resolved_api_key_env = (api_key_env or config.llm.api_key_env).strip()
    try:
        client = GeminiClient.from_env(
            model=config.llm.model,
            fallback_model=config.llm.fallback_model,
            temperature=config.llm.temperature,
            env_var=resolved_api_key_env,
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


@tg_app.command("send-briefing")
def tg_send_briefing(
    asof: str = typer.Option(
        ...,
        "--asof",
        help="Briefing date (YYYY-MM-DD).",
    ),
    top: int = typer.Option(
        10,
        "--top",
        help="Maximum number of candidates to include.",
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
) -> None:
    """Send the latest candidate briefing to Telegram."""
    repo = Repository(db_path)
    try:
        asof_date = date.fromisoformat(asof)
    except ValueError as exc:
        typer.echo(f"invalid --asof date: {asof}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        config = load_config(config_path)
        briefing_candidates = build_briefing_candidates(repo=repo, asof=asof_date, limit=top)
        message = format_briefing_message(asof=asof_date, candidates=briefing_candidates)
        reply_markup, actions = build_inline_keyboard_and_actions(
            candidates=briefing_candidates,
            buy_cash_amount=config.trading.telegram_default_buy_cash_amount,
            sell_quantity=config.trading.telegram_default_sell_quantity,
        )
        client = TelegramClient.from_env()
        result = client.send_message(message, reply_markup=reply_markup)
        persist_tg_actions(repo=repo, actions=actions, message_id=result.message_id)
    except (TelegramClientError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        "telegram sent "
        f"asof={asof_date.isoformat()} "
        f"candidates={len(briefing_candidates)} "
        f"message_id={result.message_id if result.message_id is not None else '-'}"
    )


@tg_app.command("handle-callback")
def tg_handle_callback(
    update_json: Path = typer.Option(
        ...,
        "--update-json",
        help="Telegram update JSON file containing callback_query payload.",
        exists=True,
        dir_okay=False,
        readable=True,
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
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="KIS token cache file path.",
    ),
    live: bool = typer.Option(False, "--live", help="live 환경 실주문 실행을 허용합니다."),
) -> None:
    """Handle Telegram inline-button callback payload."""
    raw_payload = update_json.read_text(encoding="utf-8")
    try:
        client = TelegramClient.from_env()
        summary = _handle_telegram_callback_raw_payload(
            raw_payload=raw_payload,
            client=client,
            db_path=db_path,
            config_path=config_path,
            cache_path=cache_path,
            allow_live=live,
        )
    except (TelegramClientError, KISClientError, ValueError) as exc:
        typer.echo(_format_telegram_callback_error(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(summary)


@tg_app.command("poll-callbacks")
def tg_poll_callbacks(
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
    cache_path: Path | None = typer.Option(
        None,
        "--cache-path",
        help="KIS token cache file path.",
    ),
    live: bool = typer.Option(False, "--live", help="live 환경 실주문 실행을 허용합니다."),
    offset_file: Path = typer.Option(
        Path("data/cache/telegram/offset.txt"),
        "--offset-file",
        help="Telegram getUpdates offset state file.",
    ),
    poll_timeout: int = typer.Option(
        20,
        "--poll-timeout",
        min=0,
        help="Telegram getUpdates long-poll timeout seconds.",
    ),
    idle_sleep_seconds: float = typer.Option(
        1.0,
        "--idle-sleep-seconds",
        min=0.0,
        help="Sleep seconds after a poll error before retrying.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Process at most one poll batch and exit.",
    ),
) -> None:
    """Continuously poll Telegram callback updates and process them immediately."""
    client = TelegramClient.from_env()
    offset = _load_telegram_offset(offset_file)
    typer.echo(
        "telegram polling "
        f"offset={offset if offset is not None else '-'} "
        f"timeout={poll_timeout} "
        f"offset_file={offset_file}"
    )

    while True:
        try:
            result = client.get_updates(
                offset=offset,
                timeout=poll_timeout,
                allowed_updates=["callback_query"],
            )
        except (TelegramClientError, ValueError) as exc:
            typer.echo(_format_telegram_callback_error(exc), err=True)
            if once:
                raise typer.Exit(code=1) from exc
            time.sleep(idle_sleep_seconds)
            continue

        if not result.updates:
            if once:
                typer.echo("telegram polling no_updates")
                return
            continue

        for update in result.updates:
            update_id = _extract_update_id(update)
            raw_payload = json.dumps(update, ensure_ascii=False)
            try:
                summary = _handle_telegram_callback_raw_payload(
                    raw_payload=raw_payload,
                    client=client,
                    db_path=db_path,
                    config_path=config_path,
                    cache_path=cache_path,
                    allow_live=live,
                )
                typer.echo(f"update_id={update_id} {summary}")
            except (TelegramClientError, KISClientError, ValueError) as exc:
                typer.echo(
                    f"update_id={update_id} {_format_telegram_callback_error(exc)}",
                    err=True,
                )
            finally:
                offset = update_id + 1
                _save_telegram_offset(offset_file, offset)

        if once:
            return


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


def _handle_telegram_callback_raw_payload(
    *,
    raw_payload: str,
    client: TelegramClient,
    db_path: Path,
    config_path: Path,
    cache_path: Path | None,
    allow_live: bool,
) -> str:
    envelope = parse_callback_update(raw_payload)
    client.answer_callback_query(envelope.callback_query_id)

    repo = Repository(db_path)
    try:
        config = load_config(config_path)
        result = process_callback_action(
            repo=repo,
            action_id=envelope.action_id,
            callback_query_id=envelope.callback_query_id,
            message_id=envelope.message_id,
            message_text=envelope.message_text,
            environment=_resolve_kis_environment(),
            paper_one_step_enabled=config.trading.telegram_paper_one_step_enabled,
            default_buy_cash_amount=config.trading.telegram_default_buy_cash_amount,
            default_sell_quantity=config.trading.telegram_default_sell_quantity,
        )
        if result.execution_request is not None:
            final_result = _execute_telegram_callback_order(
                execution_request=result.execution_request,
                cache_path=cache_path,
                db_path=db_path,
                config=config,
                repo=repo,
                message_text=result.message_text,
                allow_live=allow_live,
            )
            client.edit_message_text(
                message_id=envelope.message_id,
                text=final_result.message_text,
                reply_markup=final_result.reply_markup,
            )
            return (
                "telegram callback "
                f"action_id={final_result.action.action_id} "
                f"type={final_result.action.action_type.value} "
                f"ticker={final_result.action.ticker} "
                f"status={final_result.action.status.value} "
                "order_id="
                f"{final_result.order.order_id if final_result.order is not None else '-'}"
            )

        client.edit_message_text(
            message_id=envelope.message_id,
            text=result.message_text,
            reply_markup=result.reply_markup,
        )
        return (
            "telegram callback "
            f"action_id={result.action.action_id} "
            f"type={result.action.action_type.value} "
            f"ticker={result.action.ticker} "
            f"status={result.action.status.value} "
            "order_intent_id="
            f"{result.intent.intent_id if result.intent is not None else '-'}"
        )
    except (TelegramClientError, KISClientError, ValueError) as exc:
        _try_edit_callback_error_message(
            client=client,
            envelope=envelope,
            repo=repo,
            error_message=_format_telegram_callback_error(exc),
        )
        raise


def _load_telegram_offset(offset_file: Path) -> int | None:
    if not offset_file.exists():
        return None
    raw_value = offset_file.read_text(encoding="utf-8").strip()
    if not raw_value:
        return None
    try:
        offset = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"invalid telegram offset file: {offset_file}") from exc
    if offset < 0:
        raise ValueError(f"invalid telegram offset file: {offset_file}")
    return offset


def _save_telegram_offset(offset_file: Path, offset: int) -> None:
    if offset < 0:
        raise ValueError("offset must be >= 0")
    offset_file.parent.mkdir(parents=True, exist_ok=True)
    offset_file.write_text(f"{offset}\n", encoding="utf-8")


def _extract_update_id(update: dict[str, object]) -> int:
    raw_update_id = update.get("update_id")
    if not isinstance(raw_update_id, int):
        raise ValueError("telegram update missing integer update_id")
    if raw_update_id < 0:
        raise ValueError("telegram update_id must be >= 0")
    return raw_update_id


def _build_kis_client_or_exit(*, cache_path: Path | None) -> KISClient:
    try:
        return KISClient.from_env(cache_path=cache_path)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _build_order_service(
    *,
    db_path: Path,
    cache_path: Path | None,
    trading_config: TradingConfig,
) -> OrderService:
    return OrderService.from_env(
        db_path=db_path,
        cache_path=cache_path,
        trading_config=trading_config,
    )


def _build_order_service_or_exit(
    *,
    db_path: Path,
    cache_path: Path | None,
    config_path: Path,
) -> OrderService:
    try:
        config = load_config(config_path)
        return _build_order_service(
            db_path=db_path,
            cache_path=cache_path,
            trading_config=config.trading,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _execute_telegram_callback_order(
    *,
    execution_request: CallbackExecutionRequest,
    cache_path: Path | None,
    db_path: Path,
    config: object,
    repo: Repository,
    message_text: str,
    allow_live: bool,
):
    try:
        service = _build_order_service(
            db_path=db_path,
            cache_path=cache_path,
            trading_config=config.trading,
        )
        parent_action = execution_request.parent_action
        if parent_action.action_type.value == "buy":
            cash_amount = execution_request.intent.cash_amount
            if cash_amount is None:
                raise ValueError("telegram buy action missing cash_amount")
            order = service.place_buy_market(
                ticker=parent_action.ticker,
                cash_amount=cash_amount,
                confirm=True,
                allow_live=allow_live,
            )
        else:
            quantity = execution_request.intent.quantity
            if quantity is None:
                raise ValueError("telegram sell action missing quantity")
            order = service.place_sell_market(
                ticker=parent_action.ticker,
                qty=quantity,
                confirm=True,
                allow_live=allow_live,
            )
        return finalize_callback_execution(
            repo=repo,
            execution_request=execution_request,
            message_text=message_text,
            order=order,
        )
    except (KISClientError, ValueError) as exc:
        return finalize_callback_execution(
            repo=repo,
            execution_request=execution_request,
            message_text=message_text,
            order=None,
            error_message=_format_telegram_callback_error(exc),
        )


def _format_telegram_callback_error(exc: Exception) -> str:
    if isinstance(exc, KISClientError):
        return _format_kis_error(exc)
    return str(exc)


def _try_edit_callback_error_message(
    *,
    client: TelegramClient,
    envelope,
    repo: Repository,
    error_message: str,
) -> None:
    try:
        action = repo.get_tg_action(envelope.action_id)
        ticker = action.ticker if action is not None else "-"
        action_name = action.action_type.value if action is not None else "-"
        base_text = envelope.message_text.split("\n\n[Telegram Action]\n", maxsplit=1)[0].strip()
        lines = [
            base_text,
            "",
            "[Telegram Action]",
            f"ticker={ticker}",
            f"action={action_name}",
            "status=failed",
            f"detail={error_message}",
        ]
        message_text = "\n".join(line for line in lines if line != "" or base_text)
        client.edit_message_text(message_id=envelope.message_id, text=message_text)
    except Exception:
        return


def _resolve_kis_environment() -> str:
    return os.getenv("KIS_ENV", "paper").strip().lower() or "paper"


def _format_kis_error(exc: Exception) -> str:
    if isinstance(exc, KISRateLimitError):
        return f"kis_error=rate_limit detail={exc}"
    if isinstance(exc, KISAuthError):
        return f"kis_error=auth detail={exc}"
    if isinstance(exc, KISClientError):
        return f"kis_error=api detail={exc}"
    return str(exc)


def _render_positions_table(positions: list[KISPosition]) -> str:
    if not positions:
        return "positions=0"

    headers = ("ticker", "name", "qty", "current", "pnl", "pnl_rate")
    rows = [
        (
            position.ticker,
            position.name,
            str(position.quantity),
            "-" if position.current_price is None else str(position.current_price),
            "-" if position.profit_loss_amount is None else str(position.profit_loss_amount),
            "-" if position.profit_loss_rate is None else str(position.profit_loss_rate),
        )
        for position in positions
    ]
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]

    def _line(values: tuple[str, str, str, str, str, str]) -> str:
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

    divider = "-+-".join("-" * width for width in widths)
    body = [_line(headers), divider]
    body.extend(_line(row) for row in rows)
    return "\n".join(body)


def _render_order_record(order: BrokerOrder) -> str:
    return (
        "order "
        f"order_id={order.order_id} "
        f"env={order.environment} "
        f"side={order.side.value} "
        f"type={order.order_type.value} "
        f"ticker={order.ticker} "
        f"qty={order.quantity} "
        f"price={order.price if order.price is not None else '-'} "
        f"cash_amount={order.cash_amount if order.cash_amount is not None else '-'} "
        f"status={order.status.value} "
        f"external_order_id={order.external_order_id or '-'} "
        f"order_time={order.external_order_time or '-'} "
        f"note={order.note or '-'}"
    )


def _exit_if_order_failed(order: BrokerOrder) -> None:
    typer.echo(_render_order_record(order))
    if order.status is OrderStatus.REJECTED:
        raise typer.Exit(code=1)


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
        return " | ".join(value.ljust(widths[index]) for index, value in enumerate(values))

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
