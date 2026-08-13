#!/usr/bin/env python3
"""
Pydantic models for VOD crawler API
"""

from typing import Optional, List, Any, Dict
from pydantic import BaseModel


class MovieExport(BaseModel):
    """Movie export model"""
    class Config:
        extra = "allow"


class EpisodeExport(BaseModel):
    """Episode export model"""
    class Config:
        extra = "allow"


class ShowSummary(BaseModel):
    """Show summary model"""
    class Config:
        extra = "allow"


class PaginatedMovies(BaseModel):
    """Paginated movie response"""
    items: List[MovieExport]
    total: int
    limit: int
    offset: int


class PaginatedEpisodes(BaseModel):
    """Paginated episode response"""
    items: List[EpisodeExport]
    total: int
    limit: int
    offset: int


class LibraryExportResponse(BaseModel):
    """Library export response model"""
    class Config:
        extra = "allow"


class SyncRequest(BaseModel):
    """Sync request model"""
    provider: Optional[str] = None


class SyncResponse(BaseModel):
    """Sync response model"""
    success: bool
    providers_synced: List[str]
    movies_added: int = 0
    movies_updated: int = 0
    movies_deleted: int = 0
    episodes_added: int = 0
    episodes_updated: int = 0
    episodes_deleted: int = 0
    duration_seconds: float = 0.0
    message: str = ""


class StatusResponse(BaseModel):
    """Status response model"""
    class Config:
        extra = "allow"