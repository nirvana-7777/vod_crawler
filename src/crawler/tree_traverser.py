#!/usr/bin/env python3
"""
Recursive VOD tree traverser
"""

from typing import Dict, Any, List, Optional, Callable
from collections import deque

from .base import BaseCrawler
from .content_classifier import ContentClassifier, ContentType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class TreeTraverser:
    """
    Recursively traverse the VOD tree from the root
    Uses BFS to avoid deep recursion issues
    """

    def __init__(
        self,
        crawler: BaseCrawler,
        classifier: ContentClassifier,
        max_depth: int = 10,
        page_size: int = 50
    ):
        self.crawler = crawler
        self.classifier = classifier
        self.max_depth = max_depth
        self.page_size = page_size

    def traverse(
        self,
        provider: str,
        on_category: Optional[Callable] = None,
        on_item: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Traverse the entire VOD tree for a provider
        
        Args:
            provider: Provider name
            on_category: Callback for each category encountered
            on_item: Callback for each playable item encountered
        
        Returns:
            Statistics dict
        """
        stats = {
            "total_categories": 0,
            "total_items": 0,
            "items_skipped": 0,
            "movies": 0,
            "episodes": 0,
            "errors": 0,
            "depth_reached": 0,
        }
        
        logger.info(f"Starting VOD traversal for {provider}")
        
        # BFS queue: (content_id, depth, cursor, fetch_url)
        queue = deque()
        
        # Start with root
        root_response = self.crawler.get_vod_root(provider)
        if not root_response:
            logger.error(f"Failed to get VOD root for {provider}")
            return stats
        
        # Process root entries at depth 0
        entries = root_response.get("entries", [])
        for entry in entries:
            queue.append((entry, 0))
        
        # Process queue
        processed = 0
        while queue and processed < 50000:  # Safety limit
            entry, depth = queue.popleft()
            processed += 1
            
            stats["depth_reached"] = max(stats["depth_reached"], depth)
            
            # Check depth limit
            if depth >= self.max_depth:
                logger.debug(f"Reached max depth {self.max_depth} for {entry.get('name', 'unknown')}")
                continue
            
            # Classify entry
            result = self.classifier.classify(entry)
            if result is None:
                stats["items_skipped"] += 1
                continue
            
            if result.content_type == ContentType.CATEGORY:
                # Process category
                stats["total_categories"] += 1
                if on_category:
                    on_category(entry, depth)
                
                # Fetch category children
                content_id = entry.get("id")
                if not content_id:
                    logger.warning(f"Category has no ID: {entry.get('name', 'unknown')}")
                    continue
                
                # IMPORTANT: Use fetch_url if available (preserves query params)
                fetch_url = entry.get("fetch_url")
                
                # Fetch children with pagination
                cursor = None
                while True:
                    if fetch_url:
                        # Use the full URL with query params preserved
                        response = self.crawler.get_vod_node_by_url(
                            url=fetch_url,
                            cursor=cursor,
                            size=self.page_size
                        )
                    else:
                        # Fall back to content_id
                        response = self.crawler.get_vod_node(
                            provider=provider,
                            content_id=content_id,
                            cursor=cursor,
                            size=self.page_size
                        )
                    
                    if not response:
                        logger.warning(f"Failed to fetch children for {content_id}")
                        break
                    
                    child_entries = response.get("entries", [])
                    if not child_entries:
                        break
                    
                    # Add children to queue with increased depth
                    for child in child_entries:
                        queue.append((child, depth + 1))
                    
                    # Check for next page
                    cursor = response.get("next_cursor")
                    if not cursor:
                        break
                    
                    logger.debug(f"Fetching next page for {content_id}: cursor={cursor}")
                
            elif result.content_type == ContentType.MOVIE:
                # Process movie
                stats["total_items"] += 1
                stats["movies"] += 1
                if on_item:
                    on_item(entry, result)
                
            elif result.content_type == ContentType.TV_EPISODE:
                # Process TV episode
                stats["total_items"] += 1
                stats["episodes"] += 1
                if on_item:
                    on_item(entry, result)
            
            elif result.content_type == ContentType.UNKNOWN:
                stats["items_skipped"] += 1
                continue
        
        # Check if we hit the safety limit
        if processed >= 50000:
            logger.warning(f"Hit safety limit of 50000 items for {provider}")
        
        logger.info(f"Traversal complete for {provider}: {stats}")
        return stats
