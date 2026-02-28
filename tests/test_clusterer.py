from __future__ import annotations

from datetime import timedelta

from typer.testing import CliRunner

from stockotter_small.cli import app
from stockotter_v2.clusterer import TfidfClusterer
from stockotter_v2.schemas import NewsItem, now_in_seoul
from stockotter_v2.storage import Repository


def _build_news_item(
    *,
    news_id: str,
    title: str,
    raw_text: str,
    ticker: str,
    minutes_from_base: int,
) -> NewsItem:
    base_time = now_in_seoul() - timedelta(hours=1)
    return NewsItem(
        id=news_id,
        source="unit-test",
        title=title,
        url=f"https://example.com/{news_id}",
        published_at=base_time + timedelta(minutes=minutes_from_base),
        raw_text=raw_text,
        tickers_mentioned=[ticker],
    )


def _sample_news_items() -> list[NewsItem]:
    return [
        _build_news_item(
            news_id="news-001",
            title="삼성전자 반도체 투자 확대",
            raw_text="삼성전자 반도체 투자 확대 계획 발표 생산능력 증설",
            ticker="005930",
            minutes_from_base=0,
        ),
        _build_news_item(
            news_id="news-002",
            title="삼성전자 반도체 투자 확대 계획",
            raw_text="반도체 투자 확대 계획 발표와 생산능력 증설 내용",
            ticker="005930",
            minutes_from_base=3,
        ),
        _build_news_item(
            news_id="news-003",
            title="삼성전자 반도체 투자 확대 소식",
            raw_text="삼성전자 투자 확대와 생산능력 증설에 대한 기사",
            ticker="005930",
            minutes_from_base=6,
        ),
        _build_news_item(
            news_id="news-004",
            title="삼성전자 배당 기준일 공시",
            raw_text="삼성전자가 배당 기준일과 지급 일정을 공시했다",
            ticker="005930",
            minutes_from_base=9,
        ),
        _build_news_item(
            news_id="news-005",
            title="SK하이닉스 반도체 투자 확대",
            raw_text="하이닉스 반도체 투자 확대 계획 발표 생산능력 증설",
            ticker="000660",
            minutes_from_base=4,
        ),
    ]


def test_tfidf_clusterer_groups_duplicates_and_picks_earliest_representative() -> None:
    clusterer = TfidfClusterer(similarity_threshold=0.35, representative_policy="earliest")

    clusters = clusterer.cluster(_sample_news_items())

    assert len(clusters) == 3

    duplicate_cluster = next(
        cluster
        for cluster in clusters
        if cluster.member_news_ids == ["news-001", "news-002", "news-003"]
    )
    assert duplicate_cluster.representative_news_id == "news-001"


def test_tfidf_clusterer_keyword_policy_selects_keyword_rich_article() -> None:
    items = [
        _build_news_item(
            news_id="news-a",
            title="전기차 배터리 공급 계약",
            raw_text="전기차 배터리 공급 계약 체결",
            ticker="066570",
            minutes_from_base=0,
        ),
        _build_news_item(
            news_id="news-b",
            title="전기차 배터리 공급 계약 확대",
            raw_text="전기차 배터리 공급 계약 확대 리튬 니켈 코발트 양극재",
            ticker="066570",
            minutes_from_base=1,
        ),
        _build_news_item(
            news_id="news-c",
            title="전기차 배터리 공급",
            raw_text="전기차 배터리 공급 뉴스",
            ticker="066570",
            minutes_from_base=2,
        ),
    ]

    clusterer = TfidfClusterer(similarity_threshold=0.2, representative_policy="keyword")
    clusters = clusterer.cluster(items)

    assert len(clusters) == 1
    assert clusters[0].representative_news_id == "news-b"


def test_cli_cluster_saves_clusters_to_db(tmp_path) -> None:
    repo = Repository(tmp_path / "storage.db")
    for item in _sample_news_items():
        repo.upsert_news_item(item)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "cluster",
            "--since-hours",
            "24",
            "--db-path",
            str(repo.db_path),
            "--similarity-threshold",
            "0.35",
        ],
    )

    assert result.exit_code == 0
    assert "clusters=3 news=5" in result.output

    stored_clusters = repo.list_clusters()
    assert len(stored_clusters) == 3
    assert any(
        cluster.member_news_ids == ["news-001", "news-002", "news-003"]
        and cluster.representative_news_id == "news-001"
        for cluster in stored_clusters
    )
