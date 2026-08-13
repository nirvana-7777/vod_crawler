#!/usr/bin/env python3
"""
Dependency injection for FastAPI
"""

from typing import Optional
from fastapi import Depends, HTTPException, status

from ..config import Config
from ..database import DatabaseManager
from ..library.export_generator import ExportGenerator
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Global instances
_config: Optional[Config] = None
_db: Optional[DatabaseManager] = None
_export_generator: Optional[ExportGenerator] = None

# NOTE: Sync state is intentionally NOT tracked here. It used to live in a
# module-level dict (_sync_in_progress / _sync_progress), which is
# per-process state -- it silently breaks the overlap guard on
# POST /api/sync the moment the API runs with more than one worker, since
# two workers would each have their own copy and neither would see the
# other's in-progress sync. Sync state now lives in the database via
# DatabaseManager.get_sync_state() / set_sync_state() (see db_manager.py),
# which is visible to every worker. Call those directly instead of adding
# equivalents here.


def init_dependencies(config: Config, db: DatabaseManager):
    """
    Initialize global dependencies

    Args:
        config: Application configuration
        db: Database manager instance (shared)
    """
    global _config, _db, _export_generator

    _config = config
    _db = db

    # Initialize export generator with shared DB
    _export_generator = ExportGenerator(_db, _config)

    logger.info("Dependencies initialized with shared database connection")


def get_config() -> Config:
    """Get config dependency"""
    if _config is None:
        raise RuntimeError("Config not initialized")
    return _config


def get_db() -> DatabaseManager:
    """Get database manager dependency"""
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


def get_export_generator() -> ExportGenerator:
    """Get export generator dependency"""
    if _export_generator is None:
        raise RuntimeError("Export generator not initialized")
    return _export_generator