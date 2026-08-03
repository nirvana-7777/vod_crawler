#!/usr/bin/env python3
"""
Provider-specific crawler that connects the tree traversal to the database
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from .base import BaseCrawler
from .tree_traverser import TreeTraverser
from .content_classifier import ContentClassifier, ContentType, ClassificationResult
from ..database import DatabaseManager
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ProviderCrawler:
    """
    Crawl a single provider's VOD catalog and store in database
    """

    def __init__(self, config: Config, db: DatabaseManager):
        self.config = config
        self.db = db
        self.crawler = BaseCrawler(config)
        self.classifier = ContentClassifier(
            skip_highlights=config.crawler.skip_highlights
        )
        self.traverser = TreeTraverser(
            crawler=self.crawler,
            classifier=self.classifier,
            max_depth=config.crawler.max_depth,
            page_size=config.crawler.page_size
        )

        # Statistics
        self.stats = {
            "movies_added": 0,
            "movies_updated": 0,
            "episodes_added": 0,
            "episodes_updated": 0,
            "shows_created": 0,
            "shows_updated": 0,
        }

        # Batch buffers
        self.movie_buffer: List[Dict[str, Any]] = []
        self.episode_buffer: List[Dict[str, Any]] = []
        self.buffer_size = 100  # Flush every 100 items

    def crawl_provider(self, provider: str) -> Dict[str, Any]:
        """
        Crawl a single provider's VOD catalog

        Args:
            provider: Provider name (e.g., "joyn", "rtlplus")

        Returns:
            Crawl statistics
        """
        logger.info(f"Starting crawl for provider: {provider}")

        # Start crawl history
        history_id = self.db.start_crawl(provider)

        # Clear existing data for this provider
        self.db.clear_provider_data(provider)

        # Reset stats
        self.stats = {
            "movies_added": 0,
            "movies_updated": 0,
            "episodes_added": 0,
            "episodes_updated": 0,
            "shows_created": 0,
            "shows_updated": 0,
        }

        # Reset buffers
        self.movie_buffer = []
        self.episode_buffer = []

        try:
            # Traverse the VOD tree
            traversal_stats = self.traverser.traverse(
                provider=provider,
                on_category=self._process_category,
                on_item=self._process_item
            )

            # Flush any remaining items
            self._flush_buffers()

            # Update stats
            total_movies = self.stats["movies_added"] + self.stats["movies_updated"]
            total_episodes = self.stats["episodes_added"] + self.stats["episodes_updated"]

            logger.info(
                f"Crawl complete for {provider}: "
                f"{total_movies} movies, {total_episodes} episodes"
            )

            # Finish crawl history
            self.db.finish_crawl(
                history_id=history_id,
                status="success",
                items_found=traversal_stats.get("total_items", 0),
                items_added=self.stats["movies_added"] + self.stats["episodes_added"],
                items_updated=self.stats["movies_updated"] + self.stats["episodes_updated"],
                items_removed=0,  # We don't track removals individually
                details={
                    "traversal": traversal_stats,
                    "stats": self.stats
                }
            )

            return {
                "provider": provider,
                "success": True,
                "traversal_stats": traversal_stats,
                "db_stats": self.stats
            }

        except Exception as e:
            logger.error(f"Error crawling {provider}: {e}", exc_info=True)

            # Finish crawl as failed
            self.db.finish_crawl(
                history_id=history_id,
                status="failed",
                error_message=str(e),
                items_found=0,
                details={"error": str(e)}
            )

            return {
                "provider": provider,
                "success": False,
                "error": str(e)
            }

    def _process_category(self, entry: Dict[str, Any], depth: int) -> None:
        """Process a VodCategory"""
        # Log progress every 100 categories
        if self.stats.get("categories_processed", 0) % 100 == 0:
            logger.debug(f"Depth {depth}: {entry.get('name', 'unknown')}")

        self.stats["categories_processed"] = self.stats.get("categories_processed", 0) + 1

    def _process_item(self, entry: Dict[str, Any], result: ClassificationResult) -> None:
        """Process a VodItem (movie or TV episode)"""

        # CRITICAL FIX: Skip non-playable items entirely
        if not result.is_playable:
            logger.debug(f"Skipping non-playable item: {entry.get('name', 'unknown')}")
            return

        provider = entry.get("provider", "unknown")
        content_id = entry.get("id", "")
        name = entry.get("name", "Unknown")

        # Build common metadata from entry
        metadata = self._extract_metadata(entry)

        if result.content_type == ContentType.MOVIE:
            # Add to movie buffer
            movie_data = {
                "provider": provider,
                "content_id": content_id,
                "title": name,
                **metadata
            }
            self.movie_buffer.append(movie_data)

            if len(self.movie_buffer) >= self.buffer_size:
                self._flush_movies()

        elif result.content_type == ContentType.TV_EPISODE:
            # Get or create show using the DB manager (handles session lifecycle)
            show = self._get_or_create_show(
                provider=provider,
                series_title=result.series_title or name,
                series_id=result.series_id,
                entry=entry
            )

            if show:
                # Add to episode buffer
                episode_data = {
                    "show_id": show.id,
                    "provider": provider,
                    "content_id": content_id,
                    "season_number": result.season_number or 0,
                    "episode_number": result.episode_number or 0,
                    "title": name,
                    "series_title": result.series_title or name,
                    **metadata
                }
                self.episode_buffer.append(episode_data)

                if len(self.episode_buffer) >= self.buffer_size:
                    self._flush_episodes()

    def _extract_metadata(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metadata from VodItem for database storage"""
        return {
            # Core metadata
            "original_title": entry.get("original_title"),
            "plot": entry.get("description") or entry.get("long_description"),
            "long_description": entry.get("long_description"),
            "release_year": entry.get("release_year"),
            "duration_seconds": entry.get("duration_seconds"),
            "rating": entry.get("rating"),

            # Genres
            "genres": entry.get("genres"),
            "genre": entry.get("genre"),

            # People
            "cast": entry.get("cast"),
            "director": entry.get("director"),

            # Streaming
            "mode": "vod",
            "logo_url": entry.get("logo_url"),
            "manifest_url": entry.get("manifest_url"),
            "manifest_script": entry.get("manifest_script"),
            "session_manifest": entry.get("session_manifest", False),

            # DRM
            "license_url": entry.get("license_url"),
            "certificate_url": entry.get("certificate_url"),
            "drm_config": entry.get("drm_config"),
            "cdm_type": entry.get("cdm_type"),
            "use_cdm": entry.get("use_cdm", True),
            "cdm_mode": entry.get("cdm_mode", "external"),

            # Video settings
            "video": entry.get("video", "best"),
            "on_demand": entry.get("on_demand", True),
            "speed_up": entry.get("speed_up", True),
            "streaming_format": entry.get("streaming_format"),
            "quality": entry.get("quality"),

            # Localization
            "language": entry.get("language", "de"),
            "country": entry.get("country", "DE"),

            # Promotional
            "trailer_url": entry.get("trailer_url"),
            "is_highlight": entry.get("is_highlight", False),

            # Provider-specific
            "provider_episode_id": entry.get("id"),

            # External IDs (if available)
            "imdb_id": entry.get("imdb_id"),
            "tmdb_id": entry.get("tmdb_id"),
        }

    def _get_or_create_show(
            self,
            provider: str,
            series_title: str,
            series_id: Optional[str],
            entry: Dict[str, Any]
    ) -> Optional[Any]:
        """
        Get or create a TV show for an episode.
        Delegates to DatabaseManager.get_or_create_show() which handles
        the session lifecycle correctly.
        """
        if not series_title:
            logger.warning(f"No series title for episode {entry.get('id', 'unknown')}")
            return None

        # Normalize the title for matching
        normalized_title = self._normalize_series_title(series_title)

        # Get show metadata from entry
        show_metadata = {
            "plot": entry.get("description") or entry.get("long_description"),
            "poster_url": entry.get("logo_url"),
            "genres": entry.get("genres"),
            "genre": entry.get("genre"),
            "release_year": entry.get("release_year"),
        }

        # Let the DB manager handle the upsert!
        # This correctly handles both creation and updates with proper session management
        show, created = self.db.get_or_create_show(
            normalized_title=normalized_title,
            title=series_title,
            provider=provider,
            provider_id=series_id or "",
            **show_metadata
        )

        if created:
            self.stats["shows_created"] += 1
        else:
            self.stats["shows_updated"] += 1

        return show

    def _normalize_series_title(self, title: str) -> str:
        """Normalize series title for matching across providers"""
        import re

        if not title:
            return ""

        # Remove year in parentheses at the end
        title = re.sub(r'\s*\(\d{4}\)\s*$', '', title)
        # Remove "Staffel" (German for season) and variations
        title = re.sub(r'\s*Staffel\s*\d+', '', title, flags=re.IGNORECASE)
        # Remove "Season" and variations
        title = re.sub(r'\s*Season\s*\d+', '', title, flags=re.IGNORECASE)
        # Remove "Saison" (French for season)
        title = re.sub(r'\s*Saison\s*\d+', '', title, flags=re.IGNORECASE)
        # Remove extra spaces
        title = re.sub(r'\s+', ' ', title).strip()

        # Create slug for matching
        from ..database.db_manager import slugify
        return slugify(title)

    def _flush_movies(self) -> None:
        """Flush movie buffer to database"""
        if not self.movie_buffer:
            return

        logger.debug(f"Flushing {len(self.movie_buffer)} movies to database")

        try:
            stats = self.db.bulk_upsert_movies(self.movie_buffer)
            self.stats["movies_added"] += stats.get("added", 0)
            self.stats["movies_updated"] += stats.get("updated", 0)
        except Exception as e:
            logger.error(f"Error flushing movies: {e}")
            # Try one by one for recovery
            for movie in self.movie_buffer:
                try:
                    self.db.add_or_update_movie(**movie)
                    self.stats["movies_added"] += 1
                except Exception as e2:
                    logger.error(f"Error adding movie {movie.get('title', 'unknown')}: {e2}")

        self.movie_buffer = []

    def _flush_episodes(self) -> None:
        """Flush episode buffer to database"""
        if not self.episode_buffer:
            return

        logger.debug(f"Flushing {len(self.episode_buffer)} episodes to database")

        try:
            stats = self.db.bulk_upsert_episodes(self.episode_buffer)
            self.stats["episodes_added"] += stats.get("added", 0)
            self.stats["episodes_updated"] += stats.get("updated", 0)
        except Exception as e:
            logger.error(f"Error flushing episodes: {e}")
            # Try one by one for recovery
            for episode in self.episode_buffer:
                try:
                    self.db.add_or_update_episode(**episode)
                    self.stats["episodes_added"] += 1
                except Exception as e2:
                    logger.error(f"Error adding episode {episode.get('title', 'unknown')}: {e2}")

        self.episode_buffer = []

    def _flush_buffers(self) -> None:
        """Flush all buffers"""
        self._flush_movies()
        self._flush_episodes()