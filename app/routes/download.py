"""Public download endpoint. No authentication: the signed URL is the credential."""

import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

from app import db, signer
from app.deps import ClockDep, DbDep, SettingsDep, StorageDep, client_ip, user_agent
from app.errors import ApiError
from app.schemas import ErrorResponse
from app.timeutil import to_iso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["download"])

# Never let the browser render user content (stored XSS), and never cache it.
DOWNLOAD_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
}


@router.get(
    "/d/{file_id}",
    response_class=FileResponse,
    summary="Download a file through a signed link",
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "File content"},
        403: {"model": ErrorResponse, "description": "Missing, malformed or tampered link"},
        404: {"model": ErrorResponse, "description": "File was deleted"},
        410: {"model": ErrorResponse, "description": "Link has expired"},
    },
)
def download_file(
    request: Request,
    file_id: str,
    conn: DbDep,
    storage: StorageDep,
    settings: SettingsDep,
    clock: ClockDep,
    lid: Annotated[str | None, Query(description="Link id")] = None,
    exp: Annotated[str | None, Query(description="Expiry (unix seconds)")] = None,
    sig: Annotated[str | None, Query(description="Signature")] = None,
) -> FileResponse:
    # Missing or malformed parameters are treated exactly like a bad signature (403).
    result = signer.verify(settings.signing_key, file_id, lid or "", exp or "", sig or "", clock())
    if result is signer.VerifyResult.INVALID_SIGNATURE:
        raise ApiError(403, "invalid_signature", "The download link is invalid")
    if result is signer.VerifyResult.EXPIRED:
        raise ApiError(410, "link_expired", "The download link has expired")

    row = db.get_active_file(conn, file_id)
    if row is None:
        raise ApiError(404, "file_not_found", "The file no longer exists")
    if not storage.exists(file_id):
        logger.error("Blob missing for active file %s", file_id)
        raise ApiError(500, "storage_error", "The file content is unavailable")

    # Best effort: a failed download audit is logged but does not block the download
    # (unlike link creation, which fails closed).
    try:
        with conn:
            db.insert_audit_event(
                conn,
                event_type=db.EVENT_FILE_DOWNLOADED,
                file_id=file_id,
                link_id=lid,
                client_ip=client_ip(request),
                user_agent=user_agent(request),
                created_at=to_iso(clock()),
            )
    except sqlite3.Error:
        logger.exception("Failed to record download of file %s", file_id)

    return FileResponse(
        storage.path_for(file_id),
        media_type="application/octet-stream",
        filename=row["original_filename"],  # sent as Content-Disposition: attachment
        headers=DOWNLOAD_HEADERS,
    )
