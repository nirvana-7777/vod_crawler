#!/usr/bin/env python3
"""
Library export generator - creates the full export response
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Generator
from urllib.parse import urlencode

from sqlalchemy.orm import joinedload, selectinload

from ..database import DatabaseManager
from ..database.models import FREE_ACCESS_TYPES
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ExportGenerator:
    """Generate library export data for the API"""

    def __init__(self, db: DatabaseManager, config: Config):
        self.db = db
        self.config = config
        self.plugin_id = "plugin.video.ultimate"

    def _build_plugin_url(self, action: str, **kwargs) -> str:
        """Build a plugin:// URL for Kodi.

        The ``action`` parameter is required by the plugin.video.ultimate
        Kodi addon so it knows which routing handler to invoke (e.g.
        ``play_vod`` to fetch and resolve a video stream). It is placed
        first in the query string for clarity and to match the addon's
        expected URL shape.
        """
        if not action:
            raise ValueError("plugin URL requires a non-empty 'action' parameter")
        params = {"action": action, **kwargs}
        return f"plugin://{self.plugin_id}/?{urlencode(params)}"

    @staticmethod
    def _get_item_hash(item: Dict[str, Any]) -> str:
        """Generate a hash for change detection"""
        hashable = {
            "id": item.get("id"),
            "title": item.get("title"),
            "plot": item.get("plot"),
            "release_year": item.get("release_year"),
            "duration_seconds": item.get("duration_seconds"),
            "genres": item.get("genres"),
            "cast": item.get("cast"),
            "director": item.get("director"),
            "logo_url": item.get("logo_url"),
            "manifest_url": item.get("manifest_url"),
            "pricing_access_type": item.get("pricing_access_type"),
            "pricing_price_points": item.get("pricing_price_points"),
        }
        clean = {k: v for k, v in hashable.items() if v is not None}
        sorted_json = json.dumps(clean, sort_keys=True, default=str)
        return hashlib.sha256(sorted_json.encode()).hexdigest()[:16]

    @staticmethod
    def _get_show_summary(show) -> Dict[str, Any]:
        """Convert TVShow to summary dict"""
        provider_ids = {}
        for mapping in show.provider_mappings:
            if mapping.is_available:
                provider_ids[mapping.provider] = mapping.provider_id

        return {
            "id": show.id,
            "title": show.title,
            "normalized_title": show.normalized_title,
            "plot": show.plot,
            "poster_url": show.poster_url,
            "backdrop_url": show.backdrop_url,
            "genres": show.genres,
            "genre": show.genre,
            "release_year": show.release_year,
            "provider_ids": provider_ids,
            "is_available": show.is_available,
        }

    def _get_movie_export(self, movie, provider_priority: List[str]) -> Dict[str, Any]:
        """Convert Movie to export dict"""
        stream_url = self._build_plugin_url(
            action="play_vod",
            provider=movie.provider,
            item_id=movie.content_id,
            item_name=movie.title,
        )

        return {
            "id": movie.id,
            "provider": movie.provider,
            "content_id": movie.content_id,
            "title": movie.title,
            "original_title": movie.original_title,
            "plot": movie.plot,
            "long_description": movie.long_description,
            "release_year": movie.release_year,
            "duration_seconds": movie.duration_seconds,
            "rating": movie.rating,
            "genres": movie.genres,
            "genre": movie.genre,
            "cast": movie.cast,
            "director": movie.director,
            "logo_url": movie.logo_url,
            "stream_url": stream_url,
            "manifest_url": movie.manifest_url,
            "is_highlight": movie.is_highlight,
            "is_sport": movie.is_sport,
            "hash": self._get_item_hash(movie.to_dict()),
            "last_seen": movie.last_seen,
            # Pricing
            "pricing_access_type": movie.pricing_access_type,
            "pricing_price_points": movie.pricing_price_points,
            "pricing_required_tiers": movie.pricing_required_tiers,
            "pricing_required_bouquets": movie.pricing_required_bouquets,
            "pricing_rental_duration_hours": movie.pricing_rental_duration_hours,
            "pricing_catchup_duration_hours": movie.pricing_catchup_duration_hours,
            "pricing_replay_window_hours": movie.pricing_replay_window_hours,
            "pricing_preview_minutes": movie.pricing_preview_minutes,
            "pricing_description": movie.pricing_description,
            "pricing_tax_class": movie.pricing_tax_class,
        }

    def _get_episode_export(self, episode, show_title: str, provider_priority: List[str]) -> Dict[str, Any]:
        """Convert TVEpisode to export dict"""
        stream_url = self._build_plugin_url(
            action="play_vod",
            provider=episode.provider,
            item_id=episode.content_id,
            item_name=episode.title or f"Episode {episode.episode_number}",
        )

        return {
            "id": episode.id,
            "show_id": episode.show_id,
            "show_title": show_title,
            "provider": episode.provider,
            "content_id": episode.content_id,
            "season_number": episode.season_number,
            "episode_number": episode.episode_number,
            "title": episode.title,
            "series_title": episode.series_title,
            "plot": episode.plot,
            "long_description": episode.long_description,
            "air_date": episode.air_date,
            "duration_seconds": episode.duration_seconds,
            "rating": episode.rating,
            "genres": episode.genres,
            "genre": episode.genre,
            "cast": episode.cast,
            "director": episode.director,
            "logo_url": episode.logo_url,
            "stream_url": stream_url,
            "manifest_url": episode.manifest_url,
            "is_highlight": episode.is_highlight,
            "hash": self._get_item_hash(episode.to_dict()),
            "last_seen": episode.last_seen,
            # Pricing
            "pricing_access_type": episode.pricing_access_type,
            "pricing_price_points": episode.pricing_price_points,
            "pricing_required_tiers": episode.pricing_required_tiers,
            "pricing_required_bouquets": episode.pricing_required_bouquets,
            "pricing_rental_duration_hours": episode.pricing_rental_duration_hours,
            "pricing_catchup_duration_hours": episode.pricing_catchup_duration_hours,
            "pricing_replay_window_hours": episode.pricing_replay_window_hours,
            "pricing_preview_minutes": episode.pricing_preview_minutes,
            "pricing_description": episode.pricing_description,
            "pricing_tax_class": episode.pricing_tax_class,
        }

    @staticmethod
    def _apply_pricing_filter(query, model, include_priced: bool):
        """
        Restrict a query to free-at-point-of-use content unless the caller
        opted in to priced content.

        pricing_access_type is None for content whose pricing was never
        determined (unknown, NOT free -- see pricing.py) as well as for
        content that genuinely is free/AVOD, so unknown-pricing items are
        included by default alongside the known-free ones. If that turns
        out to be too permissive for a given deployment, tighten this to
        `.in_(FREE_ACCESS_TYPES)` without the `is_(None)` branch.
        """
        if include_priced:
            return query
        column = model.pricing_access_type
        return query.filter(
            (column.is_(None)) | (column.in_(FREE_ACCESS_TYPES))
        )

    def _get_deleted_items(
        self,
        since: datetime,
        providers: Optional[List[str]] = None,
        include_priced: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get items deleted (is_available=False) since the given timestamp."""
        deleted = []

        with self.db.session() as session:
            from ..database.models import Movie, TVEpisode

            movie_query = session.query(Movie).filter(
                Movie.is_available == False,
                Movie.last_seen >= since
            )
            if providers:
                movie_query = movie_query.filter(Movie.provider.in_(providers))
            movie_query = self._apply_pricing_filter(movie_query, Movie, include_priced)

            for movie in movie_query.all():
                deleted.append({
                    "id": movie.id,
                    "type": "movie",
                    "provider": movie.provider,
                    "content_id": movie.content_id,
                })

            ep_query = session.query(TVEpisode).filter(
                TVEpisode.is_available == False,
                TVEpisode.last_seen >= since
            )
            if providers:
                ep_query = ep_query.filter(TVEpisode.provider.in_(providers))
            ep_query = self._apply_pricing_filter(ep_query, TVEpisode, include_priced)

            for ep in ep_query.all():
                deleted.append({
                    "id": ep.id,
                    "type": "episode",
                    "provider": ep.provider,
                    "content_id": ep.content_id,
                })

            logger.info(f"Found {len(deleted)} deleted items since {since}")
            return deleted

    def generate_export(
        self,
        since: Optional[datetime] = None,
        providers: Optional[List[str]] = None,
        include_priced: bool = False,
    ) -> Dict[str, Any]:
        """Generate a full library export (builds the whole response in memory)."""
        logger.info(
            f"Generating export (since={since}, providers={providers}, "
            f"include_priced={include_priced})"
        )

        provider_priority = self.config.provider_priority

        with self.db.session() as session:
            from ..database.models import TVShow, TVEpisode, Movie, ShowProvider

            show_query = session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter(TVShow.is_available == True)

            movie_query = session.query(Movie).filter(Movie.is_available == True)
            episode_query = session.query(TVEpisode).filter(TVEpisode.is_available == True)

            if providers:
                show_query = show_query.join(ShowProvider).filter(
                    ShowProvider.provider.in_(providers),
                    ShowProvider.is_available == True
                ).distinct()
                movie_query = movie_query.filter(Movie.provider.in_(providers))
                episode_query = episode_query.filter(TVEpisode.provider.in_(providers))

            if since:
                show_query = show_query.filter(TVShow.last_seen >= since)
                movie_query = movie_query.filter(Movie.last_seen >= since)
                episode_query = episode_query.filter(TVEpisode.last_seen >= since)

            movie_query = self._apply_pricing_filter(movie_query, Movie, include_priced)
            episode_query = self._apply_pricing_filter(episode_query, TVEpisode, include_priced)

            shows = show_query.all()
            movies = movie_query.all()
            episodes = episode_query.all()

            response = {
                "version": "1.0",
                "timestamp": datetime.now(timezone.utc),
                "type": "incremental" if since else "full",
                "shows": [],
                "movies": [],
                "episodes": [],
                "deleted": [],
                "stats": {
                    "total_shows": len(shows),
                    "total_movies": len(movies),
                    "total_episodes": len(episodes),
                    "providers": providers or self.db.get_providers(),
                    "include_priced": include_priced,
                }
            }

            show_titles = {}
            for show in shows:
                response["shows"].append(self._get_show_summary(show))
                show_titles[show.id] = show.title

            for movie in movies:
                response["movies"].append(
                    self._get_movie_export(movie, provider_priority)
                )

            for episode in episodes:
                show_title = show_titles.get(episode.show_id, "Unknown Show")
                response["episodes"].append(
                    self._get_episode_export(episode, show_title, provider_priority)
                )

            if since:
                deleted = self._get_deleted_items(since, providers, include_priced)
                response["deleted"] = deleted
                response["stats"]["deleted"] = len(deleted)

            logger.info(
                f"Export generated: {len(response['shows'])} shows, "
                f"{len(response['movies'])} movies, "
                f"{len(response['episodes'])} episodes"
            )

            return response

    def generate_export_stream(
        self,
        since: Optional[datetime] = None,
        providers: Optional[List[str]] = None,
        include_priced: bool = False,
    ) -> Generator[str, None, None]:
        """
        Generate a library export as a streaming generator to reduce memory usage.

        Yields fragments of ONE well-formed JSON document -- commas and brackets
        are placed here, not by the caller. The caller (routes.py) must pass
        these fragments through unmodified.

        Notes on correctness:
        - Only ONE "stats" key is emitted (deleted count is computed up front
          and folded into the same stats object as the other counts).
        - provider_mappings uses selectinload, not joinedload, because
          joinedload's row-multiplying JOIN is unsafe to combine with
          yield_per() on a one-to-many relationship -- a show's mappings can
          be split across batch boundaries and silently truncated.
        """
        logger.info(
            f"Generating streaming export (since={since}, providers={providers}, "
            f"include_priced={include_priced})"
        )

        provider_priority = self.config.provider_priority

        with self.db.session() as session:
            from ..database.models import TVShow, TVEpisode, Movie, ShowProvider

            show_query = session.query(TVShow).options(
                selectinload(TVShow.provider_mappings)
            ).filter(TVShow.is_available == True)

            movie_query = session.query(Movie).filter(Movie.is_available == True)
            episode_query = session.query(TVEpisode).filter(TVEpisode.is_available == True)

            if providers:
                show_query = show_query.join(ShowProvider).filter(
                    ShowProvider.provider.in_(providers),
                    ShowProvider.is_available == True
                ).distinct()
                movie_query = movie_query.filter(Movie.provider.in_(providers))
                episode_query = episode_query.filter(TVEpisode.provider.in_(providers))

            if since:
                show_query = show_query.filter(TVShow.last_seen >= since)
                movie_query = movie_query.filter(Movie.last_seen >= since)
                episode_query = episode_query.filter(TVEpisode.last_seen >= since)

            movie_query = self._apply_pricing_filter(movie_query, Movie, include_priced)
            episode_query = self._apply_pricing_filter(episode_query, TVEpisode, include_priced)

            total_shows = show_query.count()
            total_movies = movie_query.count()
            total_episodes = episode_query.count()

            # Compute deleted items up front so they can be folded into the
            # single stats object below -- avoids emitting a second "stats" key.
            deleted = self._get_deleted_items(since, providers, include_priced) if since else []

            yield '{"version":"1.0",'
            yield f'"timestamp":"{datetime.now(timezone.utc).isoformat()}",'
            yield f'"type":"{"incremental" if since else "full"}",'
            yield (
                f'"stats":{{'
                f'"total_shows":{total_shows},'
                f'"total_movies":{total_movies},'
                f'"total_episodes":{total_episodes},'
                f'"providers":{json.dumps(providers or self.db.get_providers())},'
                f'"deleted":{len(deleted)},'
                f'"include_priced":{json.dumps(include_priced)}'
                f'}},'
            )

            # Stream shows
            yield '"shows":['
            first_show = True
            show_titles = {}
            for show in show_query.yield_per(100):
                if not first_show:
                    yield ','
                first_show = False
                show_dict = self._get_show_summary(show)
                yield json.dumps(show_dict, default=str)
                show_titles[show.id] = show.title
            yield '],'

            # Stream movies
            yield '"movies":['
            first_movie = True
            for movie in movie_query.yield_per(100):
                if not first_movie:
                    yield ','
                first_movie = False
                movie_dict = self._get_movie_export(movie, provider_priority)
                yield json.dumps(movie_dict, default=str)
            yield '],'

            # Stream episodes
            yield '"episodes":['
            first_episode = True
            for episode in episode_query.yield_per(100):
                if not first_episode:
                    yield ','
                first_episode = False
                show_title = show_titles.get(episode.show_id, "Unknown Show")
                episode_dict = self._get_episode_export(episode, show_title, provider_priority)
                yield json.dumps(episode_dict, default=str)
            yield '],'

            # Deleted items (already computed above)
            yield f'"deleted":{json.dumps(deleted, default=str)}'

            yield '}'

            logger.info(
                f"Streaming export complete: {total_shows} shows, "
                f"{total_movies} movies, {total_episodes} episodes, "
                f"{len(deleted)} deleted"
            )

    def get_status(self) -> Dict[str, Any]:
        """Get current status"""
        stats = self.db.get_stats()

        with self.db.session() as session:
            from ..database.models import CrawlHistory
            last_crawl = session.query(CrawlHistory).order_by(
                CrawlHistory.crawl_start.desc()
            ).first()

        return {
            "last_crawl": last_crawl.crawl_start if last_crawl else None,
            "status": "idle",
            "providers": self.db.get_providers(),
            "stats": stats,
            "version": "1.0",
        }