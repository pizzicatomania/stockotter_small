CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    published_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    tickers_mentioned TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_items_published_at ON news_items (published_at);

CREATE TABLE IF NOT EXISTS structured_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    horizon TEXT NOT NULL,
    themes TEXT NOT NULL DEFAULT '[]',
    entities TEXT NOT NULL DEFAULT '[]',
    risk_flags TEXT NOT NULL DEFAULT '[]',
    UNIQUE (news_id, event_type, direction, horizon),
    FOREIGN KEY (news_id) REFERENCES news_items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_structured_events_news_id ON structured_events (news_id);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT NOT NULL UNIQUE,
    representative_news_id TEXT NOT NULL,
    member_news_ids TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (representative_news_id) REFERENCES news_items(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    score REAL NOT NULL,
    reasons TEXT NOT NULL DEFAULT '[]',
    supporting_news_ids TEXT NOT NULL DEFAULT '[]',
    themes TEXT NOT NULL DEFAULT '[]',
    risk_flags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_positions (
    ticker TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    entry_price REAL NOT NULL,
    qty_total REAL NOT NULL,
    qty_remaining REAL NOT NULL,
    entry_date TEXT NOT NULL,
    last_close REAL NOT NULL,
    updated_at TEXT NOT NULL,
    highest_close_since_tp REAL,
    exit_price REAL,
    exit_date TEXT,
    sideways_days INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_state ON paper_positions (state);

CREATE TABLE IF NOT EXISTS paper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    state_before TEXT NOT NULL,
    state_after TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_paper_events_ticker_date ON paper_events (ticker, event_date);
