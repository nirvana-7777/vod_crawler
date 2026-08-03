#!/usr/bin/env python3
"""
VOD Crawler - Main entry point with web API
"""

import sys
import argparse
import threading
import signal
import time
from pathlib import Path

import uvicorn

from .config import Config
from .api import create_app
from .database import DatabaseManager
from .crawler import ProviderCrawler
from .utils.logger import configure_logging, logger


def run_crawl(config: Config, db: DatabaseManager, provider: str = None):
    """Run the VOD crawler with shared database"""
    logger.info("Starting VOD Crawler")

    # Determine providers to crawl
    if provider:
        providers = [provider]
    else:
        providers = config.provider_priority

    logger.info(f"Providers to crawl: {providers}")

    # Crawl each provider
    results = []
    for p in providers:
        logger.info(f"=== Crawling {p} ===")
        crawler = ProviderCrawler(config, db)
        result = crawler.crawl_provider(p)
        results.append(result)

    # Print summary
    logger.info("=== Crawl Summary ===")
    for result in results:
        if result.get("success"):
            traversal = result.get("traversal_stats", {})
            db_stats = result.get("db_stats", {})
            logger.info(
                f"{result['provider']}: "
                f"{traversal.get('total_items', 0)} items, "
                f"{db_stats.get('movies_added', 0)} movies added, "
                f"{db_stats.get('episodes_added', 0)} episodes added"
            )
        else:
            logger.error(f"{result['provider']}: FAILED - {result.get('error')}")

    logger.info("Crawl complete!")
    return results


def run_web_api(config: Config, db: DatabaseManager):
    """Run the web API server with shared database"""
    app = create_app(config, db)

    # Get port from config or use default
    port = getattr(config, 'api_port', 7888)
    host = getattr(config, 'api_host', '0.0.0.0')

    logger.info(f"Starting web API on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=config.logging.level.lower(),
        access_log=False,
    )


def run_scheduler(config: Config, db: DatabaseManager):
    """
    Run the crawler on a schedule

    CRITICAL FIX: This function now blocks indefinitely so the container stays alive.
    BackgroundScheduler.start() is non-blocking, so we need to wait.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler()

    # Parse cron schedule
    schedule = config.crawler.schedule
    try:
        trigger = CronTrigger.from_crontab(schedule)
        logger.info(f"Using schedule: {schedule}")
    except ValueError as e:
        logger.error(f"Invalid cron schedule '{schedule}': {e}")
        logger.info("Falling back to default: 3 AM daily")
        trigger = CronTrigger(hour=3, minute=0)

    # Schedule the job - uses shared db
    def crawl_job():
        logger.info("Scheduled crawl starting...")
        try:
            run_crawl(config, db)
        except Exception as e:
            logger.error(f"Scheduled crawl failed: {e}", exc_info=True)

    scheduler.add_job(
        crawl_job,
        trigger=trigger,
        id="vod_crawler",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started")

    # CRITICAL FIX: Block until shutdown signal is received
    # This prevents the container from exiting immediately
    stop = {"flag": False}

    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        scheduler.shutdown()
        stop["flag"] = True

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while not stop["flag"]:
        time.sleep(60)

    logger.info("Scheduler stopped")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Ultimate VOD Crawler")
    parser.add_argument(
        "--config", "-c",
        default=str(Path(__file__).parent.parent / "config.yaml"),
        help="Path to config file"
    )
    parser.add_argument(
        "--provider", "-p",
        help="Crawl only this provider (optional)"
    )
    parser.add_argument(
        "--crawl", "-C",
        action="store_true",
        help="Run crawl once and exit"
    )
    parser.add_argument(
        "--api", "-A",
        action="store_true",
        help="Run web API server"
    )
    parser.add_argument(
        "--scheduler", "-S",
        action="store_true",
        help="Run scheduler (crawl + API)"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="API port (default: 7888)"
    )

    args = parser.parse_args()

    # Load config
    config = Config.from_file(args.config)

    # Override port if provided
    if args.port:
        config.api_port = args.port

    # Configure logging FIRST
    configure_logging(config)

    # Initialize database - SHARED instance
    db_path = Path(config.library.path) / ".metadata" / "vod_cache.db"
    db = DatabaseManager(str(db_path), config=config)
    logger.info(f"Database initialized at {db_path}")

    # Determine mode
    if args.crawl or args.provider:
        # Run crawl once
        run_crawl(config, db, args.provider)

    elif args.api:
        # Run web API only
        run_web_api(config, db)

    elif args.scheduler:
        # Run scheduler + API
        logger.info("Starting scheduler + API mode")

        # Start API in a separate thread with shared db
        api_thread = threading.Thread(
            target=run_web_api,
            args=(config, db),
            daemon=True,
            name="WebAPI"
        )
        api_thread.start()

        # Start scheduler (this now BLOCKS)
        run_scheduler(config, db)

    else:
        # Default: run everything
        logger.info("Starting full service mode (scheduler + API)")

        # Start API in a separate thread with shared db
        api_thread = threading.Thread(
            target=run_web_api,
            args=(config, db),
            daemon=True,
            name="WebAPI"
        )
        api_thread.start()

        # Run initial crawl
        run_crawl(config, db)

        # Start scheduler (this now BLOCKS)
        run_scheduler(config, db)


if __name__ == "__main__":
    main()