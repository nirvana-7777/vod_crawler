#!/usr/bin/env python3
"""
Database manager for VOD crawler - Aligned with Backend VodItem/Content
"""

import uuid
import hashlib
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

from sqlalchemy import create_engine, text, distinct, exists, tuple_, func
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import sessionmaker, Session, scoped_session, joinedload
from sqlalchemy.exc import IntegrityError

from .models import (
    Base, TVShow, TVEpisode, Movie, CrawlHistory, VodCache, ShowProvider,
    SyncState, SyncJob, PRICING_FIELDS,
)
from ..utils.logger import get_logger
from ..utils.time import utcnow
from ..utils.slugify import slugify

logger = get_logger(__name__)

# Whitelisted mutable fields for the single-row upsert helpers. Kept as
# module-level constants (rather than re-typed in every method) so the
# episode/movie field lists and the pricing fields can't silently drift
# out of sync with each other.
_EPISODE_MUTABLE_FIELDS = [
    'title', 'series_title', 'air_date', 'duration_seconds',
    'plot', 'long_description', 'rating', 'genres', 'genre',
    'cast', 'director', 'mode', 'logo_url', 'manifest_url',
    'manifest_script', 'session_manifest', 'license_url',
    'certificate_url', 'drm_config', 'cdm_type', 'use_cdm',
    'cdm_mode', 'video', 'on_demand', 'speed_up',
    'streaming_format', 'quality', 'language', 'country',
    'trailer_url', 'is_highlight',
] + PRICING_FIELDS

