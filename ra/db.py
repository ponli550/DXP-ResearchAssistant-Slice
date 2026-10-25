import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    title TEXT NOT NULL,
    source TEXT,
    pages INTEGER NOT NULL,
    needs_ocr INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id INTEGER NOT NULL REFERENCES documents(id),
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    low_text INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, page)
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id),
    page INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(text, content='passages', content_rowid='id');
CREATE TABLE IF NOT EXISTS grants (
    user TEXT NOT NULL,
    library_id TEXT NOT NULL REFERENCES libraries(id),
    PRIMARY KEY (user, library_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    user TEXT NOT NULL,
    audit_id INTEGER NOT NULL REFERENCES audit(id),
    rating TEXT NOT NULL CHECK (rating IN ('up','down')),
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    user TEXT NOT NULL,
    audit_id INTEGER NOT NULL REFERENCES audit(id),
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    user TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_used TEXT,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS layouts (
    owner TEXT PRIMARY KEY,
    doc TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY,
    user TEXT NOT NULL,
    name TEXT NOT NULL,
    questions TEXT NOT NULL,
    doc_ids TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS analysis_cells (
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    qi INTEGER NOT NULL,
    doc_id INTEGER NOT NULL,
    audit_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (analysis_id, qi, doc_id)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    user TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    cited TEXT NOT NULL
);
"""


class Connection(sqlite3.Connection):
    """Plain sqlite3 connection that also accepts attributes, so tests can attach reports."""


def connect(path=":memory:"):
    con = sqlite3.connect(path, factory=Connection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    # Columns added after the first release: added in place so an existing database keeps its rows.
    if "answer" not in {r["name"] for r in con.execute("PRAGMA table_info(audit)")}:
        con.execute("ALTER TABLE audit ADD COLUMN answer TEXT")
        con.commit()
    return con
