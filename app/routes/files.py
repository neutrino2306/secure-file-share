"""Owner-facing file endpoints (all require X-API-Key)."""

import logging
import sqlite3
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, Response, UploadFile

from app import db
from app.auth import CurrentUser
from app.deps import ClockDep, DbDep, SettingsDep, StorageDep, client_ip, user_agent
from app.errors import ApiError
from app.filenames import sanitize_filename
from app.routes.common import require_owned_file
from app.schemas import AuditEvent, AuditEventList, ErrorResponse, FileList, FileMetadata
from app.storage import EmptyFileError, FileTooLargeError
from app.timeutil import to_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/files", tags=["files"])

_AUTH_ERRORS = {401: {"model": ErrorResponse}}
_NOT_FOUND = {404: {"model": ErrorResponse}}


def _to_metadata(row: sqlite3.Row) -> FileMetadata:
    return FileMetadata(
        id=row["id"],
        filename=row["original_filename"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        content_type=row["content_type"],
        created_at=row["created_at"],
        link_count=row["link_count"],
        last_shared_at=row["last_shared_at"],
    )


@router.post(
    "",
    status_code=201,
    response_model=FileMetadata,
    summary="Upload a private file",
    responses={
        **_AUTH_ERRORS,
        400: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
    },
)
def upload_file(
    request: Request,
    file: Annotated[UploadFile, File(description="The file to upload")],
    user: CurrentUser,
    conn: DbDep,
    storage: StorageDep,
    settings: SettingsDep,
    clock: ClockDep,
) -> FileMetadata:
    file_id = uuid.uuid4().hex
    filename = sanitize_filename(file.filename)
    # Informational only: downloads are always served as application/octet-stream.
    content_type = file.content_type[:255] if file.content_type else None

    try:
        blob = storage.save(file_id, file.file, settings.max_upload_bytes)
    except FileTooLargeError:
        raise ApiError(
            413,
            "file_too_large",
            f"File exceeds the maximum size of {settings.max_upload_bytes} bytes",
        ) from None
    except EmptyFileError:
        raise ApiError(400, "empty_file", "Uploaded file is empty") from None
    except OSError:
        logger.exception("Failed to store upload %s", file_id)
        raise ApiError(500, "storage_error", "Could not store the file") from None

    created_at = to_iso(clock())
    try:
        with conn:  # one transaction: file row + audit event
            db.insert_file(
                conn,
                file_id=file_id,
                owner_id=user,
                original_filename=filename,
                content_type=content_type,
                size_bytes=blob.size_bytes,
                sha256=blob.sha256,
                created_at=created_at,
            )
            db.insert_audit_event(
                conn,
                event_type=db.EVENT_FILE_UPLOADED,
                file_id=file_id,
                actor_id=user,
                client_ip=client_ip(request),
                user_agent=user_agent(request),
                created_at=created_at,
            )
    except sqlite3.Error:
        # Keep the invariant "a DB row implies the blob exists" by removing the blob.
        storage.delete(file_id)
        logger.exception("Failed to record upload %s", file_id)
        raise ApiError(500, "database_error", "Could not record the file") from None

    logger.info("File %s uploaded by %s (%d bytes)", file_id, user, blob.size_bytes)
    return FileMetadata(
        id=file_id,
        filename=filename,
        size_bytes=blob.size_bytes,
        sha256=blob.sha256,
        content_type=content_type,
        created_at=created_at,
        link_count=0,
        last_shared_at=None,
    )


@router.get(
    "",
    response_model=FileList,
    summary="List your files (newest first)",
    responses=_AUTH_ERRORS,
)
def list_files(
    user: CurrentUser,
    conn: DbDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FileList:
    rows, total = db.list_owned_files(conn, user, limit, offset)
    return FileList(items=[_to_metadata(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get(
    "/{file_id}",
    response_model=FileMetadata,
    summary="Get metadata and sharing status of one of your files",
    responses={**_AUTH_ERRORS, **_NOT_FOUND},
)
def get_file(file_id: str, user: CurrentUser, conn: DbDep) -> FileMetadata:
    return _to_metadata(require_owned_file(conn, file_id, user))


@router.delete(
    "/{file_id}",
    status_code=204,
    response_class=Response,
    summary="Delete one of your files (existing links stop working)",
    responses={**_AUTH_ERRORS, **_NOT_FOUND},
)
def delete_file(
    request: Request,
    file_id: str,
    user: CurrentUser,
    conn: DbDep,
    storage: StorageDep,
    clock: ClockDep,
) -> Response:
    require_owned_file(conn, file_id, user)
    now = to_iso(clock())
    # Mark deleted first: from this point every link for the file returns 404.
    with conn:
        db.mark_file_deleted(conn, file_id, now)
        db.insert_audit_event(
            conn,
            event_type=db.EVENT_FILE_DELETED,
            file_id=file_id,
            actor_id=user,
            client_ip=client_ip(request),
            user_agent=user_agent(request),
            created_at=now,
        )
    storage.delete(file_id)
    logger.info("File %s deleted by %s", file_id, user)
    return Response(status_code=204)


@router.get(
    "/{file_id}/audit-events",
    response_model=AuditEventList,
    summary="List audit events for one of your files (newest first)",
    responses={**_AUTH_ERRORS, **_NOT_FOUND},
)
def list_file_audit_events(file_id: str, user: CurrentUser, conn: DbDep) -> AuditEventList:
    require_owned_file(conn, file_id, user)
    rows = db.list_audit_events(conn, file_id)
    return AuditEventList(items=[AuditEvent(**dict(r)) for r in rows])
