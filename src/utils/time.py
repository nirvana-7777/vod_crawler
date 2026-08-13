#!/usr/bin/env python3
"""
Time utilities for consistent timezone handling.

Every timestamp written to the database or emitted by the API should go
through utcnow() so we never accidentally mix naive and aware datetimes
(this was the source of past timezone bugs in the EPG pipeline).
"""

from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert a datetime to an ISO 8601 string, assuming UTC if naive."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()