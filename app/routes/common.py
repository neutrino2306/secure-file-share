"""Helpers shared by route modules."""

import sqlite3

from app import db
from app.errors import ApiError


def require_owned_file(conn: sqlite3.Connection, file_id: str, owner_id: str) -> sqlite3.Row:
    """Return the caller's active file or raise 404.

    Files owned by someone else also return 404 (not 403) so that callers cannot
    probe which file ids exist.
    """
    row = db.get_owned_file(conn, file_id, owner_id)
    if row is None:
        raise ApiError(404, "file_not_found", "File not found")
    return row
