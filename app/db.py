"""SQLite persistence: schema and plain parameterized SQL queries (no ORM).

Connection strategy: one short-lived connection per request. FastAPI may run a sync
dependency and the sync endpoint on different threadpool threads, hence
check_same_thread=False. The connection is still used by one request at a time.
"""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id                TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type      TEXT,
    size_bytes        INTEGER NOT NULL,
    sha256            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    deleted_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_owner_created ON files (owner_id, created_at);

-- Append-only: the application only INSERTs and SELECTs from this table.
CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    file_id     TEXT NOT NULL REFERENCES files (id),
    actor_id    TEXT,
    link_id     TEXT,
    expires_at  TEXT,
    client_ip   TEXT,
    user_agent  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_file_created ON audit_events (file_id, created_at);
"""

EVENT_FILE_UPLOADED = "file.uploaded"
EVENT_LINK_CREATED = "link.created"
EVENT_FILE_DOWNLOADED = "file.downloaded"
EVENT_FILE_DELETED = "file.deleted"

# Files plus derived "sharing status" (how many links were issued, and when last).
_FILE_COLUMNS = """
    f.id, f.owner_id, f.original_filename, f.content_type, f.size_bytes, f.sha256,
    f.created_at,
    (SELECT COUNT(*) FROM audit_events a
      WHERE a.file_id = f.id AND a.event_type = 'link.created') AS link_count,
    (SELECT MAX(a.created_at) FROM audit_events a
      WHERE a.file_id = f.id AND a.event_type = 'link.created') AS last_shared_at
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        # WAL improves concurrent reads while a write is in progress; it is a
        # persistent property of the database file.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_file(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    owner_id: str,
    original_filename: str,
    content_type: str | None,
    size_bytes: int,
    sha256: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO files
            (id, owner_id, original_filename, content_type, size_bytes, sha256, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (file_id, owner_id, original_filename, content_type, size_bytes, sha256, created_at),
    )


def get_owned_file(conn: sqlite3.Connection, file_id: str, owner_id: str) -> sqlite3.Row | None:
    """Return an active file only if it belongs to owner_id (None otherwise)."""
    return conn.execute(
        f"SELECT {_FILE_COLUMNS} FROM files f "
        "WHERE f.id = ? AND f.owner_id = ? AND f.deleted_at IS NULL",
        (file_id, owner_id),
    ).fetchone()


def get_active_file(conn: sqlite3.Connection, file_id: str) -> sqlite3.Row | None:
    """Return an active (not deleted) file regardless of owner, for public downloads."""
    return conn.execute(
        "SELECT id, owner_id, original_filename FROM files WHERE id = ? AND deleted_at IS NULL",
        (file_id,),
    ).fetchone()


def list_owned_files(
    conn: sqlite3.Connection, owner_id: str, limit: int, offset: int
) -> tuple[list[sqlite3.Row], int]:
    rows = conn.execute(
        f"SELECT {_FILE_COLUMNS} FROM files f "
        "WHERE f.owner_id = ? AND f.deleted_at IS NULL "
        "ORDER BY f.created_at DESC, f.rowid DESC LIMIT ? OFFSET ?",
        (owner_id, limit, offset),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM files WHERE owner_id = ? AND deleted_at IS NULL",
        (owner_id,),
    ).fetchone()[0]
    return rows, total


def mark_file_deleted(conn: sqlite3.Connection, file_id: str, deleted_at: str) -> None:
    conn.execute(
        "UPDATE files SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
        (deleted_at, file_id),
    )


def insert_audit_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    file_id: str,
    created_at: str,
    actor_id: str | None = None,
    link_id: str | None = None,
    expires_at: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_events
            (event_type, file_id, actor_id, link_id, expires_at, client_ip, user_agent,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_type, file_id, actor_id, link_id, expires_at, client_ip, user_agent, created_at),
    )


def list_audit_events(conn: sqlite3.Connection, file_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, event_type, file_id, actor_id, link_id, expires_at, client_ip, user_agent, "
        "created_at FROM audit_events WHERE file_id = ? ORDER BY id DESC",
        (file_id,),
    ).fetchall()
