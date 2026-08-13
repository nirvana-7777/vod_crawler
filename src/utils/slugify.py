#!/usr/bin/env python3
"""
Slugify utility for generating URL-safe / ID-safe strings.

Single source of truth: previously this logic was duplicated between
database/db_manager.py and the backend's models/vod.py. Keeping one copy
here avoids the two drifting apart again.
"""

import re
import unicodedata


def slugify(text: str) -> str:
    """
    Convert an arbitrary string to a URL-safe slug.

    Args:
        text: String to slugify (can be None/empty)

    Returns:
        Slugified string, or empty string if text is None/empty/unsluggable.
    """
    if not text:
        return ""

    try:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = text.lower()
        text = re.sub(r"[\s\-\u2013\u2014/\\|]+", "_", text)
        text = re.sub(r"[^\w\-_]", "", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("_-")
    except Exception:
        return ""