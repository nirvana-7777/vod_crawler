#!/usr/bin/env python3
"""
Content classifier for VOD items
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum, auto


class ContentType(Enum):
    """Type of content"""
    CATEGORY = auto()
    MOVIE = auto()
    TV_EPISODE = auto()
    UNKNOWN = auto()


@dataclass
class ClassificationResult:
    """Result of content classification"""
    content_type: ContentType
    is_playable: bool = False
    series_title: Optional[str] = None
    series_id: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None


class ContentClassifier:
    """
    Classify VOD items to determine if they are categories, movies, or TV episodes
    """

    def __init__(self, skip_highlights: bool = True):
        self.skip_highlights = skip_highlights

    def classify(self, item: Dict[str, Any]) -> Optional[ClassificationResult]:
        """
        Classify a VOD item

        Args:
            item: API response entry (VodItem or VodCategory)

        Returns:
            ClassificationResult or None if item should be skipped
        """
        # Check if this is a category or item
        # NOTE: the backend sends this under "type" (e.g. "vod_category"),
        # not "content_type". Keep "content_type" as a fallback in case
        # another provider integration uses that key instead.
        raw_type = item.get("type") or item.get("content_type", "VOD")
        content_type = raw_type.upper()

        # Check if it's a playable item
        is_playable = item.get("is_playable", False)

        # Skip highlights if configured
        if self.skip_highlights and item.get("is_highlight", False):
            return None

        # Check if it's a category
        is_category = (
            item.get("is_category", False)
            or content_type == "CATEGORY"
            or content_type == "VOD_CATEGORY"
        )
        if is_category or content_type == "FOLDER":
            return ClassificationResult(
                content_type=ContentType.CATEGORY,
                is_playable=False
            )

        # CRITICAL FIX: MOVIE should short-circuit regardless of stray season/episode fields
        if content_type == "MOVIE":
            return ClassificationResult(
                content_type=ContentType.MOVIE,
                is_playable=is_playable
            )

        # Check for TV episode markers
        season_number = item.get("season_number")
        episode_number = item.get("episode_number")
        series_title = item.get("series_title")
        series_id = item.get("series_id")

        # Determine if it's a TV episode
        is_episode = season_number is not None or episode_number is not None

        if is_episode:
            return ClassificationResult(
                content_type=ContentType.TV_EPISODE,
                is_playable=is_playable,
                series_title=series_title or item.get("name"),
                series_id=series_id or item.get("id"),
                season_number=season_number,
                episode_number=episode_number
            )

        # Check for series title as a fallback (some providers mark episodes differently)
        if series_title and (content_type == "VOD" or content_type == "EPISODE"):
            return ClassificationResult(
                content_type=ContentType.TV_EPISODE,
                is_playable=is_playable,
                series_title=series_title,
                series_id=series_id or item.get("id"),
                season_number=season_number,
                episode_number=episode_number
            )

        # If it's playable but not categorized, treat as movie
        if is_playable:
            return ClassificationResult(
                content_type=ContentType.MOVIE,
                is_playable=True
            )

        # Unknown
        return ClassificationResult(
            content_type=ContentType.UNKNOWN,
            is_playable=False
        )