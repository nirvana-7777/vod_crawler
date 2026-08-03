#!/usr/bin/env python3
"""
SQLAlchemy models for VOD crawler database - Aligned with Backend VodItem/Content
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any

from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, Text, 
    ForeignKey, Index, JSON, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class ShowProvider(Base):
    """Association table for TV show provider mappings"""
    __tablename__ = "show_providers"

    show_id = Column(String(255), ForeignKey("tv_shows.id", ondelete="CASCADE"), primary_key=True)
    provider = Column(String(100), primary_key=True)
    provider_id = Column(String(255), nullable=False)
    
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_available = Column(Boolean, default=True)

    show = relationship("TVShow", back_populates="provider_mappings")

    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_show_provider_provider_id"),
        Index("idx_show_providers_provider", "provider"),
        Index("idx_show_providers_provider_id", "provider_id"),
        Index("idx_show_providers_available", "is_available"),
    )

    def __repr__(self):
        return f"<ShowProvider show_id={self.show_id!r} provider={self.provider!r} provider_id={self.provider_id!r}>"


class TVShow(Base):
    """Normalized TV show (merged across providers)"""
    __tablename__ = "tv_shows"

    id = Column(String(255), primary_key=True)
    title = Column(String(500), nullable=False)
    normalized_title = Column(String(500), unique=True, nullable=False)
    plot = Column(Text, nullable=True)  # Maps to backend 'description'
    poster_url = Column(String(1000), nullable=True)
    backdrop_url = Column(String(1000), nullable=True)
    genres = Column(JSON, nullable=True)
    genre = Column(String(255), nullable=True)  # Primary genre (backend expects this)
    release_year = Column(Integer, nullable=True)
    imdb_id = Column(String(50), nullable=True)
    tmdb_id = Column(String(50), nullable=True)
    tvdb_id = Column(String(50), nullable=True)
    
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_available = Column(Boolean, default=True)

    episodes = relationship("TVEpisode", back_populates="show", cascade="all, delete-orphan")
    provider_mappings = relationship("ShowProvider", back_populates="show", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_tv_shows_available", "is_available"),
        Index("idx_tv_shows_release_year", "release_year"),
    )

    def __repr__(self):
        return f"<TVShow id={self.id!r} title={self.title!r} normalized={self.normalized_title!r}>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "normalized_title": self.normalized_title,
            "plot": self.plot,
            "poster_url": self.poster_url,
            "backdrop_url": self.backdrop_url,
            "genres": self.genres,
            "genre": self.genre,
            "release_year": self.release_year,
            "imdb_id": self.imdb_id,
            "tmdb_id": self.tmdb_id,
            "tvdb_id": self.tvdb_id,
            "provider_ids": {m.provider: m.provider_id for m in self.provider_mappings if m.is_available},
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "is_available": self.is_available,
        }


class TVEpisode(Base):
    """Individual TV episode with provider-specific data (Maps to VodItem)"""
    __tablename__ = "tv_episodes"

    id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
    show_id = Column(String(255), ForeignKey("tv_shows.id", ondelete="CASCADE"), nullable=False, index=True)
    
    provider = Column(String(100), nullable=False)
    content_id = Column(String(255), nullable=False)
    season_number = Column(Integer, nullable=False)
    episode_number = Column(Integer, nullable=False)
    title = Column(String(500), nullable=True)  # Maps to backend 'name'
    series_title = Column(String(500), nullable=True)  # Backend expects this on the item
    
    # Metadata
    plot = Column(Text, nullable=True)  # Maps to backend 'description'
    long_description = Column(Text, nullable=True)
    air_date = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    rating = Column(String(50), nullable=True)
    genres = Column(JSON, nullable=True)
    genre = Column(String(255), nullable=True)
    cast = Column(JSON, nullable=True)
    director = Column(String(255), nullable=True)
    
    # Streaming Configuration (Matches backend Content/VodItem)
    mode = Column(String(20), default="vod")
    logo_url = Column(String(1000), nullable=True)
    manifest_url = Column(String(1000), nullable=True)
    manifest_script = Column(Text, nullable=True)  # For dynamic manifests
    session_manifest = Column(Boolean, default=False)
    
    # DRM / CDM
    license_url = Column(String(1000), nullable=True)
    certificate_url = Column(String(1000), nullable=True)
    drm_config = Column(JSON, nullable=True)
    cdm_type = Column(String(50), nullable=True)
    use_cdm = Column(Boolean, default=True)
    cdm_mode = Column(String(50), default="external")
    
    # Video settings
    video = Column(String(50), default="best")
    on_demand = Column(Boolean, default=True)
    speed_up = Column(Boolean, default=True)
    streaming_format = Column(String(50), nullable=True)
    quality = Column(String(50), nullable=True)
    
    # Localization
    language = Column(String(10), default="de")
    country = Column(String(10), default="DE")
    
    # Promotional
    trailer_url = Column(String(1000), nullable=True)
    is_highlight = Column(Boolean, default=False)
    
    # Status
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_available = Column(Boolean, default=True)

    show = relationship("TVShow", back_populates="episodes")

    __table_args__ = (
        UniqueConstraint("show_id", "season_number", "episode_number", "provider", 
                        name="uq_episode_provider"),
        Index("idx_episodes_show_provider", "show_id", "provider"),
        Index("idx_episodes_season_episode", "show_id", "season_number", "episode_number"),
        Index("idx_episodes_available", "is_available"),
        Index("idx_episodes_provider", "provider"),
    )

    def __repr__(self):
        return f"<TVEpisode id={self.id!r} show={self.show_id!r} S{self.season_number:02d}E{self.episode_number:02d} provider={self.provider!r}>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "show_id": self.show_id,
            "provider": self.provider,
            "content_id": self.content_id,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
            "title": self.title,
            "series_title": self.series_title,
            "plot": self.plot,
            "long_description": self.long_description,
            "air_date": self.air_date.isoformat() if self.air_date else None,
            "duration_seconds": self.duration_seconds,
            "rating": self.rating,
            "genres": self.genres,
            "genre": self.genre,
            "cast": self.cast,
            "director": self.director,
            "mode": self.mode,
            "logo_url": self.logo_url,
            "manifest_url": self.manifest_url,
            "manifest_script": self.manifest_script,
            "session_manifest": self.session_manifest,
            "license_url": self.license_url,
            "certificate_url": self.certificate_url,
            "drm_config": self.drm_config,
            "cdm_type": self.cdm_type,
            "use_cdm": self.use_cdm,
            "cdm_mode": self.cdm_mode,
            "video": self.video,
            "on_demand": self.on_demand,
            "speed_up": self.speed_up,
            "streaming_format": self.streaming_format,
            "quality": self.quality,
            "language": self.language,
            "country": self.country,
            "trailer_url": self.trailer_url,
            "is_highlight": self.is_highlight,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "is_available": self.is_available,
        }


class Movie(Base):
    """Standalone movie (Maps to VodItem with content_type=MOVIE)"""
    __tablename__ = "movies"

    id = Column(String(512), primary_key=True)  # provider:content_id combination
    provider = Column(String(100), nullable=False, index=True)
    content_id = Column(String(255), nullable=False)
    
    # Metadata
    title = Column(String(500), nullable=False)  # Maps to backend 'name'
    original_title = Column(String(500), nullable=True)
    plot = Column(Text, nullable=True)  # Maps to backend 'description'
    long_description = Column(Text, nullable=True)
    release_year = Column(Integer, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    rating = Column(String(50), nullable=True)
    genres = Column(JSON, nullable=True)
    genre = Column(String(255), nullable=True)  # Primary genre
    cast = Column(JSON, nullable=True)
    director = Column(String(255), nullable=True)
    
    # External IDs
    imdb_id = Column(String(50), nullable=True)
    tmdb_id = Column(String(50), nullable=True)
    
    # Streaming Configuration (Matches backend Content/VodItem)
    mode = Column(String(20), default="vod")
    logo_url = Column(String(1000), nullable=True)
    manifest_url = Column(String(1000), nullable=True)
    manifest_script = Column(Text, nullable=True)  # For dynamic manifests
    session_manifest = Column(Boolean, default=False)
    
    # DRM / CDM
    license_url = Column(String(1000), nullable=True)
    certificate_url = Column(String(1000), nullable=True)
    drm_config = Column(JSON, nullable=True)
    cdm_type = Column(String(50), nullable=True)
    use_cdm = Column(Boolean, default=True)
    cdm_mode = Column(String(50), default="external")
    
    # Video settings
    video = Column(String(50), default="best")
    on_demand = Column(Boolean, default=True)
    speed_up = Column(Boolean, default=True)
    streaming_format = Column(String(50), nullable=True)
    quality = Column(String(50), nullable=True)
    
    # Localization
    language = Column(String(10), default="de")
    country = Column(String(10), default="DE")
    
    # Promotional
    trailer_url = Column(String(1000), nullable=True)
    
    # Classification
    is_highlight = Column(Boolean, default=False)
    is_sport = Column(Boolean, default=False)
    
    # Status
    first_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_available = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("provider", "content_id", name="uq_movie_provider_content"),
        Index("idx_movies_provider", "provider"),
        Index("idx_movies_release_year", "release_year"),
        Index("idx_movies_is_available", "is_available"),
        Index("idx_movies_is_sport", "is_sport"),
    )

    def __repr__(self):
        return f"<Movie id={self.id!r} title={self.title!r} provider={self.provider!r}>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "content_id": self.content_id,
            "title": self.title,
            "original_title": self.original_title,
            "plot": self.plot,
            "long_description": self.long_description,
            "release_year": self.release_year,
            "duration_seconds": self.duration_seconds,
            "rating": self.rating,
            "genres": self.genres,
            "genre": self.genre,
            "cast": self.cast,
            "director": self.director,
            "imdb_id": self.imdb_id,
            "tmdb_id": self.tmdb_id,
            "mode": self.mode,
            "logo_url": self.logo_url,
            "manifest_url": self.manifest_url,
            "manifest_script": self.manifest_script,
            "session_manifest": self.session_manifest,
            "license_url": self.license_url,
            "certificate_url": self.certificate_url,
            "drm_config": self.drm_config,
            "cdm_type": self.cdm_type,
            "use_cdm": self.use_cdm,
            "cdm_mode": self.cdm_mode,
            "video": self.video,
            "on_demand": self.on_demand,
            "speed_up": self.speed_up,
            "streaming_format": self.streaming_format,
            "quality": self.quality,
            "language": self.language,
            "country": self.country,
            "trailer_url": self.trailer_url,
            "is_highlight": self.is_highlight,
            "is_sport": self.is_sport,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "is_available": self.is_available,
        }


class CrawlHistory(Base):
    """Track crawl history for each provider"""
    __tablename__ = "crawl_history"

    id = Column(Integer, primary_key=True)
    provider = Column(String(100), nullable=False, index=True)
    crawl_start = Column(DateTime, nullable=False)
    crawl_end = Column(DateTime, nullable=True)
    
    items_found = Column(Integer, default=0)
    items_added = Column(Integer, default=0)
    items_removed = Column(Integer, default=0)
    items_updated = Column(Integer, default=0)
    
    status = Column(String(50), default="running")
    error_message = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)

    __table_args__ = (
        Index("idx_crawl_history_provider_date", "provider", "crawl_start"),
        Index("idx_crawl_history_status", "status"),
    )

    def __repr__(self):
        return f"<CrawlHistory id={self.id} provider={self.provider!r} status={self.status!r}>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "crawl_start": self.crawl_start.isoformat() if self.crawl_start else None,
            "crawl_end": self.crawl_end.isoformat() if self.crawl_end else None,
            "items_found": self.items_found,
            "items_added": self.items_added,
            "items_removed": self.items_removed,
            "items_updated": self.items_updated,
            "status": self.status,
            "error_message": self.error_message,
            "details": self.details,
        }


class VodCache(Base):
    """Cache for raw VOD data (for debugging and change detection)"""
    __tablename__ = "vod_cache"

    id = Column(Integer, primary_key=True)
    provider = Column(String(100), nullable=False, index=True)
    content_id = Column(String(255), nullable=False)
    content_type = Column(String(50), nullable=False)
    
    raw_data = Column(JSON, nullable=False)
    hash = Column(String(64), nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("provider", "content_id", "content_type", 
                        name="uq_vod_cache_provider_content_type"),
        Index("idx_vod_cache_provider_type", "provider", "content_type"),
        Index("idx_vod_cache_hash", "hash"),
    )

    def __repr__(self):
        return f"<VodCache provider={self.provider!r} content_id={self.content_id!r} type={self.content_type!r}>"
