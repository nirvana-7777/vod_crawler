#!/usr/bin/env python3
"""
Base crawler with HTTP client for backend API
"""

import time
import requests
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin, urlencode

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class BaseCrawler:
    """Base crawler with API client"""

    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.backend.url.rstrip("/")
        self.timeout = config.backend.timeout
        self.session = requests.Session()
        
        # Retry configuration
        self.max_retries = 3
        self.retry_delay = 2  # seconds

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        retries: int = 3,
        is_full_url: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Make an HTTP request to the backend API with retries
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path or full URL if is_full_url=True
            params: Query parameters
            retries: Number of retries on failure
            is_full_url: If True, path is treated as a full URL
        
        Returns:
            JSON response dict or None on failure
        """
        # Build URL
        if is_full_url:
            url = path
        else:
            url = urljoin(self.base_url, path)
        
        for attempt in range(retries):
            try:
                logger.debug(f"Requesting {method} {url} (attempt {attempt + 1})")
                
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=self.timeout,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "UltimateVODCrawler/1.0"
                    }
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    # Not found - not worth retrying
                    logger.warning(f"404 Not Found: {url}")
                    return None
                elif response.status_code >= 500:
                    # Server error - retry
                    logger.warning(
                        f"Server error {response.status_code} on {url}, retrying..."
                    )
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                else:
                    logger.error(
                        f"Unexpected status {response.status_code} on {url}: "
                        f"{response.text[:200]}"
                    )
                    return None
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on {url}, retrying...")
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            except requests.exceptions.ConnectionError:
                logger.warning(f"Connection error on {url}, retrying...")
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            except Exception as e:
                logger.error(f"Request error on {url}: {e}")
                if attempt == retries - 1:
                    return None
                time.sleep(self.retry_delay * (attempt + 1))
                continue
        
        return None

    def get_vod_root(self, provider: str) -> Optional[Dict[str, Any]]:
        """
        Get the root VOD node for a provider
        
        Returns:
            API response with entries, next_cursor, total
        """
        path = f"/api/providers/{provider}/vod"
        return self._request("GET", path)

    def get_vod_node(self, provider: str, content_id: str, cursor: str = None, size: int = 50) -> Optional[Dict[str, Any]]:
        """
        Get a specific VOD node (category or item listing)
        
        Args:
            provider: Provider name
            content_id: VOD node content_id
            cursor: Pagination cursor
            size: Page size
        
        Returns:
            API response with entries, next_cursor, total
        """
        # Build path with content_id (may contain slashes)
        path = f"/api/providers/{provider}/vod/{content_id}"
        params = {}
        if cursor:
            params["cursor"] = cursor
        if size:
            params["size"] = size
        
        return self._request("GET", path, params=params)

    def get_vod_node_by_url(self, url: str, cursor: str = None, size: int = 50) -> Optional[Dict[str, Any]]:
        """
        Get a VOD node using a full URL (for fetch_url)
        
        Args:
            url: Full URL (including query params)
            cursor: Pagination cursor
            size: Page size
        
        Returns:
            API response with entries, next_cursor, total
        """
        params = {}
        if cursor:
            params["cursor"] = cursor
        if size:
            params["size"] = size
        
        return self._request("GET", url, params=params, is_full_url=True)

    def get_epg_id_mapping(self, provider: str) -> Dict[str, str]:
        """
        Get EPG ID mapping for a provider from the backend
        Uses the backend's EPG alias system
        """
        # The backend has EPG mapping via /api/epg/aliases
        # But this is provider-specific. We'll implement this in the
        # provider crawler if needed.
        return {}
