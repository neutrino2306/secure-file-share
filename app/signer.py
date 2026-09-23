"""HMAC-SHA256 signing and verification for download links.

This module is pure: no I/O, no global state, and the current time is passed in.
That keeps it trivial to unit test (including expiry) without sleeping.

Canonical string (fields joined by "\\n"):

    v1
    <file_id>
    <link_id>
    <expires_at as unix seconds>

signature = base64url(HMAC-SHA256(secret, canonical)) without "=" padding.

Because the signature covers file_id, link_id and expires_at, changing any of them
(e.g. extending the expiry or pointing the link at another file) invalidates it.
The secret comes from configuration, so links stay valid across restarts.
"""

import base64
import hashlib
import hmac
import re
from enum import Enum

SIGNATURE_VERSION = "v1"

_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_EXPIRES_PATTERN = re.compile(r"[0-9]{1,12}")
_MAX_SIGNATURE_LENGTH = 64


class VerifyResult(Enum):
    VALID = "valid"
    INVALID_SIGNATURE = "invalid_signature"
    EXPIRED = "expired"


def is_valid_id(value: str) -> bool:
    """Return True if value is a 32-char lowercase hex id (file ids and link ids)."""
    return bool(_ID_PATTERN.fullmatch(value))


def _canonical_bytes(file_id: str, link_id: str, expires_at: int) -> bytes:
    return "\n".join([SIGNATURE_VERSION, file_id, link_id, str(expires_at)]).encode("utf-8")


def sign(secret: bytes, file_id: str, link_id: str, expires_at: int) -> str:
    """Return the URL-safe signature for the given link fields."""
    digest = hmac.new(secret, _canonical_bytes(file_id, link_id, expires_at), hashlib.sha256)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")


def verify(
    secret: bytes,
    file_id: str,
    link_id: str,
    expires_at: str,
    signature: str,
    now: float,
) -> VerifyResult:
    """Verify raw (untrusted) link parameters.

    Order matters: the signature is checked first, and only an authentic link can be
    reported as EXPIRED. Malformed input is treated as an invalid signature and never
    raises, so callers can map the result straight to an HTTP status.

    A link is expired when now >= expires_at.
    """
    if not (is_valid_id(file_id) and is_valid_id(link_id)):
        return VerifyResult.INVALID_SIGNATURE
    if not _EXPIRES_PATTERN.fullmatch(expires_at):
        return VerifyResult.INVALID_SIGNATURE
    if not signature or len(signature) > _MAX_SIGNATURE_LENGTH:
        return VerifyResult.INVALID_SIGNATURE
    try:
        # hmac.compare_digest raises TypeError for non-ASCII str, so compare bytes.
        provided = signature.encode("ascii")
    except UnicodeEncodeError:
        return VerifyResult.INVALID_SIGNATURE

    expires_int = int(expires_at)
    expected = sign(secret, file_id, link_id, expires_int).encode("ascii")
    if not hmac.compare_digest(expected, provided):
        return VerifyResult.INVALID_SIGNATURE
    if now >= expires_int:
        return VerifyResult.EXPIRED
    return VerifyResult.VALID
