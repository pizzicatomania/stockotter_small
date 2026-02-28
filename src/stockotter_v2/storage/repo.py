from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from stockotter_v2.paper.positions import (
    PaperEvent,
    PaperEventType,
    PaperPosition,
    PositionState,
)
from stockotter_v2.schemas import Candidate, Cluster, NewsItem, StructuredEvent, now_in_seoul


class Repository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def upsert_news_item(self, item: NewsItem) -> None:
        payload = (
            item.id,
            item.source,
            item.title,
            item.url,
            item.published_at.isoformat(),
            item.raw_text,
            json.dumps(item.tickers_mentioned, ensure_ascii=False),
            item.fetched_at.isoformat(),
        )
        query = """
        INSERT INTO news_items (
            id, source, title, url, published_at, raw_text, tickers_mentioned, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            id=excluded.id,
            source=excluded.source,
            title=excluded.title,
            published_at=excluded.published_at,
            raw_text=excluded.raw_text,
            tickers_mentioned=excluded.tickers_mentioned,
            fetched_at=excluded.fetched_at
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def list_news_items(self, limit: int | None = None) -> list[NewsItem]:
        query = """
        SELECT id, source, title, url, published_at, raw_text, tickers_mentioned, fetched_at
        FROM news_items
        ORDER BY published_at DESC, id DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_news_item(row) for row in rows]

    def get_news_item(self, news_id: str) -> NewsItem | None:
        query = """
        SELECT id, source, title, url, published_at, raw_text, tickers_mentioned, fetched_at
        FROM news_items
        WHERE id = ?
        """
        with self._connect() as conn:
            row = conn.execute(query, (news_id,)).fetchone()

        if row is None:
            return None
        return self._row_to_news_item(row)

    def list_news_items_without_event(self, *, since_hours: int = 24) -> list[NewsItem]:
        if since_hours < 1:
            raise ValueError("since_hours must be >= 1")

        cutoff = (now_in_seoul() - timedelta(hours=since_hours)).isoformat()
        query = """
        SELECT n.id, n.source, n.title, n.url, n.published_at, n.raw_text,
               n.tickers_mentioned, n.fetched_at
        FROM news_items n
        LEFT JOIN structured_events e ON e.news_id = n.id
        WHERE e.news_id IS NULL
          AND n.published_at >= ?
        ORDER BY n.published_at DESC, n.id DESC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (cutoff,)).fetchall()

        return [self._row_to_news_item(row) for row in rows]

    def list_news_items_since_hours(self, *, since_hours: int = 24) -> list[NewsItem]:
        if since_hours < 1:
            raise ValueError("since_hours must be >= 1")

        cutoff = (now_in_seoul() - timedelta(hours=since_hours)).isoformat()
        query = """
        SELECT id, source, title, url, published_at, raw_text, tickers_mentioned, fetched_at
        FROM news_items
        WHERE published_at >= ?
        ORDER BY published_at ASC, id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (cutoff,)).fetchall()

        return [self._row_to_news_item(row) for row in rows]

    def upsert_structured_event(self, event: StructuredEvent) -> None:
        payload = (
            event.news_id,
            event.event_type,
            event.direction,
            event.confidence,
            event.horizon,
            json.dumps(event.themes, ensure_ascii=False),
            json.dumps(event.entities, ensure_ascii=False),
            json.dumps(event.risk_flags, ensure_ascii=False),
        )
        query = """
        INSERT INTO structured_events (
            news_id, event_type, direction, confidence, horizon, themes, entities, risk_flags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(news_id, event_type, direction, horizon) DO UPDATE SET
            confidence=excluded.confidence,
            themes=excluded.themes,
            entities=excluded.entities,
            risk_flags=excluded.risk_flags
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def upsert_cluster(self, cluster: Cluster) -> None:
        payload = (
            cluster.cluster_id,
            cluster.representative_news_id,
            json.dumps(cluster.member_news_ids, ensure_ascii=False),
            cluster.summary,
        )
        query = """
        INSERT INTO clusters (
            cluster_id, representative_news_id, member_news_ids, summary
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(cluster_id) DO UPDATE SET
            representative_news_id=excluded.representative_news_id,
            member_news_ids=excluded.member_news_ids,
            summary=excluded.summary,
            updated_at=CURRENT_TIMESTAMP
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def list_clusters(self, limit: int | None = None) -> list[Cluster]:
        query = """
        SELECT cluster_id, representative_news_id, member_news_ids, summary
        FROM clusters
        ORDER BY cluster_id ASC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_cluster(row) for row in rows]

    def list_events_by_date(self, event_date: date | str) -> list[StructuredEvent]:
        date_key = event_date.isoformat() if isinstance(event_date, date) else event_date
        query = """
        SELECT e.news_id, e.event_type, e.direction, e.confidence, e.horizon,
               e.themes, e.entities, e.risk_flags
        FROM structured_events e
        INNER JOIN news_items n ON n.id = e.news_id
        WHERE substr(n.published_at, 1, 10) = ?
        ORDER BY n.published_at DESC, e.id DESC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (date_key,)).fetchall()

        return [self._row_to_structured_event(row) for row in rows]

    def list_representative_structured_events_since_hours(
        self,
        *,
        since_hours: int = 24,
    ) -> list[tuple[NewsItem, StructuredEvent]]:
        if since_hours < 1:
            raise ValueError("since_hours must be >= 1")

        cutoff = (now_in_seoul() - timedelta(hours=since_hours)).isoformat()
        query = """
        SELECT
            n.id AS n_id,
            n.source AS n_source,
            n.title AS n_title,
            n.url AS n_url,
            n.published_at AS n_published_at,
            n.raw_text AS n_raw_text,
            n.tickers_mentioned AS n_tickers_mentioned,
            n.fetched_at AS n_fetched_at,
            e.news_id AS e_news_id,
            e.event_type AS e_event_type,
            e.direction AS e_direction,
            e.confidence AS e_confidence,
            e.horizon AS e_horizon,
            e.themes AS e_themes,
            e.entities AS e_entities,
            e.risk_flags AS e_risk_flags
        FROM (
            SELECT DISTINCT representative_news_id
            FROM clusters
        ) c
        INNER JOIN news_items n ON n.id = c.representative_news_id
        INNER JOIN structured_events e ON e.news_id = n.id
        WHERE n.published_at >= ?
        ORDER BY n.published_at DESC, n.id DESC, e.id DESC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (cutoff,)).fetchall()

        events: list[tuple[NewsItem, StructuredEvent]] = []
        for row in rows:
            news_item = NewsItem(
                id=row["n_id"],
                source=row["n_source"],
                title=row["n_title"],
                url=row["n_url"],
                published_at=row["n_published_at"],
                raw_text=row["n_raw_text"],
                tickers_mentioned=json.loads(row["n_tickers_mentioned"]),
                fetched_at=row["n_fetched_at"],
            )
            event = StructuredEvent(
                news_id=row["e_news_id"],
                event_type=row["e_event_type"],
                direction=row["e_direction"],
                confidence=row["e_confidence"],
                horizon=row["e_horizon"],
                themes=json.loads(row["e_themes"]),
                entities=json.loads(row["e_entities"]),
                risk_flags=json.loads(row["e_risk_flags"]),
            )
            events.append((news_item, event))
        return events

    def replace_candidates(self, candidates: list[Candidate]) -> None:
        query = """
        INSERT INTO candidates (
            ticker, score, reasons, supporting_news_ids, themes, risk_flags
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """
        payloads = [
            (
                candidate.ticker,
                candidate.score,
                json.dumps(candidate.reasons, ensure_ascii=False),
                json.dumps(candidate.supporting_news_ids, ensure_ascii=False),
                json.dumps(candidate.themes, ensure_ascii=False),
                json.dumps(candidate.risk_flags, ensure_ascii=False),
            )
            for candidate in candidates
        ]
        with self._connect() as conn:
            conn.execute("DELETE FROM candidates")
            if payloads:
                conn.executemany(query, payloads)

    def list_candidates(self, limit: int | None = None) -> list[Candidate]:
        query = """
        SELECT ticker, score, reasons, supporting_news_ids, themes, risk_flags
        FROM candidates
        ORDER BY score DESC, ticker ASC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_candidate(row) for row in rows]

    def get_paper_position(self, ticker: str) -> PaperPosition | None:
        query = """
        SELECT
            ticker, state, entry_price, qty_total, qty_remaining, entry_date,
            last_close, updated_at, highest_close_since_tp, exit_price, exit_date, sideways_days
        FROM paper_positions
        WHERE ticker = ?
        """
        with self._connect() as conn:
            row = conn.execute(query, (ticker,)).fetchone()

        if row is None:
            return None
        return self._row_to_paper_position(row)

    def upsert_paper_position(self, position: PaperPosition) -> None:
        payload = (
            position.ticker,
            position.state.value,
            position.entry_price,
            position.qty_total,
            position.qty_remaining,
            position.entry_date.isoformat(),
            position.last_close,
            position.updated_at.isoformat(),
            position.highest_close_since_tp,
            position.exit_price,
            position.exit_date.isoformat() if position.exit_date is not None else None,
            position.sideways_days,
        )
        query = """
        INSERT INTO paper_positions (
            ticker, state, entry_price, qty_total, qty_remaining, entry_date,
            last_close, updated_at, highest_close_since_tp, exit_price, exit_date, sideways_days
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            state=excluded.state,
            entry_price=excluded.entry_price,
            qty_total=excluded.qty_total,
            qty_remaining=excluded.qty_remaining,
            entry_date=excluded.entry_date,
            last_close=excluded.last_close,
            updated_at=excluded.updated_at,
            highest_close_since_tp=excluded.highest_close_since_tp,
            exit_price=excluded.exit_price,
            exit_date=excluded.exit_date,
            sideways_days=excluded.sideways_days
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def list_open_paper_positions(self) -> list[PaperPosition]:
        query = """
        SELECT
            ticker, state, entry_price, qty_total, qty_remaining, entry_date,
            last_close, updated_at, highest_close_since_tp, exit_price, exit_date, sideways_days
        FROM paper_positions
        WHERE state != ?
        ORDER BY ticker ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (PositionState.EXITED.value,)).fetchall()

        return [self._row_to_paper_position(row) for row in rows]

    def insert_paper_event(self, event: PaperEvent) -> None:
        payload = (
            event.ticker,
            event.event_date.isoformat(),
            event.event_type.value,
            event.price,
            event.quantity,
            event.state_before.value,
            event.state_after.value,
            event.note,
        )
        query = """
        INSERT INTO paper_events (
            ticker, event_date, event_type, price, quantity, state_before, state_after, note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def list_paper_events(
        self,
        *,
        ticker: str | None = None,
        limit: int | None = None,
    ) -> list[PaperEvent]:
        query = """
        SELECT ticker, event_date, event_type, price, quantity, state_before, state_after, note
        FROM paper_events
        """
        params: list[object] = []
        if ticker is not None:
            query += " WHERE ticker = ?"
            params.append(ticker)
        query += " ORDER BY event_date ASC, id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        return [self._row_to_paper_event(row) for row in rows]

    def _init_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        schema = schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _row_to_news_item(row: sqlite3.Row) -> NewsItem:
        return NewsItem(
            id=row["id"],
            source=row["source"],
            title=row["title"],
            url=row["url"],
            published_at=row["published_at"],
            raw_text=row["raw_text"],
            tickers_mentioned=json.loads(row["tickers_mentioned"]),
            fetched_at=row["fetched_at"],
        )

    @staticmethod
    def _row_to_structured_event(row: sqlite3.Row) -> StructuredEvent:
        return StructuredEvent(
            news_id=row["news_id"],
            event_type=row["event_type"],
            direction=row["direction"],
            confidence=row["confidence"],
            horizon=row["horizon"],
            themes=json.loads(row["themes"]),
            entities=json.loads(row["entities"]),
            risk_flags=json.loads(row["risk_flags"]),
        )

    @staticmethod
    def _row_to_cluster(row: sqlite3.Row) -> Cluster:
        return Cluster(
            cluster_id=row["cluster_id"],
            representative_news_id=row["representative_news_id"],
            member_news_ids=json.loads(row["member_news_ids"]),
            summary=row["summary"],
        )

    @staticmethod
    def _row_to_candidate(row: sqlite3.Row) -> Candidate:
        return Candidate(
            ticker=row["ticker"],
            score=row["score"],
            reasons=json.loads(row["reasons"]),
            supporting_news_ids=json.loads(row["supporting_news_ids"]),
            themes=json.loads(row["themes"]),
            risk_flags=json.loads(row["risk_flags"]),
        )

    @staticmethod
    def _row_to_paper_position(row: sqlite3.Row) -> PaperPosition:
        return PaperPosition(
            ticker=row["ticker"],
            state=PositionState(row["state"]),
            entry_price=row["entry_price"],
            qty_total=row["qty_total"],
            qty_remaining=row["qty_remaining"],
            entry_date=row["entry_date"],
            last_close=row["last_close"],
            updated_at=row["updated_at"],
            highest_close_since_tp=row["highest_close_since_tp"],
            exit_price=row["exit_price"],
            exit_date=row["exit_date"],
            sideways_days=row["sideways_days"],
        )

    @staticmethod
    def _row_to_paper_event(row: sqlite3.Row) -> PaperEvent:
        return PaperEvent(
            ticker=row["ticker"],
            event_date=row["event_date"],
            event_type=PaperEventType(row["event_type"]),
            price=row["price"],
            quantity=row["quantity"],
            state_before=PositionState(row["state_before"]),
            state_after=PositionState(row["state_after"]),
            note=row["note"],
        )
