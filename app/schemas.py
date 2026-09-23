"""Pydantic request and response models (these also drive the OpenAPI docs)."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class FileMetadata(BaseModel):
    id: str
    filename: str
    size_bytes: int
    sha256: str
    content_type: str | None
    created_at: str
    link_count: int = Field(description="Number of signed links generated for this file")
    last_shared_at: str | None = Field(description="When the most recent link was generated")


class FileList(BaseModel):
    items: list[FileMetadata]
    total: int
    limit: int
    offset: int


class CreateLinkRequest(BaseModel):
    # Reject unknown fields (e.g. a client trying to send its own "expires_at").
    model_config = ConfigDict(extra="forbid")

    # StrictInt: "3600" (string), 1.5 and true are rejected instead of coerced.
    ttl_seconds: StrictInt = Field(description="Link lifetime in seconds", examples=[3600])


class SignedLink(BaseModel):
    url: str
    link_id: str
    file_id: str
    expires_at: str


class AuditEvent(BaseModel):
    id: int
    event_type: str
    file_id: str
    actor_id: str | None
    link_id: str | None
    expires_at: str | None
    client_ip: str | None
    user_agent: str | None
    created_at: str


class AuditEventList(BaseModel):
    items: list[AuditEvent]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
