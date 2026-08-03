#!/usr/bin/env python3
"""
Library export generator - creates the full export response
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple, Generator
from urllib.parse import urlencode

from sqlalchemy.orm import joinedload, selectinload

from ..database import DatabaseManager
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
        """Build a plugin:// URL for Kodi"""
        params = urlencode(kwargs)
        return f"plugin://{self.plugin_id}/?{params}"

    def _get_item_hash(self, item: Dict[str, Any]) -> str:
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
        }
        clean = {k: v for k, v in hashable.items() if v is not None}
        sorted_json = json.dumps(clean, sort_keys=True)
        return hashlib.sha256(sorted_json.encode()).hexdigest()[:16]

    def _get_show_summary(self, show) -> Dict[str, Any]:
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
        }

    def _get_deleted_items(
        self,
        since: datetime,
        providers: Optional[List[str]] = None
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
        providers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Generate a full library export (builds the whole response in memory)."""
        logger.info(f"Generating export (since={since}, providers={providers})")

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
                deleted = self._get_deleted_items(since, providers)
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
        providers: Optional[List[str]] = None
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
        logger.info(f"Generating streaming export (since={since}, providers={providers})")

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

            total_shows = show_query.count()
            total_movies = movie_query.count()
            total_episodes = episode_query.count()

            # Compute deleted items up front so they can be folded into the
            # single stats object below -- avoids emitting a second "stats" key.
            deleted = self._get_deleted_items(since, providers) if since else []

            yield '{"version":"1.0",'
            yield f'"timestamp":"{datetime.now(timezone.utc).isoformat()}",'
            yield f'"type":"{"incremental" if since else "full"}",'
            yield (
                f'"stats":{{'
                f'"total_shows":{total_shows},'
                f'"total_movies":{total_movies},'
                f'"total_episodes":{total_episodes},'
                f'"providers":{json.dumps(providers or self.db.get_providers())},'
                f'"deleted":{len(deleted)}'
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