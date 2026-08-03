#!/usr/bin/env python3
"""
Configuration management for VOD crawler
"""

import os
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class BackendConfig:
    """Backend API configuration"""
    url: str = "http://ultimate-backend:7777"
    timeout: int = 30


@dataclass
class LibraryConfig:
    """Library output configuration"""
    path: str = "/srv/vod-library"
    movies_dir: str = "Movies"
    tv_shows_dir: str = "TV Shows"
    sports_dir: str = "Sports"


@dataclass
class CrawlerConfig:
    """Crawler settings"""
    schedule: str = "0 3 * * *"  # 3 AM daily
    concurrency: int = 2
    page_size: int = 50
    max_depth: int = 10
    skip_highlights: bool = True


@dataclass
class MetadataConfig:
    """Metadata settings"""
    use_kodi_scrapers: bool = True
    embed_metadata: bool = False
    generate_nfo: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = "INFO"
    file: str = "/var/log/vod-crawler/crawler.log"


@dataclass
class Config:
    """Main configuration"""
    backend: BackendConfig = field(default_factory=BackendConfig)
    library: LibraryConfig = field(default_factory=LibraryConfig)
    provider_priority: List[str] = field(default_factory=lambda: ["magenta2", "rtlplus", "joyn", "discovery_de"])
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_file(cls, config_path: str) -> "Config":
        """Load configuration from YAML file"""
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}

        config = cls()

        if "backend" in data:
            backend = data["backend"]
            config.backend.url = backend.get("url", config.backend.url)
            config.backend.timeout = backend.get("timeout", config.backend.timeout)

        if "library" in data:
            library = data["library"]
            config.library.path = library.get("path", config.library.path)
            config.library.movies_dir = library.get("movies_dir", config.library.movies_dir)
            config.library.tv_shows_dir = library.get("tv_shows_dir", config.library.tv_shows_dir)
            config.library.sports_dir = library.get("sports_dir", config.library.sports_dir)

        if "provider_priority" in data:
            config.provider_priority = data["provider_priority"]

        if "crawler" in data:
            crawler = data["crawler"]
            config.crawler.schedule = crawler.get("schedule", config.crawler.schedule)
            config.crawler.concurrency = crawler.get("concurrency", config.crawler.concurrency)
            config.crawler.page_size = crawler.get("page_size", config.crawler.page_size)
            config.crawler.max_depth = crawler.get("max_depth", config.crawler.max_depth)
            config.crawler.skip_highlights = crawler.get("skip_highlights", config.crawler.skip_highlights)

        if "metadata" in data:
            metadata = data["metadata"]
            config.metadata.use_kodi_scrapers = metadata.get("use_kodi_scrapers", config.metadata.use_kodi_scrapers)
            config.metadata.embed_metadata = metadata.get("embed_metadata", config.metadata.embed_metadata)
            config.metadata.generate_nfo = metadata.get("generate_nfo", config.metadata.generate_nfo)

        if "logging" in data:
            logging = data["logging"]
            config.logging.level = logging.get("level", config.logging.level)
            config.logging.file = logging.get("file", config.logging.file)

        return config

    def get_provider_priority(self, provider: str) -> int:
        """Get priority index for a provider (lower = higher priority)"""
        try:
            return self.provider_priority.index(provider)
        except ValueError:
            return len(self.provider_priority)

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
