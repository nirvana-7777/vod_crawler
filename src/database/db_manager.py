#!/usr/bin/env python3
"""
Database manager for VOD crawler - Aligned with Backend VodItem/Content
"""

import uuid
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

from sqlalchemy import create_engine, text, distinct, exists, tuple_
from sqlalchemy.orm import sessionmaker, Session, scoped_session, joinedload
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .models import (
    Base, TVShow, TVEpisode, Movie,
    CrawlHistory, VodCache, ShowProvider
)
from ..utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages database operations for VOD crawler"""

    def __init__(self, db_path: str, config=None):
        """
        Initialize database manager

        Args:
            db_path: Path to SQLite database file
            config: Optional Config object for provider whitelist
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config

        # REMOVED StaticPool - use default connection pool
        # Each session gets its own connection, safe for multi-threaded use
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        self.SessionFactory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False
        )
        self.Session = scoped_session(self.SessionFactory)

        self._init_db()
        logger.info(f"Database initialized at {self.db_path}")

    def _init_db(self):
        Base.metadata.create_all(self.engine)
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            conn.execute(text("PRAGMA cache_size=10000"))
            conn.commit()

    @contextmanager
    def session(self) -> Session:
        session = self.SessionFactory()
        try:
            yield session
        except Exception as e:
            session.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            session.close()

    def _validate_provider(self, provider: str) -> None:
        if self.config and hasattr(self.config, 'provider_priority'):
            allowed = self.config.provider_priority
            if provider not in allowed:
                raise ValueError(f"Unknown provider: {provider}. Allowed: {allowed}")

    # ========== TV Show Operations ==========

    def get_or_create_show(
            self,
            normalized_title: str,
            title: str,
            provider: str,
            provider_id: str,
            **kwargs
    ) -> Tuple[TVShow, bool]:
        self._validate_provider(provider)

        with self.session() as session:
            show = session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter_by(
                normalized_title=normalized_title
            ).first()

            if show:
                show.title = title
                show.last_seen = datetime.now(timezone.utc)
                show.is_available = True

                provider_mapping = session.query(ShowProvider).filter_by(
                    show_id=show.id,
                    provider=provider
                ).first()

                if provider_mapping:
                    provider_mapping.provider_id = provider_id
                    provider_mapping.last_seen = datetime.now(timezone.utc)
                    provider_mapping.is_available = True
                else:
                    new_mapping = ShowProvider(
                        show_id=show.id,
                        provider=provider,
                        provider_id=provider_id,
                        first_seen=datetime.now(timezone.utc),
                        last_seen=datetime.now(timezone.utc),
                        is_available=True
                    )
                    show.provider_mappings.append(new_mapping)

                for key in ['plot', 'poster_url', 'backdrop_url', 'genres', 'genre',
                            'release_year', 'imdb_id', 'tmdb_id', 'tvdb_id']:
                    if key in kwargs and kwargs[key]:
                        setattr(show, key, kwargs[key])

                session.commit()
                return show, False
            else:
                show_id = slugify(normalized_title) or str(uuid.uuid4())
                existing = session.query(TVShow).filter_by(id=show_id).first()
                if existing:
                    show_id = str(uuid.uuid4())

                show = TVShow(
                    id=show_id,
                    title=title,
                    normalized_title=normalized_title,
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    is_available=True,
                )

                for key in ['plot', 'poster_url', 'backdrop_url', 'genres', 'genre',
                            'release_year', 'imdb_id', 'tmdb_id', 'tvdb_id']:
                    if key in kwargs and kwargs[key]:
                        setattr(show, key, kwargs[key])

                session.add(show)

                mapping = ShowProvider(
                    provider=provider,
                    provider_id=provider_id,
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    is_available=True
                )
                show.provider_mappings.append(mapping)

                session.commit()
                return show, True

    def get_show_by_id(self, show_id: str) -> Optional[TVShow]:
        with self.session() as session:
            return session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter_by(id=show_id).first()

    def get_show_by_provider_id(self, provider: str, provider_id: str) -> Optional[TVShow]:
        self._validate_provider(provider)
        with self.session() as session:
            mapping = session.query(ShowProvider).filter_by(
                provider=provider,
                provider_id=provider_id,
                is_available=True
            ).first()
            if mapping:
                return session.query(TVShow).options(
                    joinedload(TVShow.provider_mappings)
                ).filter_by(id=mapping.show_id).first()
            return None

    def get_show_provider_mapping(self, show_id: str, provider: str) -> Optional[ShowProvider]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(ShowProvider).filter_by(
                show_id=show_id,
                provider=provider
            ).first()

    def get_shows_for_provider(self, provider: str) -> List[TVShow]:
        self._validate_provider(provider)
        with self.session() as session:
            mappings = session.query(ShowProvider).filter_by(
                provider=provider,
                is_available=True
            ).all()
            show_ids = [m.show_id for m in mappings]
            if not show_ids:
                return []
            return session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter(
                TVShow.id.in_(show_ids),
                TVShow.is_available == True
            ).all()

    def update_show_availability(self, show_id: str, available: bool) -> bool:
        with self.session() as session:
            show = session.query(TVShow).filter_by(id=show_id).first()
            if not show:
                return False
            show.is_available = available
            show.last_seen = datetime.now(timezone.utc)
            session.commit()
            return True

    # ========== TV Episode Operations ==========

    def add_or_update_episode(
            self,
            show_id: str,
            provider: str,
            content_id: str,
            season_number: int,
            episode_number: int,
            **kwargs
    ) -> Tuple[TVEpisode, bool]:
        self._validate_provider(provider)
        with self.session() as session:
            episode = session.query(TVEpisode).filter_by(
                show_id=show_id,
                provider=provider,
                season_number=season_number,
                episode_number=episode_number
            ).first()

            if episode:
                episode.content_id = content_id
                episode.last_seen = datetime.now(timezone.utc)
                episode.is_available = True

                for key in ['title', 'series_title', 'air_date', 'duration_seconds',
                            'plot', 'long_description', 'rating', 'genres', 'genre',
                            'cast', 'director', 'mode', 'logo_url', 'manifest_url',
                            'manifest_script', 'session_manifest', 'license_url',
                            'certificate_url', 'drm_config', 'cdm_type', 'use_cdm',
                            'cdm_mode', 'video', 'on_demand', 'speed_up',
                            'streaming_format', 'quality', 'language', 'country',
                            'trailer_url', 'is_highlight']:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(episode, key, kwargs[key])
                session.commit()
                return episode, False
            else:
                episode_id = str(uuid.uuid4())
                episode = TVEpisode(
                    id=episode_id,
                    show_id=show_id,
                    provider=provider,
                    content_id=content_id,
                    season_number=season_number,
                    episode_number=episode_number,
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    is_available=True,
                )

                for key in ['title', 'series_title', 'air_date', 'duration_seconds',
                            'plot', 'long_description', 'rating', 'genres', 'genre',
                            'cast', 'director', 'mode', 'logo_url', 'manifest_url',
                            'manifest_script', 'session_manifest', 'license_url',
                            'certificate_url', 'drm_config', 'cdm_type', 'use_cdm',
                            'cdm_mode', 'video', 'on_demand', 'speed_up',
                            'streaming_format', 'quality', 'language', 'country',
                            'trailer_url', 'is_highlight']:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(episode, key, kwargs[key])
                session.add(episode)
                session.commit()
                return episode, True

    def bulk_upsert_episodes(self, episodes: List[Dict[str, Any]]) -> Dict[str, int]:
        """Bulk upsert episodes with accurate added/updated stats"""
        from sqlalchemy.dialects.sqlite import insert
        stats = {"added": 0, "updated": 0}

        if not episodes:
            return stats

        # Fields that should not be overwritten during an update
        immutable_fields = {'show_id', 'provider', 'season_number', 'episode_number', 'id', 'first_seen'}

        with self.session() as session:
            # Determine which rows already exist for accurate stats
            keys = [(e['show_id'], e['provider'], e['season_number'], e['episode_number']) for e in episodes]
            existing = set()
            if keys:
                # Use tuple_ for composite key lookup
                from sqlalchemy import and_
                for show_id, provider, season, episode_num in keys:
                    exists_query = session.query(TVEpisode.id).filter(
                        TVEpisode.show_id == show_id,
                        TVEpisode.provider == provider,
                        TVEpisode.season_number == season,
                        TVEpisode.episode_number == episode_num
                    ).first()
                    if exists_query:
                        existing.add((show_id, provider, season, episode_num))

            for ep_data in episodes:
                self._validate_provider(ep_data['provider'])

                # Ensure ID is set (generate if not present)
                if 'id' not in ep_data or not ep_data['id']:
                    ep_data['id'] = str(uuid.uuid4())

                # Dynamically build the update set
                update_set = {
                    k: v for k, v in ep_data.items()
                    if k not in immutable_fields
                }
                update_set['last_seen'] = datetime.now(timezone.utc)
                update_set['is_available'] = True

                stmt = insert(TVEpisode).values(**ep_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['show_id', 'provider', 'season_number', 'episode_number'],
                    set_=update_set
                )
                session.execute(stmt)

                # Accurate stats using pre-checked existing rows
                key = (ep_data['show_id'], ep_data['provider'],
                       ep_data['season_number'], ep_data['episode_number'])
                if key in existing:
                    stats["updated"] += 1
                else:
                    stats["added"] += 1
                    existing.add(key)  # Add to set so subsequent duplicates count as updates

            session.commit()
            return stats

    def get_episode_by_provider(self, provider: str, content_id: str) -> Optional[TVEpisode]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(TVEpisode).filter_by(
                provider=provider,
                content_id=content_id
            ).first()

    def get_episodes_for_show(self, show_id: str, provider: Optional[str] = None) -> List[TVEpisode]:
        with self.session() as session:
            query = session.query(TVEpisode).filter_by(
                show_id=show_id,
                is_available=True
            )
            if provider:
                self._validate_provider(provider)
                query = query.filter_by(provider=provider)
            return query.order_by(
                TVEpisode.season_number,
                TVEpisode.episode_number
            ).all()

    def get_episodes_for_provider(self, provider: str) -> List[TVEpisode]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(TVEpisode).filter_by(
                provider=provider,
                is_available=True
            ).order_by(
                TVEpisode.season_number,
                TVEpisode.episode_number
            ).all()

    def mark_episodes_unavailable(self, show_id: str, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            count = session.query(TVEpisode).filter_by(
                show_id=show_id,
                provider=provider
            ).update({
                "is_available": False,
                "last_seen": datetime.now(timezone.utc)
            }, synchronize_session=False)
            session.commit()
            return count

    def mark_all_episodes_unavailable(self, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            count = session.query(TVEpisode).filter_by(
                provider=provider
            ).update({
                "is_available": False,
                "last_seen": datetime.now(timezone.utc)
            }, synchronize_session=False)
            session.commit()
            return count

    # ========== Movie Operations ==========

    def add_or_update_movie(
            self,
            provider: str,
            content_id: str,
            title: str,
            **kwargs
    ) -> Tuple[Movie, bool]:
        self._validate_provider(provider)
        with self.session() as session:
            movie = session.query(Movie).filter_by(
                provider=provider,
                content_id=content_id
            ).first()
            movie_id = f"{provider}:{content_id}"

            if movie:
                movie.title = title
                movie.last_seen = datetime.now(timezone.utc)
                movie.is_available = True

                for key in ['title', 'original_title', 'plot', 'long_description',
                            'release_year', 'duration_seconds', 'rating', 'genres', 'genre',
                            'cast', 'director', 'imdb_id', 'tmdb_id', 'mode', 'logo_url',
                            'manifest_url', 'manifest_script', 'session_manifest',
                            'license_url', 'certificate_url', 'drm_config', 'cdm_type',
                            'use_cdm', 'cdm_mode', 'video', 'on_demand', 'speed_up',
                            'streaming_format', 'quality', 'language', 'country',
                            'trailer_url', 'is_highlight', 'is_sport']:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(movie, key, kwargs[key])
                session.commit()
                return movie, False
            else:
                movie = Movie(
                    id=movie_id,
                    provider=provider,
                    content_id=content_id,
                    title=title,
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    is_available=True,
                )

                for key in ['title', 'original_title', 'plot', 'long_description',
                            'release_year', 'duration_seconds', 'rating', 'genres', 'genre',
                            'cast', 'director', 'imdb_id', 'tmdb_id', 'mode', 'logo_url',
                            'manifest_url', 'manifest_script', 'session_manifest',
                            'license_url', 'certificate_url', 'drm_config', 'cdm_type',
                            'use_cdm', 'cdm_mode', 'video', 'on_demand', 'speed_up',
                            'streaming_format', 'quality', 'language', 'country',
                            'trailer_url', 'is_highlight', 'is_sport']:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(movie, key, kwargs[key])
                session.add(movie)
                session.commit()
                return movie, True

    def bulk_upsert_movies(self, movies: List[Dict[str, Any]]) -> Dict[str, int]:
        """Bulk upsert movies with accurate added/updated stats"""
        from sqlalchemy.dialects.sqlite import insert
        stats = {"added": 0, "updated": 0}

        if not movies:
            return stats

        immutable_fields = {'provider', 'content_id', 'id', 'first_seen'}

        with self.session() as session:
            # Determine which rows already exist for accurate stats
            keys = [(m['provider'], m['content_id']) for m in movies]
            existing = set()
            if keys:
                for provider, content_id in keys:
                    exists_query = session.query(Movie.id).filter(
                        Movie.provider == provider,
                        Movie.content_id == content_id
                    ).first()
                    if exists_query:
                        existing.add((provider, content_id))

            for movie_data in movies:
                self._validate_provider(movie_data['provider'])

                # Ensure ID is set (derived from provider:content_id)
                movie_data['id'] = f"{movie_data['provider']}:{movie_data['content_id']}"

                update_set = {
                    k: v for k, v in movie_data.items()
                    if k not in immutable_fields
                }
                update_set['last_seen'] = datetime.now(timezone.utc)
                update_set['is_available'] = True

                stmt = insert(Movie).values(**movie_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['provider', 'content_id'],
                    set_=update_set
                )
                session.execute(stmt)

                # Accurate stats using pre-checked existing rows
                key = (movie_data['provider'], movie_data['content_id'])
                if key in existing:
                    stats["updated"] += 1
                else:
                    stats["added"] += 1
                    existing.add(key)  # Add to set so subsequent duplicates count as updates

            session.commit()
            return stats

    def get_movie_by_provider(self, provider: str, content_id: str) -> Optional[Movie]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(Movie).filter_by(
                provider=provider,
                content_id=content_id
            ).first()

    def get_movies_by_provider(self, provider: str) -> List[Movie]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(Movie).filter_by(
                provider=provider,
                is_available=True
            ).order_by(Movie.release_year.desc()).all()

    def mark_movies_unavailable(self, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            count = session.query(Movie).filter_by(
                provider=provider
            ).update({
                "is_available": False,
                "last_seen": datetime.now(timezone.utc)
            }, synchronize_session=False)
            session.commit()
            return count

    # ========== Crawl History Operations ==========

    def start_crawl(self, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            history = CrawlHistory(
                provider=provider,
                crawl_start=datetime.now(timezone.utc),
                status="running"
            )
            session.add(history)
            session.flush()
            session.commit()
            return history.id

    def finish_crawl(
            self,
            history_id: int,
            status: str,
            items_found: int = 0,
            items_added: int = 0,
            items_removed: int = 0,
            items_updated: int = 0,
            error_message: str = None,
            details: Dict = None
    ):
        with self.session() as session:
            history = session.query(CrawlHistory).filter_by(id=history_id).first()
            if history:
                history.crawl_end = datetime.now(timezone.utc)
                history.status = status
                history.items_found = items_found
                history.items_added = items_added
                history.items_removed = items_removed
                history.items_updated = items_updated
                if error_message:
                    history.error_message = error_message
                if details:
                    history.details = details
                session.commit()

    def get_last_crawl(self, provider: str) -> Optional[CrawlHistory]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(CrawlHistory).filter_by(
                provider=provider
            ).order_by(CrawlHistory.crawl_start.desc()).first()

    def get_crawl_history(self, provider: str, limit: int = 10) -> List[CrawlHistory]:
        self._validate_provider(provider)
        with self.session() as session:
            return session.query(CrawlHistory).filter_by(
                provider=provider
            ).order_by(CrawlHistory.crawl_start.desc()).limit(limit).all()

    # ========== VOD Cache Operations ==========

    def cache_vod_data(
            self,
            provider: str,
            content_id: str,
            content_type: str,
            data: Dict[str, Any]
    ):
        self._validate_provider(provider)
        with self.session() as session:
            hash_value = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()
            cache = session.query(VodCache).filter_by(
                provider=provider,
                content_id=content_id,
                content_type=content_type
            ).first()
            if cache:
                cache.raw_data = data
                cache.hash = hash_value
                cache.updated_at = datetime.now(timezone.utc)
            else:
                cache = VodCache(
                    provider=provider,
                    content_id=content_id,
                    content_type=content_type,
                    raw_data=data,
                    hash=hash_value,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                session.add(cache)
            session.commit()

    def get_cached_vod_data(
            self,
            provider: str,
            content_id: str,
            content_type: str
    ) -> Optional[Dict[str, Any]]:
        self._validate_provider(provider)
        with self.session() as session:
            cache = session.query(VodCache).filter_by(
                provider=provider,
                content_id=content_id,
                content_type=content_type
            ).first()
            return cache.raw_data if cache else None

    def get_vod_hash(
            self,
            provider: str,
            content_id: str,
            content_type: str
    ) -> Optional[str]:
        self._validate_provider(provider)
        with self.session() as session:
            cache = session.query(VodCache).filter_by(
                provider=provider,
                content_id=content_id,
                content_type=content_type
            ).first()
            return cache.hash if cache else None

    # ========== Provider Operations ==========

    def get_providers(self) -> List[str]:
        with self.session() as session:
            episode_providers = session.query(distinct(TVEpisode.provider)).filter(
                TVEpisode.is_available == True
            ).all()
            movie_providers = session.query(distinct(Movie.provider)).filter(
                Movie.is_available == True
            ).all()
            mapping_providers = session.query(distinct(ShowProvider.provider)).filter(
                ShowProvider.is_available == True
            ).all()

            providers = set()
            for p in episode_providers:
                providers.add(p[0])
            for p in movie_providers:
                providers.add(p[0])
            for p in mapping_providers:
                providers.add(p[0])
            return sorted(list(providers))

    def clear_provider_data(self, provider: str) -> Dict[str, int]:
        self._validate_provider(provider)
        results = {}
        with self.session() as session:
            mapping_count = session.query(ShowProvider).filter_by(
                provider=provider,
                is_available=True
            ).update({
                "is_available": False,
                "last_seen": datetime.now(timezone.utc)
            }, synchronize_session=False)
            results["show_mappings"] = mapping_count

            show_ids = session.query(ShowProvider.show_id).filter_by(
                provider=provider
            ).all()
            show_ids = [s[0] for s in show_ids]

            if show_ids:
                shows_to_update = session.query(TVShow.id).filter(
                    TVShow.id.in_(show_ids),
                    ~exists().where(
                        ShowProvider.show_id == TVShow.id,
                        ShowProvider.provider != provider,
                        ShowProvider.is_available == True
                    )
                ).all()
                shows_to_update = [s[0] for s in shows_to_update]

                if shows_to_update:
                    session.query(TVShow).filter(
                        TVShow.id.in_(shows_to_update)
                    ).update({
                        "is_available": False,
                        "last_seen": datetime.now(timezone.utc)
                    }, synchronize_session=False)

            results["shows_removed"] = len(shows_to_update) if show_ids else 0

            episode_count = session.query(TVEpisode).filter_by(
                provider=provider,
                is_available=True
            ).update({
                "is_available": False,
                "last_seen": datetime.now(timezone.utc)
            }, synchronize_session=False)
            results["episodes"] = episode_count

            movie_count = session.query(Movie).filter_by(
                provider=provider,
                is_available=True
            ).update({
                "is_available": False,
                "last_seen": datetime.now(timezone.utc)
            }, synchronize_session=False)
            results["movies"] = movie_count

            session.commit()
            return results

    def get_stats(self) -> Dict[str, Any]:
        with self.session() as session:
            tv_shows = session.query(TVShow).filter_by(is_available=True).count()
            tv_episodes = session.query(TVEpisode).filter_by(is_available=True).count()
            movies = session.query(Movie).filter_by(is_available=True).count()

            providers = self.get_providers()
            provider_stats = {}
            for provider in providers:
                episodes = session.query(TVEpisode).filter_by(
                    provider=provider,
                    is_available=True
                ).count()
                movies_count = session.query(Movie).filter_by(
                    provider=provider,
                    is_available=True
                ).count()
                show_mappings = session.query(ShowProvider).filter_by(
                    provider=provider,
                    is_available=True
                ).count()
                provider_stats[provider] = {
                    "episodes": episodes,
                    "movies": movies_count,
                    "shows": show_mappings
                }
            return {
                "total_tv_shows": tv_shows,
                "total_tv_episodes": tv_episodes,
                "total_movies": movies,
                "provider_stats": provider_stats,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }


def slugify(text: str) -> str:
    import re
    import unicodedata
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r'[\s\-–—/\\|]+', '_', text)
    text = re.sub(r'[^\w\-_]', '', text)
    text = re.sub(r'_+', '_', text)
    return text.strip('_-')