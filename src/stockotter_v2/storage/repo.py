from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from stockotter_v2.schemas import NewsItem, StructuredEvent, now_in_seoul


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
