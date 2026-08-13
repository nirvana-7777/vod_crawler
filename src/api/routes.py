#!/usr/bin/env python3
"""
FastAPI routes for VOD crawler API
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse

from sqlalchemy.orm import joinedload

from .models import (
    LibraryExportResponse,
    SyncRequest,
    SyncResponse,
    StatusResponse,
    MovieExport,
    EpisodeExport,
    ShowSummary,
    PaginatedMovies,
    PaginatedEpisodes,
)
from .dependencies import get_db, get_export_generator, get_config
from ..database import DatabaseManager
from ..database.models import TVEpisode, FREE_ACCESS_TYPES
from ..library.export_generator import ExportGenerator
from ..crawler import ProviderCrawler
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/status", response_model=StatusResponse)
async def get_status(
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get crawler status"""
    status = export_generator.get_status()

    sync_state = db.get_sync_state()
    status["sync_in_progress"] = sync_state["sync_in_progress"]
    status["sync_progress"] = sync_state["sync_progress"]

    return status


@router.get("/library/export", response_model=LibraryExportResponse)
async def export_library(
    since: Optional[str] = Query(None, description="ISO 8601 timestamp for incremental updates"),
    providers: Optional[str] = Query(None, description="Comma-separated list of providers"),
    include_priced: bool = Query(False, description="Include priced (rent/buy/subscription) content; default only free/unknown-priced content"),
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
        include_priced=include_priced,
    )

    return data


