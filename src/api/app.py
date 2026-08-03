#!/usr/bin/env python3
"""
FastAPI application setup
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routes import router
from .dependencies import init_dependencies
from ..config import Config
from ..database import DatabaseManager
from ..utils.logger import get_logger

logger = get_logger(__name__)


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

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    logger.info("FastAPI application created with shared database connection")
    return app