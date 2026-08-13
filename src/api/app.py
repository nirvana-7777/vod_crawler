#!/usr/bin/env python3
"""
FastAPI application setup
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .routes import router
from .dependencies import init_dependencies, get_db
from ..config import Config
from ..database import DatabaseManager
from ..database.models import CrawlHistory
from ..utils.logger import get_logger
from ..utils.time import utcnow

logger = get_logger(__name__)

# A crawl older than this is considered stale for /health purposes. This is
# deliberately generous -- the nightly/interval schedule is provider-driven
# and can legitimately be many hours; this just catches "the scheduler died".
_STALE_CRAWL_HOURS = 48


def create_app(config: Config, db: DatabaseManager) -> FastAPI:
    """
    Create the FastAPI application

    Args:
        config: Application configuration
        db: Database manager instance (shared)

    Returns:
        Configured FastAPI app
    """
    # Initialize dependencies with shared DB
    init_dependencies(config, db)

    # Create app
    app = FastAPI(
        title="Ultimate VOD Crawler API",
        description="API for VOD library management",
        version="1.0.0",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict this
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routes
    app.include_router(router)

    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "service": "Ultimate VOD Crawler",
            "version": "1.0.0",
            "endpoints": {
                "status": "/api/status",
                "export": "/api/library/export",
                "export_stream": "/api/library/export/stream",
                "changes": "/api/changes",
                "sync": "/api/sync (POST)",
                "movies": "/api/library/movies",
                "shows": "/api/library/shows",
                "show_episodes": "/api/library/shows/{show_id}/episodes",
            }
        }

    # Health check -- verifies DB connectivity and that a crawl has
    # completed successfully recently, not just that the process is up.
    @app.get("/health")
    async def health(db: DatabaseManager = Depends(get_db)):
        try:
            with db.session() as session:
                session.execute(text("SELECT 1"))

                last_crawl = session.query(CrawlHistory).filter_by(
                    status="success"
                ).order_by(CrawlHistory.crawl_start.desc()).first()

                hours_since_crawl = None
                last_crawl_iso = None
                if last_crawl:
                    hours_since_crawl = (utcnow() - last_crawl.crawl_start).total_seconds() / 3600
                    last_crawl_iso = last_crawl.crawl_start.isoformat()

            if hours_since_crawl is None or hours_since_crawl > _STALE_CRAWL_HOURS:
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "degraded",
                        "database": "connected",
                        "message": f"No successful crawl in the last {_STALE_CRAWL_HOURS} hours",
                        "last_crawl": last_crawl_iso,
                    }
                )

            return {
                "status": "healthy",
                "database": "connected",
                "last_crawl": last_crawl_iso,
                "hours_since_crawl": round(hours_since_crawl, 2),
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "error": str(e)}
            )

    logger.info("FastAPI application created with shared database connection")
    return app