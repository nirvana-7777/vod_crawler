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
            on_item: Callback(entry, result, parent_content_id) for each
                playable item encountered. parent_content_id is the
                content_id of the category the item was fetched under --
                needed because some providers (e.g. RTL+) never include a
                usable series/program id on the episode entry itself.

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

        # BFS queue: (entry, depth, parent_content_id)
        queue = deque()

        # Track category content_ids we've already fetched children for.
        # Some providers' catalogs are a graph, not a tree -- the same
        # series/season/lane can be reachable from multiple parent lanes
        # (e.g. Magenta2's overlapping recommendation lanes). Without this,
        # the same node gets fetched and its items fully reprocessed once
        # per path that leads to it, which both wastes requests and can
        # cause duplicate-insert failures downstream (e.g. show_providers
        # unique constraint on (provider, provider_id)).
        visited_category_ids = set()
        visited_item_ids = set()

        # Start with root
        root_response = self.crawler.get_vod_root(provider)
        if not root_response:
            logger.error(f"Failed to get VOD root for {provider}")
            return stats

        # Process root entries at depth 0 (no parent category)
        entries = root_response.get("entries", [])
        for entry in entries:
            queue.append((entry, 0, None))

        # Process queue
        processed = 0
        while queue and processed < 50000:  # Safety limit
            entry, depth, parent_content_id = queue.popleft()
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

                if content_id in visited_category_ids:
                    logger.debug(f"Skipping already-visited category: {content_id}")
                    continue
                visited_category_ids.add(content_id)

                # IMPORTANT: fetch_url is only safe to call directly when it
                # points back at OUR OWN backend. For some providers (e.g.
                # Magenta2) the backend surfaces the raw upstream provider
                # URL here (tvhubs.t-online.de) purely as debug metadata --
                # that's a different host with a completely different JSON
                # shape, so calling it directly returns either a 404 (no
                # auth/session) or a 200 with no "entries" key at all,
                # which silently yields zero children with no error logged.
                # Only use fetch_url when it's rooted at our own backend;
                # otherwise always fall back to the content_id-based call.
                fetch_url = entry.get("fetch_url")
                if fetch_url and not fetch_url.startswith(self.crawler.base_url):
                    logger.debug(
                        f"Ignoring non-backend fetch_url for {entry.get('name', content_id)}: "
                        f"{fetch_url!r} (using content_id instead)"
                    )
                    fetch_url = None

                # Some categories are app deep-links (e.g. "app") rather than
                # real URLs -- e.g. Magenta2's JOYN/Paramount+ tiles. Skip
                # these instead of retrying an invalid URL 3x with backoff.
                if fetch_url and not fetch_url.startswith(("http://", "https://")):
                    logger.debug(
                        f"Skipping non-HTTP category fetch_url for "
                        f"{entry.get('name', content_id)}: {fetch_url!r}"
                    )
                    continue

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

                    # Add children to queue with increased depth, tagging
                    # them with this category's content_id as their parent
                    for child in child_entries:
                        queue.append((child, depth + 1, content_id))

                    # Check for next page
                    cursor = response.get("next_cursor")
                    if not cursor:
                        break

                    logger.debug(f"Fetching next page for {content_id}: cursor={cursor}")

            elif result.content_type == ContentType.MOVIE:
                # Process movie
                item_id = entry.get("id") or entry.get("Id")
                if item_id and item_id in visited_item_ids:
                    continue
                if item_id:
                    visited_item_ids.add(item_id)
                stats["total_items"] += 1
                stats["movies"] += 1
                if on_item:
                    on_item(entry, result, parent_content_id)

            elif result.content_type == ContentType.TV_EPISODE:
                # Process TV episode
                item_id = entry.get("id") or entry.get("Id")
                if item_id and item_id in visited_item_ids:
                    continue
                if item_id:
                    visited_item_ids.add(item_id)
                stats["total_items"] += 1
                stats["episodes"] += 1
                if on_item:
                    on_item(entry, result, parent_content_id)

            elif result.content_type == ContentType.UNKNOWN:
                stats["items_skipped"] += 1
                continue

        # Check if we hit the safety limit
        if processed >= 50000:
            logger.warning(f"Hit safety limit of 50000 items for {provider}")

        logger.info(f"Traversal complete for {provider}: {stats}")
        return stats