PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    tg_user_id     INTEGER NOT NULL UNIQUE,
    username       TEXT,
    created_at     TEXT NOT NULL,
    is_banned      INTEGER NOT NULL DEFAULT 0,
    invited_by     INTEGER REFERENCES users(id),
    notify_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL REFERENCES users(id),
    max_uses   INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    keyword_raw  TEXT NOT NULL,
    keyword_norm TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rules_user ON rules(user_id);

CREATE TABLE IF NOT EXISTS items (
    guid         TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    title_norm   TEXT NOT NULL,
    link         TEXT NOT NULL,
    author       TEXT,
    category     TEXT,
    published_at TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    excerpt      TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);

CREATE TABLE IF NOT EXISTS deliveries (
    id         INTEGER PRIMARY KEY,
    item_guid  TEXT NOT NULL REFERENCES items(guid) ON DELETE CASCADE,
    rule_id    INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    sent_at    TEXT,
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    UNIQUE(item_guid, rule_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_pending ON deliveries(status, user_id);

CREATE TABLE IF NOT EXISTS stats_daily (
    day   TEXT NOT NULL,
    key   TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
