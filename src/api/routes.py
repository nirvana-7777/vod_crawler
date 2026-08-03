#!/usr/bin/env python3
"""
FastAPI routes for VOD crawler API
"""

from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

from sqlalchemy.orm import joinedload

from .models import (
    LibraryExportResponse,
    SyncRequest,
    SyncResponse,
    StatusResponse,
    MovieExport,
    EpisodeExport,
    ShowSummary,
)
from .dependencies import get_db, get_export_generator, get_config, get_sync_state, set_sync_state
from ..database import DatabaseManager
from ..library.export_generator import ExportGenerator
from ..crawler import ProviderCrawler
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/status", response_model=StatusResponse)
async def get_status(
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get crawler status"""
    status = export_generator.get_status()

    sync_state = get_sync_state()
    status["sync_in_progress"] = sync_state["sync_in_progress"]
    status["sync_progress"] = sync_state["sync_progress"]

    return status


@router.get("/library/export", response_model=LibraryExportResponse)
async def export_library(
    since: Optional[str] = Query(None, description="ISO 8601 timestamp for incremental updates"),
    providers: Optional[str] = Query(None, description="Comma-separated list of providers"),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Export the complete library"""
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid since timestamp format")

    provider_list = None
    if providers:
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]

    data = export_generator.generate_export(
        since=since_dt,
        providers=provider_list,
    )

    return data


@router.get("/library/export/stream")
async def export_library_stream(
    since: Optional[str] = Query(None, description="ISO 8601 timestamp for incremental updates"),
    providers: Optional[str] = Query(None, description="Comma-separated list of providers"),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """
    Export the library as a JSON stream (for large exports).

    generate_export_stream() yields fragments of ONE already-well-formed JSON
    document (correct commas/brackets included). This route must pass those
    fragments through unmodified -- do NOT insert additional separators here,
    or the output becomes invalid JSON.
    """
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid since timestamp format")

    provider_list = None
    if providers:
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]

    def generate_stream():
        yield from export_generator.generate_export_stream(
            since=since_dt,
            providers=provider_list,
        )

    return StreamingResponse(
        generate_stream(),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=library_export.json",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/library/movies", response_model=List[MovieExport])
async def get_movies(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get all movies (legacy endpoint)"""
    with db.session() as session:
        from ..database.models import Movie

        query = session.query(Movie).filter(Movie.is_available == True)
        if provider:
            query = query.filter(Movie.provider == provider)

        movies = query.all()
        provider_priority = export_generator.config.provider_priority

        return [export_generator._get_movie_export(m, provider_priority) for m in movies]


@router.get("/library/shows", response_model=List[ShowSummary])
async def get_shows(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get all TV shows (legacy endpoint)"""
    from ..database.models import TVShow

    if provider:
        shows = db.get_shows_for_provider(provider)
    else:
        with db.session() as session:
            shows = session.query(TVShow).options(
                joinedload(TVShow.provider_mappings)
            ).filter(TVShow.is_available == True).all()

    return [export_generator._get_show_summary(s) for s in shows]


@router.get("/library/shows/{show_id}/episodes", response_model=List[EpisodeExport])
async def get_show_episodes(
    show_id: str,
    provider: Optional[str] = Query(None, description="Filter by provider"),
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get episodes for a specific show (legacy endpoint)"""
    episodes = db.get_episodes_for_show(show_id, provider=provider)
    provider_priority = export_generator.config.provider_priority

    show = db.get_show_by_id(show_id)
    show_title = show.title if show else "Unknown Show"

    return [
        export_generator._get_episode_export(e, show_title, provider_priority)
        for e in episodes
    ]


@router.post("/sync", response_model=SyncResponse)
async def trigger_sync(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    config: Config = Depends(get_config),
    db: DatabaseManager = Depends(get_db),
):
    """
    Trigger a manual sync (crawl + library generation).

    Runs in the background. Check /api/status for progress.
    Guarded against overlapping syncs -- note this guard only covers syncs
    triggered via this endpoint; the cron-scheduled crawl in main.py does not
    currently check or set this state (see note in scheduler wiring).
    """
    sync_state = get_sync_state()
    if sync_state["sync_in_progress"]:
        raise HTTPException(
            status_code=409,
            detail=f"Sync already in progress for provider(s): {sync_state.get('sync_progress', {}).get('providers', [])}"
        )

    if request.provider:
        providers = [request.provider]
    else:
        providers = config.provider_priority

    logger.info(f"Manual sync triggered for providers: {providers}")

    set_sync_state(True, {"providers": providers, "current": None, "completed": []})

    def run_sync_job():
        try:
            for idx, provider in enumerate(providers):
                logger.info(f"Background sync: starting {provider}")

                set_sync_state(True, {
                    "providers": providers,
                    "current": provider,
                    "completed": providers[:idx],
                    "remaining": providers[idx + 1:],
                    "total": len(providers)
                })

                crawler = ProviderCrawler(config, db)
                result = crawler.crawl_provider(provider)

                if result.get("success"):
                    logger.info(f"Background sync: {provider} completed successfully")
                else:
                    logger.error(f"Background sync: {provider} failed: {result.get('error')}")

            set_sync_state(False, {
                "providers": providers,
                "completed": providers,
                "status": "complete"
            })
            logger.info("Background sync completed for all providers")

        except Exception as e:
            logger.error(f"Background sync failed: {e}", exc_info=True)
            set_sync_state(False, {
                "providers": providers,
                "status": "failed",
                "error": str(e)
            })

    background_tasks.add_task(run_sync_job)

    return SyncResponse(
        success=True,
        providers_synced=providers,
        movies_added=0,
        movies_updated=0,
        movies_deleted=0,
        episodes_added=0,
        episodes_updated=0,
        episodes_deleted=0,
        duration_seconds=0.0,
        message=f"Sync started in background for {len(providers)} provider(s). Check /api/status for progress.",
    )


@router.get("/changes")
async def get_changes(
    since: str = Query(..., description="ISO 8601 timestamp"),
    providers: Optional[str] = Query(None, description="Comma-separated list of providers"),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get changes since a given timestamp (convenience endpoint)"""
    try:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid since timestamp format")

    provider_list = None
    if providers:
        provider_list = [p.strip() for p in providers.split(",") if p.strip()]

    data = export_generator.generate_export(
        since=since_dt,
        providers=provider_list,
    )

    return {
        "since": since,
        "timestamp": data["timestamp"],
        "added": {
            "movies": data["movies"],
            "episodes": data["episodes"],
        },
        "deleted": data["deleted"],
        "stats": data["stats"],
    }