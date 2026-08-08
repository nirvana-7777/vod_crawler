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
        # Check if this is a category or item.
        # NOTE: the backend's field naming is inconsistent between levels:
        #   - category entries use lowercase "type" (e.g. "vod_category")
        #   - leaf item entries use lowercase "type": "vod" (generic/useless)
        #     PLUS a separate PascalCase "ContentType": "MOVIE" key that is
        #     the actual authoritative signal for items.
        # So: check the lowercase "type" first for the category case, then
        # fall back to ContentType/content_type for the item case.
        raw_type = item.get("type", "VOD")
        content_type = raw_type.upper()

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

        # There is no explicit "is_playable" flag anywhere in the backend
        # payload -- infer playability from the presence of a stream/manifest
        # URL instead.
        is_playable = bool(
            item.get("is_playable")
            or item.get("stream_url")
            or item.get("manifest_url")
        )

        # Item-level content type lives under PascalCase "ContentType" (the
        # lowercase "type" is just "vod" for every playable item and isn't
        # useful here). Fall back to lowercase "content_type" in case some
        # provider integration normalizes it differently.
        item_content_type = (
            item.get("ContentType") or item.get("content_type") or ""
        ).upper()

        # CRITICAL FIX: MOVIE should short-circuit regardless of stray season/episode fields
        if item_content_type == "MOVIE":
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
                series_title=series_title or item.get("name") or item.get("Name"),
                series_id=series_id,
                season_number=season_number,
                episode_number=episode_number
            )

        # Check for series title as a fallback (some providers mark episodes differently)
        if series_title and (item_content_type in ("", "VOD", "EPISODE")):
            return ClassificationResult(
                content_type=ContentType.TV_EPISODE,
                is_playable=is_playable,
                series_title=series_title,
                series_id=series_id,
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