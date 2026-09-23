"""FastAPI dependencies that expose per-app resources stored on app.state.

Keeping resources on app.state (instead of module globals) lets tests and the
restart test run several independent app instances side by side.
"""

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from app import db
from app.config import Settings
from app.storage import BlobStorage
from app.timeutil import Clock


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> BlobStorage:
    return request.app.state.storage


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[BlobStorage, Depends(get_storage)]
ClockDep = Annotated[Clock, Depends(get_clock)]
DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    # Note: behind a reverse proxy this is the proxy address. Trusting
    # X-Forwarded-For requires a known proxy setup, so it is intentionally ignored.
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:512] if value else None
