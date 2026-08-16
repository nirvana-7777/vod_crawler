#!/usr/bin/env python3
"""
Provider-specific crawler that connects the tree traversal to the database
"""

import re
from typing import Dict, Any, List, Optional

from .base import BaseCrawler
from .tree_traverser import TreeTraverser
from .content_classifier import ContentClassifier, ContentType, ClassificationResult
from ..database import DatabaseManager
from ..config import Config
from ..utils.logger import get_logger
from ..utils.slugify import slugify

logger = get_logger(__name__)


class ProviderCrawler:
    """
    Crawl a single provider's VOD catalog and store in database
    """

    def __init__(self, config: Config, db: DatabaseManager):
        self.config = config
        self.db = db
        self.crawler = BaseCrawler(config)
        self.current_provider: Optional[str] = None
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

        # content_ids (movies + episodes) for the CURRENT provider crawl
        # that already have a pricing detail lookup on record -- populated
        # once at the start of crawl_provider() and consulted (not
        # re-queried) for every item _process_item() sees. See
        # _process_item() for why this exists: category/listing responses
        # don't carry the full Pricing object, only a dedicated per-item
        # detail fetch does, and we only want to pay for that fetch once
        # per item, not once per crawl.
        self._pricing_checked_ids: set = set()

    def crawl_provider(self, provider: str) -> Dict[str, Any]:
        """
        Crawl a single provider's VOD catalog

        Args:
            provider: Provider name (e.g., "joyn", "rtlplus")

        Returns:
            Crawl statistics
        """
        logger.info(f"Starting crawl for provider: {provider}")

        # Canonical provider identifier for this crawl -- this is what
        # matches config.provider_priority, the API path we're calling,
        # and what _validate_provider() checks against. Do NOT trust
        # whatever an individual item's own "provider"/"Provider" field
        # says instead: for plugin-backed multi-country providers (e.g.
        # discovery_de, joyn_de/at/ch, magentaeu_hr/pl/me/at), the item
        # payload reflects the underlying PLUGIN name ("discovery", "joyn",
        # "magentaeu"), not the specific country-scoped instance we
        # actually queried -- using it directly causes "Unknown provider"
        # validation failures.
        self.current_provider = provider

        # Start crawl history
        history_id = self.db.start_crawl(provider)

        # Clear existing data for this provider. This marks every
        # currently-available movie/episode/show mapping for this provider
        # as is_available=False *before* traversal starts. Anything the
        # traversal below encounters gets flipped back to True via the
        # upsert helpers; anything it doesn't encounter (i.e. no longer on
        # the provider) simply stays False. This "clear-then-repopulate"
        # pattern is the mark-unavailable mechanism -- there is no need
        # for a second pass that diffs a "seen" set against the DB at the
        # end, since the same result already falls out of this.
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

        # One query for the whole crawl -- see the attribute docstring in
        # __init__ for why this can't just be looked up per-item.
        self._pricing_checked_ids = self.db.get_pricing_checked_ids(provider)
        logger.debug(
            f"{len(self._pricing_checked_ids)} items already pricing-checked "
            f"for {provider}; new/unchecked items will get a detail fetch"
        )

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

    def _process_item(self, entry: Dict[str, Any], result: ClassificationResult, parent_content_id: Optional[str] = None) -> None:
        """Process a VodItem (movie or TV episode)

        Args:
            entry: raw item payload
            result: classification result
            parent_content_id: content_id of the category this item was
                fetched under. Used as the series identifier fallback for
                episodes -- some providers (e.g. RTL+) leave series_id null
                on every clip and never expose a usable series id anywhere
                on the item itself, only via the parent program/category id.
        """

        # CRITICAL FIX: Skip non-playable items entirely
        if not result.is_playable:
            logger.debug(f"Skipping non-playable item: {entry.get('name', 'unknown')}")
            return

        # NOTE: leaf item entries only carry PascalCase "Id"/"Name" -- there
        # are no lowercase equivalents in this backend's item payload (unlike
        # category entries, which are all lowercase). Fall back accordingly
        # or every item collapses to the same identity.
        #
        # Provider identity is DELIBERATELY not read from the entry at all:
        # for plugin-backed multi-country providers, the item's own
        # "provider"/"Provider" field reflects the underlying plugin name
        # (e.g. "discovery" for discovery_de, "joyn" for joyn_de/at/ch),
        # not the specific country-scoped instance we're crawling. Always
        # use the canonical provider this crawl was started with instead.
        provider = self.current_provider
        content_id = entry.get("id") or entry.get("Id") or ""
        name = entry.get("name") or entry.get("Name") or "Unknown"

        # Build common metadata from entry
        metadata = self._extract_metadata(entry)

        # Category/listing responses (what we're given here) don't carry
        # the full Pricing object -- only a dedicated per-item detail
        # fetch (GET /vod/{content_id}) does. Only pay for that fetch once
        # per item, ever: if we've already checked this content_id in a
        # previous crawl, skip it and leave pricing_* out of `metadata`
        # entirely so the upsert doesn't touch (or clobber) the existing
        # DB values for it. This deliberately does not pick up price
        # CHANGES on already-checked items -- see the pricing_checked
        # column docstring in models.py.
        if content_id and content_id not in self._pricing_checked_ids:
            try:
                detail = self.crawler.get_vod_node(provider=provider, content_id=content_id)
            except Exception as e:
                logger.debug(f"Pricing detail fetch failed for {content_id}: {e}")
                detail = None

            if detail and detail.get("entries"):
                pricing = self._extract_pricing(detail["entries"][0])
                metadata.update(pricing)
                metadata["pricing_checked"] = True
                self._pricing_checked_ids.add(content_id)
            else:
                # Don't mark as checked on failure/empty response -- leave
                # it to be retried on the next crawl rather than silently
                # giving up on this item's pricing forever.
                logger.debug(f"No detail entry returned for {content_id}, will retry pricing next crawl")

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
            show_id = self._get_or_create_show(
                provider=provider,
                series_title=result.series_title or name,
                series_id=result.series_id,
                parent_content_id=parent_content_id,
                entry=entry
            )

            if show_id:
                # Add to episode buffer
                # NOTE: these keys exist in `metadata` (shared with movies)
                # but have no corresponding column on TVEpisode -- movies
                # have their own release_year/original_title/imdb_id/tmdb_id,
                # episodes don't. Passing them through makes
                # bulk_upsert_episodes()'s raw insert(...).values(**ep_data)
                # blow up with "Unconsumed column names" since it doesn't
                # filter unknown keys the way add_or_update_episode()'s
                # explicit whitelist does.
                episode_only_metadata = {
                    k: v for k, v in metadata.items()
                    if k not in ("original_title", "release_year", "imdb_id", "tmdb_id")
                }
                episode_data = {
                    "show_id": show_id,
                    "provider": provider,
                    "content_id": content_id,
                    "season_number": result.season_number or 0,
                    "episode_number": result.episode_number or 0,
                    "title": name,
                    "series_title": result.series_title or name,
                    **episode_only_metadata
                }
                self.episode_buffer.append(episode_data)

                if len(self.episode_buffer) >= self.buffer_size:
                    self._flush_episodes()

    @staticmethod
    def _extract_pricing(entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract pricing_* columns from a raw item entry.

        The backend's Content.to_dict() (see content.py) serializes the
        Pricing dataclass under the PascalCase key "Pricing" only when
        pricing is set; unpriced/unknown content simply omits the key.
        Every field here defaults to None, matching Pricing's own
        "None means unknown, not free" contract -- we must not default
        pricing_access_type to something that reads as free.
        """
        pricing_fields = {
            "pricing_access_type": None,
            "pricing_price_points": None,
            "pricing_required_tiers": None,
            "pricing_required_bouquets": None,
            "pricing_rental_duration_hours": None,
            "pricing_catchup_duration_hours": None,
            "pricing_replay_window_hours": None,
            "pricing_preview_minutes": None,
            "pricing_description": None,
            "pricing_tax_class": None,
        }

        pricing = entry.get("pricing") or entry.get("Pricing")
        if not pricing:
            return pricing_fields

        pricing_dict = pricing.to_dict() if hasattr(pricing, "to_dict") else pricing
        if not isinstance(pricing_dict, dict):
            logger.warning(f"Unexpected pricing payload type: {type(pricing_dict)!r}")
            return pricing_fields

        pricing_fields["pricing_access_type"] = pricing_dict.get("access_type")
        pricing_fields["pricing_price_points"] = pricing_dict.get("price_points")
        pricing_fields["pricing_required_tiers"] = pricing_dict.get("required_tiers")
        pricing_fields["pricing_required_bouquets"] = pricing_dict.get("required_bouquets")
        pricing_fields["pricing_rental_duration_hours"] = pricing_dict.get("rental_duration_hours")
        pricing_fields["pricing_catchup_duration_hours"] = pricing_dict.get("catchup_duration_hours")
        pricing_fields["pricing_replay_window_hours"] = pricing_dict.get("replay_window_hours")
        pricing_fields["pricing_preview_minutes"] = pricing_dict.get("preview_minutes")
        pricing_fields["pricing_description"] = pricing_dict.get("description")
        pricing_fields["pricing_tax_class"] = pricing_dict.get("tax_class")
        return pricing_fields

    @classmethod
    def _extract_metadata(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metadata from VodItem for database storage"""
        metadata = {
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
            "logo_url": entry.get("logo_url") or entry.get("LogoUrl"),
            "manifest_url": entry.get("manifest_url"),
            "manifest_script": entry.get("manifest_script") or entry.get("ManifestScript"),
            "session_manifest": entry.get("session_manifest", entry.get("SessionManifest", False)),

            # DRM
            "license_url": entry.get("license_url") or entry.get("LicenseUrl"),
            "certificate_url": entry.get("certificate_url") or entry.get("CertificateUrl"),
            "drm_config": entry.get("drm_config") or entry.get("DrmConfig"),
            "cdm_type": entry.get("cdm_type") or entry.get("CdmType"),
            "use_cdm": entry.get("use_cdm", entry.get("UseCdm", True)),
            "cdm_mode": entry.get("cdm_mode") or entry.get("CdmMode", "external"),

            # Video settings
            "video": entry.get("video") or entry.get("Video", "best"),
            "on_demand": entry.get("on_demand", entry.get("OnDemand", True)),
            "speed_up": entry.get("speed_up", entry.get("SpeedUp", True)),
            "streaming_format": entry.get("streaming_format") or entry.get("StreamingFormat"),
            "quality": entry.get("quality") or entry.get("Quality"),

            # Localization
            "language": entry.get("language") or entry.get("Language", "de"),
            "country": entry.get("country") or entry.get("Country", "DE"),

            # Promotional
            "trailer_url": entry.get("trailer_url"),
            "is_highlight": entry.get("is_highlight", False),

            # External IDs (if available)
            "imdb_id": entry.get("imdb_id"),
            "tmdb_id": entry.get("tmdb_id"),
        }

        metadata.update(cls._extract_pricing(entry))
        return metadata

    def _get_or_create_show(
            self,
            provider: str,
            series_title: str,
            series_id: Optional[str],
            entry: Dict[str, Any],
            parent_content_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get or create a TV show for an episode. Returns the show id (or
        None if no series title is available to key off of).

        Delegates to DatabaseManager.get_or_create_show() which handles
        the session lifecycle correctly.

        series_id (from classification) is often null at the item level
        (e.g. RTL+ never populates it on the clip itself) -- prefer
        parent_content_id, the content_id of the category/program folder
        the episode was fetched under, which is the actual stable series
        identifier in that case.
        """
        if not series_title:
            logger.warning(f"No series title for episode {entry.get('id', 'unknown')}")
            return None

        # Normalize the title for matching
        normalized_title = self._normalize_series_title(series_title)

        # Get show metadata from entry
        show_metadata = {
            "plot": entry.get("description") or entry.get("long_description"),
            "poster_url": entry.get("logo_url") or entry.get("LogoUrl"),
            "genres": entry.get("genres"),
            "genre": entry.get("genre"),
            "release_year": entry.get("release_year"),
        }

        resolved_series_id = series_id or parent_content_id or ""

        # Let the DB manager handle the upsert! This correctly handles both
        # creation and updates with proper session management.
        show_id, created = self.db.get_or_create_show(
            normalized_title=normalized_title,
            title=series_title,
            provider=provider,
            provider_id=resolved_series_id,
            **show_metadata
        )

        if created:
            self.stats["shows_created"] += 1
        else:
            self.stats["shows_updated"] += 1

        return show_id

    @staticmethod
    def _normalize_series_title(title: str) -> str:
        """Normalize series title for matching across providers"""
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
                    _, created = self.db.add_or_update_movie(**movie)
                    if created:
                        self.stats["movies_added"] += 1
                    else:
                        self.stats["movies_updated"] += 1
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
                    _, created = self.db.add_or_update_episode(**episode)
                    if created:
                        self.stats["episodes_added"] += 1
                    else:
                        self.stats["episodes_updated"] += 1
                except Exception as e2:
                    logger.error(f"Error adding episode {episode.get('title', 'unknown')}: {e2}")

        self.episode_buffer = []

    def _flush_buffers(self) -> None:
        """Flush all buffers"""
        self._flush_movies()
        self._flush_episodes()