_MOVIE_MUTABLE_FIELDS = [
    'title', 'original_title', 'plot', 'long_description',
    'release_year', 'duration_seconds', 'rating', 'genres', 'genre',
    'cast', 'director', 'imdb_id', 'tmdb_id', 'mode', 'logo_url',
    'manifest_url', 'manifest_script', 'session_manifest',
    'license_url', 'certificate_url', 'drm_config', 'cdm_type',
    'use_cdm', 'cdm_mode', 'video', 'on_demand', 'speed_up',
    'streaming_format', 'quality', 'language', 'country',
    'trailer_url', 'is_highlight', 'is_sport',
] + PRICING_FIELDS


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

        # Each session gets its own connection from the default pool --
        # safe for multi-threaded use (no StaticPool).
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
            conn.execute(text("PRAGMA mmap_size=268435456"))  # 256MB
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()

    @contextmanager
    def session(self) -> Session:
        session = self.SessionFactory()
        try:
            yield session
        except Exception:
            session.rollback()
            logger.exception("Database error")
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
    ) -> Tuple[str, bool]:
        """
        Get or create a TV show. Returns (show_id, created) rather than the
        ORM object -- callers (the crawler) only ever need the id to attach
        episodes to, and returning a bare id sidesteps any risk of a
        DetachedInstanceError if a caller touches a lazily-loaded attribute
        after this session has closed.
        """
        self._validate_provider(provider)

        with self.session() as session:
            # Look up by (provider, provider_id) FIRST. This is the
            # authoritative identity for "have we already seen this exact
            # series from this provider" -- normalized_title alone is not
            # reliable because the same show can arrive with subtly
            # different title text across different lanes/categories
            # (extra whitespace, promo suffixes, etc.), which would
            # otherwise produce two different TVShow rows for what is
            # really one show, and the second one's ShowProvider insert
            # then collides on the (provider, provider_id) unique
            # constraint.
            show = None
            if provider_id:
                existing_mapping = session.query(ShowProvider).filter_by(
                    provider=provider,
                    provider_id=provider_id
                ).first()
                if existing_mapping:
                    show = session.query(TVShow).filter_by(id=existing_mapping.show_id).first()

            if show is None:
                show = session.query(TVShow).filter_by(
                    normalized_title=normalized_title
                ).first()

            if show:
                show.title = title
                show.last_seen = utcnow()
                show.is_available = True

                provider_mapping = session.query(ShowProvider).filter_by(
                    show_id=show.id,
                    provider=provider
                ).first()

                if provider_mapping:
                    provider_mapping.provider_id = provider_id
                    provider_mapping.last_seen = utcnow()
                    provider_mapping.is_available = True
                else:
                    session.add(ShowProvider(
                        show_id=show.id,
                        provider=provider,
                        provider_id=provider_id,
                        first_seen=utcnow(),
                        last_seen=utcnow(),
                        is_available=True
                    ))

                for key in ['plot', 'poster_url', 'backdrop_url', 'genres', 'genre',
                            'release_year', 'imdb_id', 'tmdb_id', 'tvdb_id']:
                    if key in kwargs and kwargs[key]:
                        setattr(show, key, kwargs[key])

                try:
                    session.commit()
                except IntegrityError:
                    # Defensive fallback: another concurrent/duplicate path
                    # already inserted this exact (provider, provider_id)
                    # mapping between our lookup and our commit. Roll back
                    # our attempt and just return the row that won.
                    session.rollback()
                    logger.warning(
                        f"Race on show_providers ({provider}, {provider_id}); "
                        f"using the row that was already committed"
                    )
                    winner_mapping = session.query(ShowProvider).filter_by(
                        provider=provider,
                        provider_id=provider_id
                    ).first()
                    return winner_mapping.show_id, False

                return show.id, False
            else:
                show_id = slugify(normalized_title) or str(uuid.uuid4())
                existing = session.query(TVShow.id).filter_by(id=show_id).first()
                if existing:
                    show_id = str(uuid.uuid4())

                show = TVShow(
                    id=show_id,
                    title=title,
                    normalized_title=normalized_title,
                    first_seen=utcnow(),
                    last_seen=utcnow(),
                    is_available=True,
                )

                for key in ['plot', 'poster_url', 'backdrop_url', 'genres', 'genre',
                            'release_year', 'imdb_id', 'tmdb_id', 'tvdb_id']:
                    if key in kwargs and kwargs[key]:
                        setattr(show, key, kwargs[key])

                session.add(show)
                session.add(ShowProvider(
                    show_id=show_id,
                    provider=provider,
                    provider_id=provider_id,
                    first_seen=utcnow(),
                    last_seen=utcnow(),
                    is_available=True
                ))

                try:
                    session.commit()
                except IntegrityError:
                    # Same race as above, but on the create path: someone
                    # else committed this (provider, provider_id) mapping
                    # between our lookup and our insert. Discard our new
                    # (now orphaned) show row and use the winner instead.
                    session.rollback()
                    logger.warning(
                        f"Race creating show for ({provider}, {provider_id}); "
                        f"using the row that was already committed"
                    )
                    winner_mapping = session.query(ShowProvider).filter_by(
                        provider=provider,
                        provider_id=provider_id
                    ).first()
                    return winner_mapping.show_id, False

                return show_id, True

    def get_show_by_id(self, show_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a show as a plain dict.

        Returned as a dict (not the ORM object) because callers (the API
        routes) only ever read plain fields from this after the session
        that produced it has closed -- returning a dict makes that safe
        and explicit instead of relying on expire_on_commit=False.
        """
        with self.session() as session:
            show = session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter_by(id=show_id).first()
            return show.to_dict() if show else None

    def get_show_by_provider_id(self, provider: str, provider_id: str) -> Optional[Dict[str, Any]]:
        """Get a show by (provider, provider_id) as a dict."""
        self._validate_provider(provider)
        with self.session() as session:
            mapping = session.query(ShowProvider).filter_by(
                provider=provider,
                provider_id=provider_id,
                is_available=True
            ).first()
            if not mapping:
                return None
            show = session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter_by(id=mapping.show_id).first()
            return show.to_dict() if show else None

    def get_show_provider_mapping(self, show_id: str, provider: str) -> Optional[Dict[str, Any]]:
        """Get a show/provider mapping as a dict."""
        self._validate_provider(provider)
        with self.session() as session:
            mapping = session.query(ShowProvider).filter_by(
                show_id=show_id,
                provider=provider
            ).first()
            return mapping.to_dict() if mapping else None

    def get_shows_for_provider(self, provider: str) -> List[Dict[str, Any]]:
        """Get all shows available on a provider, as a list of dicts."""
        self._validate_provider(provider)
        with self.session() as session:
            mappings = session.query(ShowProvider).filter_by(
                provider=provider,
                is_available=True
            ).all()
            show_ids = [m.show_id for m in mappings]
            if not show_ids:
                return []
            shows = session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter(
                TVShow.id.in_(show_ids),
                TVShow.is_available == True
            ).all()
            return [s.to_dict() for s in shows]

    def update_show_availability(self, show_id: str, available: bool) -> bool:
        with self.session() as session:
            show = session.query(TVShow).filter_by(id=show_id).first()
            if not show:
                return False
            show.is_available = available
            show.last_seen = utcnow()
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
            # Identity is (provider, content_id) -- NOT (show_id, provider,
            # season_number, episode_number). Season/episode numbers are
            # data, not identity: multiple distinct episodes can legitimately
            # both lack real values and default to (0, 0), and that must not
            # make them collide.
            episode = session.query(TVEpisode).filter_by(
                provider=provider,
                content_id=content_id
            ).first()

            if episode:
                episode.show_id = show_id
                episode.season_number = season_number
                episode.episode_number = episode_number
                episode.last_seen = utcnow()
                episode.is_available = True

                for key in _EPISODE_MUTABLE_FIELDS:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(episode, key, kwargs[key])
                session.commit()
                return episode, False
            else:
                episode = TVEpisode(
                    id=str(uuid.uuid4()),
                    show_id=show_id,
                    provider=provider,
                    content_id=content_id,
                    season_number=season_number,
                    episode_number=episode_number,
                    first_seen=utcnow(),
                    last_seen=utcnow(),
                    is_available=True,
                )

                for key in _EPISODE_MUTABLE_FIELDS:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(episode, key, kwargs[key])
                session.add(episode)
                session.commit()
                return episode, True

    def bulk_upsert_episodes(self, episodes: List[Dict[str, Any]]) -> Dict[str, int]:
        """Bulk upsert episodes with accurate added/updated stats"""
        stats = {"added": 0, "updated": 0}

        if not episodes:
            return stats

        # Identity is (provider, content_id) -- matches the
        # uq_episode_provider_content constraint. season_number/
        # episode_number are data, not identity: distinct episodes can
        # legitimately share (0, 0) when a provider doesn't supply real
        # values, and content_id is what actually disambiguates them.
        immutable_fields = {'provider', 'content_id', 'id', 'first_seen'}

        with self.session() as session:
            # Single batched existence check instead of one query per row.
            keys = [(e['provider'], e['content_id']) for e in episodes]
            existing = set()
            if keys:
                rows = session.query(TVEpisode.provider, TVEpisode.content_id).filter(
                    tuple_(TVEpisode.provider, TVEpisode.content_id).in_(keys)
                ).all()
                existing = {(p, c) for p, c in rows}

            for ep_data in episodes:
                self._validate_provider(ep_data['provider'])

                if not ep_data.get('id'):
                    ep_data['id'] = str(uuid.uuid4())

                update_set = {
                    k: v for k, v in ep_data.items()
                    if k not in immutable_fields
                }
                update_set['last_seen'] = utcnow()
                update_set['is_available'] = True

                stmt = insert(TVEpisode).values(**ep_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['provider', 'content_id'],
                    set_=update_set
                )
                session.execute(stmt)

                key = (ep_data['provider'], ep_data['content_id'])
                if key in existing:
                    stats["updated"] += 1
                else:
                    stats["added"] += 1
                    existing.add(key)  # subsequent duplicates in this batch count as updates

            session.commit()
            return stats

    def get_episode_by_provider(self, provider: str, content_id: str) -> Optional[Dict[str, Any]]:
        """Get an episode by (provider, content_id) as a dict."""
        self._validate_provider(provider)
        with self.session() as session:
            episode = session.query(TVEpisode).filter_by(
                provider=provider,
                content_id=content_id
            ).first()
            return episode.to_dict() if episode else None

    def get_episodes_for_show(self, show_id: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get episodes for a show, as a list of dicts."""
        with self.session() as session:
            query = session.query(TVEpisode).filter_by(
                show_id=show_id,
                is_available=True
            )
            if provider:
                self._validate_provider(provider)
                query = query.filter_by(provider=provider)
            episodes = query.order_by(
                TVEpisode.season_number,
                TVEpisode.episode_number
            ).all()
            return [e.to_dict() for e in episodes]

    def get_episodes_for_provider(self, provider: str) -> List[Dict[str, Any]]:
        """Get all episodes for a provider, as a list of dicts."""
        self._validate_provider(provider)
        with self.session() as session:
            episodes = session.query(TVEpisode).filter_by(
                provider=provider,
                is_available=True
            ).order_by(
                TVEpisode.season_number,
                TVEpisode.episode_number
            ).all()
            return [e.to_dict() for e in episodes]

    def mark_episodes_unavailable(self, show_id: str, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            count = session.query(TVEpisode).filter_by(
                show_id=show_id,
                provider=provider
            ).update({
                "is_available": False,
                "last_seen": utcnow()
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
                "last_seen": utcnow()
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
                movie.last_seen = utcnow()
                movie.is_available = True

                for key in _MOVIE_MUTABLE_FIELDS:
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
                    first_seen=utcnow(),
                    last_seen=utcnow(),
                    is_available=True,
                )

                for key in _MOVIE_MUTABLE_FIELDS:
                    if key in kwargs and kwargs[key] is not None:
                        setattr(movie, key, kwargs[key])
                session.add(movie)
                session.commit()
                return movie, True

    def bulk_upsert_movies(self, movies: List[Dict[str, Any]]) -> Dict[str, int]:
        """Bulk upsert movies with accurate added/updated stats"""
        stats = {"added": 0, "updated": 0}

        if not movies:
            return stats

        immutable_fields = {'provider', 'content_id', 'id', 'first_seen'}

        with self.session() as session:
            # Single batched existence check instead of one query per row.
            keys = [(m['provider'], m['content_id']) for m in movies]
            existing = set()
            if keys:
                rows = session.query(Movie.provider, Movie.content_id).filter(
                    tuple_(Movie.provider, Movie.content_id).in_(keys)
                ).all()
                existing = {(p, c) for p, c in rows}

            for movie_data in movies:
                self._validate_provider(movie_data['provider'])

                # Id is derived from provider:content_id, not client-supplied.
                movie_data['id'] = f"{movie_data['provider']}:{movie_data['content_id']}"

                update_set = {
                    k: v for k, v in movie_data.items()
                    if k not in immutable_fields
                }
                update_set['last_seen'] = utcnow()
                update_set['is_available'] = True

                stmt = insert(Movie).values(**movie_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['provider', 'content_id'],
                    set_=update_set
                )
                session.execute(stmt)

                key = (movie_data['provider'], movie_data['content_id'])
                if key in existing:
                    stats["updated"] += 1
                else:
                    stats["added"] += 1
                    existing.add(key)

            session.commit()
            return stats

    def get_movie_by_provider(self, provider: str, content_id: str) -> Optional[Dict[str, Any]]:
        """Get a movie by (provider, content_id) as a dict."""
        self._validate_provider(provider)
        with self.session() as session:
            movie = session.query(Movie).filter_by(
                provider=provider,
                content_id=content_id
            ).first()
            return movie.to_dict() if movie else None

    def get_movies_by_provider(self, provider: str) -> List[Dict[str, Any]]:
        """Get all movies for a provider, as a list of dicts."""
        self._validate_provider(provider)
        with self.session() as session:
            movies = session.query(Movie).filter_by(
                provider=provider,
                is_available=True
            ).order_by(Movie.release_year.desc()).all()
            return [m.to_dict() for m in movies]

    def mark_movies_unavailable(self, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            count = session.query(Movie).filter_by(
                provider=provider
            ).update({
                "is_available": False,
                "last_seen": utcnow()
            }, synchronize_session=False)
            session.commit()
            return count

    # ========== Crawl History Operations ==========

    def start_crawl(self, provider: str) -> int:
        self._validate_provider(provider)
        with self.session() as session:
            history = CrawlHistory(
                provider=provider,
                crawl_start=utcnow(),
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
                history.crawl_end = utcnow()
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

    def get_last_crawl(self, provider: str) -> Optional[Dict[str, Any]]:
        """Get the most recent crawl history entry for a provider, as a dict."""
        self._validate_provider(provider)
        with self.session() as session:
            history = session.query(CrawlHistory).filter_by(
                provider=provider
            ).order_by(CrawlHistory.crawl_start.desc()).first()
            return history.to_dict() if history else None

    def get_crawl_history(self, provider: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent crawl history for a provider, as a list of dicts."""
        self._validate_provider(provider)
        with self.session() as session:
            histories = session.query(CrawlHistory).filter_by(
                provider=provider
            ).order_by(CrawlHistory.crawl_start.desc()).limit(limit).all()
            return [h.to_dict() for h in histories]

    # ========== Sync State Operations (multi-worker safe) ==========

    def get_sync_state(self) -> Dict[str, Any]:
        """Get current sync state. Safe to call from any worker process."""
        with self.session() as session:
            state = session.query(SyncState).filter_by(id=1).first()
            if not state:
                return {"sync_in_progress": False, "sync_progress": None}
            return {
                "sync_in_progress": state.in_progress,
                "sync_progress": state.progress,
            }

    def set_sync_state(self, in_progress: bool, progress: Optional[Dict[str, Any]] = None) -> None:
        """Set sync state. Safe to call from any worker process."""
        with self.session() as session:
            state = session.query(SyncState).filter_by(id=1).first()
            if not state:
                state = SyncState(id=1)
                session.add(state)
            state.in_progress = in_progress
            state.progress = progress
            state.updated_at = utcnow()
            session.commit()

    # ========== Sync Job Operations (history/auditing) ==========

    def create_sync_job(self, provider: Optional[str] = None) -> str:
        """Create a new sync job record and return its id."""
        with self.session() as session:
            job = SyncJob(provider=provider, status="pending", started_at=utcnow())
            session.add(job)
            session.commit()
            return job.id

    def update_sync_job(
            self,
            job_id: str,
            status: str,
            error: Optional[str] = None,
            progress: Optional[Dict[str, Any]] = None,
            items_found: Optional[int] = None,
            items_added: Optional[int] = None,
            items_updated: Optional[int] = None,
            items_removed: Optional[int] = None,
    ) -> None:
        with self.session() as session:
            job = session.query(SyncJob).filter_by(id=job_id).first()
            if not job:
                logger.warning(f"update_sync_job: job {job_id} not found")
                return

            job.status = status
            if error is not None:
                job.error = error
            if progress is not None:
                job.progress = progress
            if items_found is not None:
                job.items_found = items_found
            if items_added is not None:
                job.items_added = items_added
            if items_updated is not None:
                job.items_updated = items_updated
            if items_removed is not None:
                job.items_removed = items_removed
            if status in ("complete", "failed"):
                job.completed_at = utcnow()

            session.commit()

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
                json.dumps(data, sort_keys=True, default=str).encode()
            ).hexdigest()
            cache = session.query(VodCache).filter_by(
                provider=provider,
                content_id=content_id,
                content_type=content_type
            ).first()
            if cache:
                cache.raw_data = data
                cache.content_hash = hash_value
                cache.updated_at = utcnow()
            else:
                cache = VodCache(
                    provider=provider,
                    content_id=content_id,
                    content_type=content_type,
                    raw_data=data,
                    content_hash=hash_value,
                    created_at=utcnow(),
                    updated_at=utcnow()
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
            return cache.content_hash if cache else None

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
            return sorted(providers)

    def clear_provider_data(self, provider: str) -> Dict[str, int]:
        self._validate_provider(provider)
        results = {}
        with self.session() as session:
            mapping_count = session.query(ShowProvider).filter_by(
                provider=provider,
                is_available=True
            ).update({
                "is_available": False,
                "last_seen": utcnow()
            }, synchronize_session=False)
            results["show_mappings"] = mapping_count

            show_ids = session.query(ShowProvider.show_id).filter_by(
                provider=provider
            ).all()
            show_ids = [s[0] for s in show_ids]

            shows_to_update = []
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
                        "last_seen": utcnow()
                    }, synchronize_session=False)

            results["shows_removed"] = len(shows_to_update)

            episode_count = session.query(TVEpisode).filter_by(
                provider=provider,
                is_available=True
            ).update({
                "is_available": False,
                "last_seen": utcnow()
            }, synchronize_session=False)
            results["episodes"] = episode_count

            movie_count = session.query(Movie).filter_by(
                provider=provider,
                is_available=True
            ).update({
                "is_available": False,
                "last_seen": utcnow()
            }, synchronize_session=False)
            results["movies"] = movie_count

            session.commit()
            return results

    def get_stats(self) -> Dict[str, Any]:
        """
        Aggregate library stats.

        Uses GROUP BY per table instead of one COUNT() query per
        (provider, table) pair -- with N providers this was previously
        1 + 3N queries; now it's a fixed 6.
        """
        with self.session() as session:
            episode_counts = dict(
                session.query(TVEpisode.provider, func.count(TVEpisode.id))
                .filter(TVEpisode.is_available == True)
                .group_by(TVEpisode.provider)
                .all()
            )
            movie_counts = dict(
                session.query(Movie.provider, func.count(Movie.id))
                .filter(Movie.is_available == True)
                .group_by(Movie.provider)
                .all()
            )
            show_counts = dict(
                session.query(ShowProvider.provider, func.count(ShowProvider.show_id))
                .filter(ShowProvider.is_available == True)
                .group_by(ShowProvider.provider)
                .all()
            )

            all_providers = set(episode_counts) | set(movie_counts) | set(show_counts)
            provider_stats = {
                provider: {
                    "episodes": episode_counts.get(provider, 0),
                    "movies": movie_counts.get(provider, 0),
                    "shows": show_counts.get(provider, 0),
                }
                for provider in all_providers
            }

            return {
                "total_tv_shows": session.query(TVShow).filter_by(is_available=True).count(),
                "total_tv_episodes": session.query(TVEpisode).filter_by(is_available=True).count(),
                "total_movies": session.query(Movie).filter_by(is_available=True).count(),
                "provider_stats": provider_stats,
                "last_updated": utcnow().isoformat(),
            }