"""Signed link generation (requires X-API-Key and file ownership)."""

import logging
import secrets
import sqlite3
from urllib.parse import urlencode

from fastapi import APIRouter, Request

from app import db, signer
from app.auth import CurrentUser
from app.deps import ClockDep, DbDep, SettingsDep, client_ip, user_agent
from app.errors import ApiError
from app.routes.common import require_owned_file
from app.schemas import CreateLinkRequest, ErrorResponse, SignedLink
from app.timeutil import to_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/files", tags=["links"])


@router.post(
    "/{file_id}/links",
    status_code=201,
    response_model=SignedLink,
    summary="Generate a temporary signed download link",
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse, "description": "Audit event could not be recorded"},
    },
)
def create_link(
    request: Request,
    file_id: str,
    body: CreateLinkRequest,
    user: CurrentUser,
    conn: DbDep,
    settings: SettingsDep,
    clock: ClockDep,
) -> SignedLink:
    require_owned_file(conn, file_id, user)

    ttl = body.ttl_seconds
    if not settings.min_ttl_seconds <= ttl <= settings.max_ttl_seconds:
        raise ApiError(
            422,
            "invalid_ttl",
            f"ttl_seconds must be between {settings.min_ttl_seconds} "
            f"and {settings.max_ttl_seconds}",
        )

    now = clock()
    expires_at = int(now) + ttl  # computed server-side; clients cannot choose it
    link_id = secrets.token_hex(16)
    signature = signer.sign(settings.signing_key, file_id, link_id, expires_at)
    query = urlencode({"lid": link_id, "exp": expires_at, "sig": signature})
    url = f"{settings.public_base_url}/d/{file_id}?{query}"
    expires_iso = to_iso(expires_at)

    # Fail closed: if the audit event cannot be written, the link is not issued.
    try:
        with conn:
            db.insert_audit_event(
                conn,
                event_type=db.EVENT_LINK_CREATED,
                file_id=file_id,
                actor_id=user,
                link_id=link_id,
                expires_at=expires_iso,
                client_ip=client_ip(request),
                user_agent=user_agent(request),
                created_at=to_iso(now),
            )
    except sqlite3.Error:
        logger.exception("Failed to record link.created for file %s", file_id)
        raise ApiError(
            500, "audit_write_failed", "Could not record audit event; link was not issued"
        ) from None

    logger.info("Link %s created for file %s by %s (ttl=%ds)", link_id, file_id, user, ttl)
    return SignedLink(url=url, link_id=link_id, file_id=file_id, expires_at=expires_iso)
