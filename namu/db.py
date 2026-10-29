"""SQLite storage for Namu (stdlib sqlite3 only).

Default location is ``data/namu.db`` next to the package; override with the
``NAMU_DATA_DIR`` environment variable or by passing explicit paths (tests do).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    sha256        TEXT NOT NULL UNIQUE,
    filename      TEXT NOT NULL,
    uploaded_at   TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    -- statements of this file the user deleted (keys "inst|type|last4|start|end"),
    -- so a reparse does not resurrect them
    excluded_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    institution TEXT NOT NULL,
    product     TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL,
    last4       TEXT NOT NULL,
    holder      TEXT,
    UNIQUE (institution, type, last4)
);

CREATE TABLE IF NOT EXISTS statements (
    id           INTEGER PRIMARY KEY,
    file_id      INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    account_id   INTEGER NOT NULL REFERENCES accounts(id),
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    parser       TEXT NOT NULL,
    reconciled   INTEGER,                       -- 1 / 0 / NULL (unverified)
    checks_json  TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    -- every parsed txn of this statement (incl. ones de-duplicated against another
    -- statement), used to restore rows when an overlapping statement is deleted
    txns_json    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_statements_account ON statements(account_id);
CREATE INDEX IF NOT EXISTS ix_statements_file ON statements(file_id);

CREATE TABLE IF NOT EXISTS transactions (
    id                INTEGER PRIMARY KEY,
    fingerprint       TEXT NOT NULL UNIQUE,
    account_id        INTEGER NOT NULL REFERENCES accounts(id),
    statement_id      INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
    date              TEXT NOT NULL,
    post_date         TEXT,
    description       TEXT NOT NULL,
    extra             TEXT,
    merchant          TEXT NOT NULL DEFAULT '',
    amount_cents      INTEGER NOT NULL,
    section           TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'expense',
    category          TEXT NOT NULL DEFAULT 'Other',
    category_source   TEXT NOT NULL DEFAULT 'auto',
    cardholder        TEXT,
    transfer_match_id INTEGER,
    recurring         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_txn_statement ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS ix_txn_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS rules (
    merchant   TEXT PRIMARY KEY COLLATE NOCASE,
    category   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS overrides (
    fingerprint TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def default_data_dir() -> Path:
    env = os.environ.get("NAMU_DATA_DIR")
    return Path(env).expanduser().resolve() if env else ROOT / "data"


def db_path_for(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir or default_data_dir()) / "namu.db"


def uploads_dir_for(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir or default_data_dir()) / "uploads"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn
