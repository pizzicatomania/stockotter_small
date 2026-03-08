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
from stockotter_v2.schemas import (
    BrokerOrder,
    Candidate,
    Cluster,
    NewsItem,
    OrderIntent,
    OrderIntentStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    StructuredEvent,
    TelegramAction,
    TelegramActionStatus,
    TelegramActionType,
    now_in_seoul,
)


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

    def list_structured_events_by_news_id(self, news_id: str) -> list[StructuredEvent]:
        query = """
        SELECT news_id, event_type, direction, confidence, horizon, themes, entities, risk_flags
        FROM structured_events
        WHERE news_id = ?
        ORDER BY id DESC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (news_id,)).fetchall()
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
        snapshot_at = now_in_seoul().isoformat()
        query = """
        INSERT INTO candidates (
            ticker, score, reasons, supporting_news_ids, themes, risk_flags, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        payloads = [
            (
                candidate.ticker,
                candidate.score,
                json.dumps(candidate.reasons, ensure_ascii=False),
                json.dumps(candidate.supporting_news_ids, ensure_ascii=False),
                json.dumps(candidate.themes, ensure_ascii=False),
                json.dumps(candidate.risk_flags, ensure_ascii=False),
                snapshot_at,
                snapshot_at,
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

    def get_candidate_snapshot_date(self) -> date | None:
        query = """
        SELECT updated_at
        FROM candidates
        ORDER BY updated_at DESC, ticker ASC
        LIMIT 1
        """
        with self._connect() as conn:
            row = conn.execute(query).fetchone()

        if row is None or not row["updated_at"]:
            return None
        return date.fromisoformat(str(row["updated_at"])[:10])

    def insert_tg_action(self, action: TelegramAction) -> None:
        payload = (
            action.action_id,
            action.action_type.value,
            action.ticker,
            action.quantity,
            action.cash_amount,
            action.parent_action_id,
            action.created_at.isoformat(),
            action.status.value,
            action.message_id,
            action.callback_query_id,
        )
        query = """
        INSERT INTO tg_actions (
            action_id, action_type, ticker, quantity, cash_amount, parent_action_id,
            created_at, status, message_id, callback_query_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(action_id) DO UPDATE SET
            action_type=excluded.action_type,
            ticker=excluded.ticker,
            quantity=excluded.quantity,
            cash_amount=excluded.cash_amount,
            parent_action_id=excluded.parent_action_id,
            created_at=excluded.created_at,
            status=excluded.status,
            message_id=excluded.message_id,
            callback_query_id=excluded.callback_query_id
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def get_tg_action(self, action_id: str) -> TelegramAction | None:
        query = """
        SELECT
            action_id, action_type, ticker, quantity, cash_amount, parent_action_id,
            created_at, status, message_id, callback_query_id
        FROM tg_actions
        WHERE action_id = ?
        """
        with self._connect() as conn:
            row = conn.execute(query, (action_id,)).fetchone()

        if row is None:
            return None
        return self._row_to_tg_action(row)

    def list_tg_actions(self, limit: int | None = None) -> list[TelegramAction]:
        query = """
        SELECT
            action_id, action_type, ticker, quantity, cash_amount, parent_action_id,
            created_at, status, message_id, callback_query_id
        FROM tg_actions
        ORDER BY created_at DESC, action_id DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_tg_action(row) for row in rows]

    def list_tg_child_actions(self, parent_action_id: str) -> list[TelegramAction]:
        query = """
        SELECT
            action_id, action_type, ticker, quantity, cash_amount, parent_action_id,
            created_at, status, message_id, callback_query_id
        FROM tg_actions
        WHERE parent_action_id = ?
        ORDER BY created_at ASC, action_id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(query, (parent_action_id,)).fetchall()
        return [self._row_to_tg_action(row) for row in rows]

    def update_tg_action_status(
        self,
        *,
        action_id: str,
        status: TelegramActionStatus,
        callback_query_id: str | None = None,
    ) -> None:
        params: list[object] = [status.value]
        query = "UPDATE tg_actions SET status = ?"
        if callback_query_id is not None:
            query += ", callback_query_id = ?"
            params.append(callback_query_id)
        query += " WHERE action_id = ?"
        params.append(action_id)
        with self._connect() as conn:
            conn.execute(query, tuple(params))

    def insert_order_intent(self, intent: OrderIntent) -> None:
        payload = (
            intent.intent_id,
            intent.action_id,
            intent.action_type.value,
            intent.ticker,
            intent.quantity,
            intent.cash_amount,
            1 if intent.is_dry_run else 0,
            intent.status.value,
            intent.note,
            intent.created_at.isoformat(),
        )
        query = """
        INSERT INTO order_intents (
            intent_id, action_id, action_type, ticker, quantity, cash_amount,
            is_dry_run, status, note, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(action_id) DO UPDATE SET
            intent_id=excluded.intent_id,
            action_type=excluded.action_type,
            ticker=excluded.ticker,
            quantity=excluded.quantity,
            cash_amount=excluded.cash_amount,
            is_dry_run=excluded.is_dry_run,
            status=excluded.status,
            note=excluded.note,
            created_at=excluded.created_at
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def update_order_intent(
        self,
        *,
        action_id: str,
        status: OrderIntentStatus,
        is_dry_run: bool | None = None,
        note: str | None = None,
    ) -> None:
        params: list[object] = [status.value]
        query = "UPDATE order_intents SET status = ?"
        if is_dry_run is not None:
            query += ", is_dry_run = ?"
            params.append(int(is_dry_run))
        if note is not None:
            query += ", note = ?"
            params.append(note)
        query += " WHERE action_id = ?"
        params.append(action_id)
        with self._connect() as conn:
            conn.execute(query, tuple(params))

    def get_order_intent_by_action(self, action_id: str) -> OrderIntent | None:
        query = """
        SELECT
            intent_id, action_id, action_type, ticker, quantity, cash_amount,
            is_dry_run, status, note, created_at
        FROM order_intents
        WHERE action_id = ?
        """
        with self._connect() as conn:
            row = conn.execute(query, (action_id,)).fetchone()

        if row is None:
            return None
        return self._row_to_order_intent(row)

    def list_order_intents(self, limit: int | None = None) -> list[OrderIntent]:
        query = """
        SELECT
            intent_id, action_id, action_type, ticker, quantity, cash_amount,
            is_dry_run, status, note, created_at
        FROM order_intents
        ORDER BY created_at DESC, intent_id DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_order_intent(row) for row in rows]

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

    def upsert_order(self, order: BrokerOrder) -> None:
        payload = (
            order.order_id,
            order.broker,
            order.environment,
            order.ticker,
            order.side.value,
            order.order_type.value,
            order.quantity,
            order.price,
            order.cash_amount,
            order.status.value,
            int(order.is_dry_run),
            json.dumps(order.request_payload, ensure_ascii=False, sort_keys=True),
            json.dumps(order.response_payload, ensure_ascii=False, sort_keys=True),
            order.external_order_id,
            order.external_order_time,
            order.note,
            order.created_at.isoformat(),
            order.updated_at.isoformat(),
            order.submitted_at.isoformat() if order.submitted_at is not None else None,
        )
        query = """
        INSERT INTO orders (
            order_id, broker, environment, ticker, side, order_type, quantity, price,
            cash_amount, status, is_dry_run, request_payload, response_payload,
            external_order_id, external_order_time, note, created_at, updated_at, submitted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            broker=excluded.broker,
            environment=excluded.environment,
            ticker=excluded.ticker,
            side=excluded.side,
            order_type=excluded.order_type,
            quantity=excluded.quantity,
            price=excluded.price,
            cash_amount=excluded.cash_amount,
            status=excluded.status,
            is_dry_run=excluded.is_dry_run,
            request_payload=excluded.request_payload,
            response_payload=excluded.response_payload,
            external_order_id=excluded.external_order_id,
            external_order_time=excluded.external_order_time,
            note=excluded.note,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at,
            submitted_at=excluded.submitted_at
        """
        with self._connect() as conn:
            conn.execute(query, payload)

    def get_order(self, order_id: str) -> BrokerOrder | None:
        query = """
        SELECT
            order_id, broker, environment, ticker, side, order_type, quantity, price,
            cash_amount, status, is_dry_run, request_payload, response_payload,
            external_order_id, external_order_time, note, created_at, updated_at, submitted_at
        FROM orders
        WHERE order_id = ?
        """
        with self._connect() as conn:
            row = conn.execute(query, (order_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_order(row)

    def list_orders(self, limit: int | None = None) -> list[BrokerOrder]:
        query = """
        SELECT
            order_id, broker, environment, ticker, side, order_type, quantity, price,
            cash_amount, status, is_dry_run, request_payload, response_payload,
            external_order_id, external_order_time, note, created_at, updated_at, submitted_at
        FROM orders
        ORDER BY created_at DESC, order_id DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_order(row) for row in rows]

    def count_orders_for_day(
        self,
        *,
        order_date: date | str,
        environment: str | None = None,
        include_dry_run: bool = False,
    ) -> int:
        date_key = order_date.isoformat() if isinstance(order_date, date) else order_date
        query = """
        SELECT COUNT(*) AS order_count
        FROM orders
        WHERE substr(created_at, 1, 10) = ?
        """
        params: list[object] = [date_key]
        if environment is not None:
            query += " AND environment = ?"
            params.append(environment)
        if not include_dry_run:
            query += " AND is_dry_run = 0"

        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return int(row["order_count"]) if row is not None else 0

    def sum_order_cash_for_day(
        self,
        *,
        order_date: date | str,
        environment: str | None = None,
        side: OrderSide | None = None,
        include_dry_run: bool = False,
    ) -> int:
        date_key = order_date.isoformat() if isinstance(order_date, date) else order_date
        query = """
        SELECT COALESCE(SUM(COALESCE(cash_amount, price * quantity, 0)), 0) AS total_cash
        FROM orders
        WHERE substr(created_at, 1, 10) = ?
        """
        params: list[object] = [date_key]
        if environment is not None:
            query += " AND environment = ?"
            params.append(environment)
        if side is not None:
            query += " AND side = ?"
            params.append(side.value)
        if not include_dry_run:
            query += " AND is_dry_run = 0"

        with self._connect() as conn:
            row = conn.execute(query, tuple(params)).fetchone()
        return int(row["total_cash"]) if row is not None else 0

    def _init_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        schema = schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema)
            self._apply_compat_migrations(conn)

    @staticmethod
    def _apply_compat_migrations(conn: sqlite3.Connection) -> None:
        tg_action_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(tg_actions)").fetchall()
        }
        if "parent_action_id" not in tg_action_columns:
            conn.execute("ALTER TABLE tg_actions ADD COLUMN parent_action_id TEXT")

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
    def _row_to_tg_action(row: sqlite3.Row) -> TelegramAction:
        return TelegramAction(
            action_id=row["action_id"],
            action_type=TelegramActionType(row["action_type"]),
            ticker=row["ticker"],
            quantity=row["quantity"],
            cash_amount=row["cash_amount"],
            parent_action_id=row["parent_action_id"],
            created_at=row["created_at"],
            status=TelegramActionStatus(row["status"]),
            message_id=row["message_id"],
            callback_query_id=row["callback_query_id"],
        )

    @staticmethod
    def _row_to_order_intent(row: sqlite3.Row) -> OrderIntent:
        return OrderIntent(
            intent_id=row["intent_id"],
            action_id=row["action_id"],
            action_type=TelegramActionType(row["action_type"]),
            ticker=row["ticker"],
            quantity=row["quantity"],
            cash_amount=row["cash_amount"],
            is_dry_run=bool(row["is_dry_run"]),
            status=OrderIntentStatus(row["status"]),
            note=row["note"],
            created_at=row["created_at"],
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

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> BrokerOrder:
        return BrokerOrder(
            order_id=row["order_id"],
            broker=row["broker"],
            environment=row["environment"],
            ticker=row["ticker"],
            side=OrderSide(row["side"]),
            order_type=OrderType(row["order_type"]),
            quantity=row["quantity"],
            price=row["price"],
            cash_amount=row["cash_amount"],
            status=OrderStatus(row["status"]),
            is_dry_run=bool(row["is_dry_run"]),
            request_payload=json.loads(row["request_payload"]),
            response_payload=json.loads(row["response_payload"]),
            external_order_id=row["external_order_id"],
            external_order_time=row["external_order_time"],
            note=row["note"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            submitted_at=row["submitted_at"],
        )
