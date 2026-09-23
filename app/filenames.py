"""Sanitizing user-supplied filenames.

The sanitized name is only used for display and the Content-Disposition header.
It is never used to build a filesystem path (blobs are stored by generated id).
"""

import re

MAX_FILENAME_LENGTH = 255
DEFAULT_FILENAME = "unnamed"

# ASCII control characters plus Unicode bidi overrides (used to spoof extensions,
# e.g. "invoice\u202egpj.exe" rendering as "invoiceexe.jpg").
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def sanitize_filename(raw: str | None) -> str:
    if not raw:
        return DEFAULT_FILENAME
    # Keep only the last path component, treating both / and \ as separators.
    name = raw.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_CHARS.sub("", name).strip()
    if name in {"", ".", ".."}:
        return DEFAULT_FILENAME
    return name[:MAX_FILENAME_LENGTH]
