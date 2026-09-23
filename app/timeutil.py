"""Time helpers. The app uses an injectable clock (seconds since epoch) for testability."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], float]


def to_iso(timestamp: float) -> str:
    """Format a unix timestamp as a sortable ISO 8601 UTC string (millisecond precision)."""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="milliseconds")