@router.get("/library/export/stream")
async def export_library_stream(
    since: Optional[str] = Query(None, description="ISO 8601 timestamp for incremental updates"),
    providers: Optional[str] = Query(None, description="Comma-separated list of providers"),
    include_priced: bool = Query(False, description="Include priced (rent/buy/subscription) content; default only free/unknown-priced content"),
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
            include_priced=include_priced,
        )

    return StreamingResponse(
        generate_stream(),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=library_export.json",
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/library/movies", response_model=PaginatedMovies)
async def get_movies(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    include_priced: bool = Query(False, description="Include priced content; default only free/unknown-priced content"),
    limit: int = Query(100, ge=1, le=1000, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get movies, paginated at the SQL level."""
    with db.session() as session:
        from ..database.models import Movie

        query = session.query(Movie).filter(Movie.is_available == True)
        if provider:
            query = query.filter(Movie.provider == provider)
        if not include_priced:
            query = query.filter(
                (Movie.pricing_access_type.is_(None)) |
                (Movie.pricing_access_type.in_(FREE_ACCESS_TYPES))
            )

        total = query.count()
        movies = query.order_by(Movie.title).limit(limit).offset(offset).all()
        provider_priority = export_generator.config.provider_priority

        return PaginatedMovies(
            items=[export_generator._get_movie_export(m, provider_priority) for m in movies],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/library/shows", response_model=List[ShowSummary])
async def get_shows(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get all TV shows (legacy endpoint)"""
    from ..database.models import TVShow

    if provider:
        # get_shows_for_provider() returns dicts shaped like TVShow.to_dict()
        # (a superset of ShowSummary's fields -- extras like imdb_id are
        # simply dropped by the response_model). Return them directly
        # instead of routing through _get_show_summary(), which expects
        # ORM attribute access and would break on a plain dict.
        return db.get_shows_for_provider(provider)

    with db.session() as session:
        shows = session.query(TVShow).options(
            joinedload(TVShow.provider_mappings)
        ).filter(TVShow.is_available == True).all()
        return [export_generator._get_show_summary(s) for s in shows]


@router.get("/library/shows/{show_id}/episodes", response_model=PaginatedEpisodes)
async def get_show_episodes(
    show_id: str,
    provider: Optional[str] = Query(None, description="Filter by provider"),
    include_priced: bool = Query(False, description="Include priced content; default only free/unknown-priced content"),
    limit: int = Query(100, ge=1, le=1000, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: DatabaseManager = Depends(get_db),
    export_generator: ExportGenerator = Depends(get_export_generator),
):
    """Get episodes for a specific show, paginated at the SQL level."""
    with db.session() as session:
        query = session.query(TVEpisode).filter(
            TVEpisode.show_id == show_id,
            TVEpisode.is_available == True
        )
        if provider:
            query = query.filter(TVEpisode.provider == provider)
        if not include_priced:
            query = query.filter(
                (TVEpisode.pricing_access_type.is_(None)) |
                (TVEpisode.pricing_access_type.in_(FREE_ACCESS_TYPES))
            )

        total = query.count()
        episodes = query.order_by(
            TVEpisode.season_number, TVEpisode.episode_number
        ).offset(offset).limit(limit).all()

        provider_priority = export_generator.config.provider_priority

    show = db.get_show_by_id(show_id)
    show_title = show["title"] if show else "Unknown Show"

    return PaginatedEpisodes(
        items=[
            export_generator._get_episode_export(e, show_title, provider_priority)
            for e in episodes
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


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

    Sync state lives in the database (DatabaseManager.get_sync_state /
    set_sync_state), not an in-memory dict -- so the overlap guard below
    holds even if the API runs with multiple worker processes.

    This guard only covers syncs triggered via this endpoint; the
    cron-scheduled crawl in main.py does not currently check or set this
    state (see note in scheduler wiring).
    """
    sync_state = db.get_sync_state()
    if sync_state["sync_in_progress"]:
        current_progress = sync_state.get("sync_progress") or {}
        raise HTTPException(
            status_code=409,
            detail=f"Sync already in progress for provider(s): {current_progress.get('providers', [])}"
        )

    if request.provider:
        providers = [request.provider]
    else:
        providers = config.provider_priority

    logger.info(f"Manual sync triggered for providers: {providers}")

    db.set_sync_state(True, {"providers": providers, "current": None, "completed": []})
    job_id = db.create_sync_job(provider=request.provider)

    def run_sync_job():
        total_added = 0
        total_updated = 0
        total_found = 0
        try:
            db.update_sync_job(job_id, status="running")

            for idx, provider in enumerate(providers):
                logger.info(f"Background sync: starting {provider}")

                db.set_sync_state(True, {
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
                    db_stats = result.get("db_stats", {})
                    traversal_stats = result.get("traversal_stats", {})
                    total_added += db_stats.get("movies_added", 0) + db_stats.get("episodes_added", 0)
                    total_updated += db_stats.get("movies_updated", 0) + db_stats.get("episodes_updated", 0)
                    total_found += traversal_stats.get("total_items", 0)
                else:
                    logger.error(f"Background sync: {provider} failed: {result.get('error')}")

                # Persist incremental progress after each provider so
                # /api/sync callers (or anyone inspecting SyncJob rows)
                # can see live totals during a long multi-provider sync,
                # not just the final tally once everything finishes.
                db.update_sync_job(
                    job_id,
                    status="running",
                    items_found=total_found,
                    items_added=total_added,
                    items_updated=total_updated,
                    progress={
                        "current": provider,
                        "completed": idx + 1,
                        "total": len(providers),
                    },
                )

            db.set_sync_state(False, {
                "providers": providers,
                "completed": providers,
                "status": "complete"
            })
            db.update_sync_job(
                job_id,
                status="complete",
                items_found=total_found,
                items_added=total_added,
                items_updated=total_updated,
            )
            logger.info("Background sync completed for all providers")

        except Exception as e:
            logger.error(f"Background sync failed: {e}", exc_info=True)
            db.set_sync_state(False, {
                "providers": providers,
                "status": "failed",
                "error": str(e)
            })
            db.update_sync_job(job_id, status="failed", error=str(e))

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
    include_priced: bool = Query(False, description="Include priced content; default only free/unknown-priced content"),
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
        include_priced=include_priced,
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