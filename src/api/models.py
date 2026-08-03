#!/usr/bin/env python3
"""
Pydantic models for API responses
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class ShowSummary(BaseModel):
    """TV show summary (without episodes)"""
    id: str
    title: str
    normalized_title: str
    plot: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    genres: Optional[List[str]] = None
    genre: Optional[str] = None
    release_year: Optional[int] = None
    provider_ids: Dict[str, str] = Field(default_factory=dict)
    is_available: bool = True


class MovieExport(BaseModel):
    """Movie item for export"""
    id: str
    provider: str
    content_id: str
    title: str
    original_title: Optional[str] = None
    plot: Optional[str] = None
    long_description: Optional[str] = None
    release_year: Optional[int] = None
    duration_seconds: Optional[int] = None
    rating: Optional[str] = None
    genres: Optional[List[str]] = None
    genre: Optional[str] = None
    cast: Optional[List[str]] = None
    director: Optional[str] = None
    logo_url: Optional[str] = None
    stream_url: str  # The plugin:// URL
    manifest_url: Optional[str] = None
    is_highlight: bool = False
    is_sport: bool = False
    hash: str  # For change detection
    last_seen: datetime


class EpisodeExport(BaseModel):
    """Episode item for export"""
    id: str
    show_id: str
    show_title: str
    provider: str
    content_id: str
    season_number: int
    episode_number: int
    title: Optional[str] = None
    series_title: Optional[str] = None
    plot: Optional[str] = None
    long_description: Optional[str] = None
    air_date: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    rating: Optional[str] = None
    genres: Optional[List[str]] = None
    genre: Optional[str] = None
    cast: Optional[List[str]] = None
    director: Optional[str] = None
    logo_url: Optional[str] = None
    stream_url: str  # The plugin:// URL
    manifest_url: Optional[str] = None
    is_highlight: bool = False
    hash: str  # For change detection
    last_seen: datetime


class DeletedItem(BaseModel):
    """Deleted item for incremental updates"""
    id: str
    type: str  # "movie" or "episode"
    provider: str
    content_id: Optional[str] = None


class LibraryExportResponse(BaseModel):
    """Complete library export response"""
    version: str = "1.0"
    timestamp: datetime
    type: str  # "full" or "incremental"
    
    # Data
    shows: List[ShowSummary] = Field(default_factory=list)
    movies: List[MovieExport] = Field(default_factory=list)
    episodes: List[EpisodeExport] = Field(default_factory=list)
    
    # For incremental updates
    deleted: List[DeletedItem] = Field(default_factory=list)
    
    # Statistics
    stats: Dict[str, Any] = Field(default_factory=dict)


class SyncRequest(BaseModel):
    """Request body for /api/sync"""
    provider: Optional[str] = None
    force: bool = False


class SyncResponse(BaseModel):
    """Response for /api/sync"""
    success: bool
    providers_synced: List[str]
    movies_added: int = 0
    movies_updated: int = 0
    movies_deleted: int = 0
    episodes_added: int = 0
    episodes_updated: int = 0
    episodes_deleted: int = 0
    duration_seconds: float = 0.0
    message: Optional[str] = None


class StatusResponse(BaseModel):
    """Status response"""
    last_crawl: Optional[datetime] = None
    status: str  # "idle", "running", "error"
    providers: List[str] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)
    version: str = "1.0"
    sync_in_progress: bool = False  # NEW: Indicates if a sync is currently running
    sync_progress: Optional[Dict[str, Any]] = None  # NEW: Per-provider progress
