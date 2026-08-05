#!/usr/bin/env python3
"""
Test database operations using pytest
"""
import os
import sys
import tempfile
from pathlib import Path

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.database.db_manager import DatabaseManager
from src.config import Config


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Config()
        db_path = Path(tmpdir) / "test.db"
        db_manager = DatabaseManager(str(db_path), config=config)
        yield db_manager


def test_tv_show_operations(db):
    show, created = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men",
        provider="joyn",
        provider_id="GN_SERIES_184925",
        plot="Two and a Half Men is an American television sitcom...",
        release_year=2003,
        genres=["Comedy", "Sitcom"]
    )

    assert created is True
    assert show.title == "Two and a Half Men"
    assert show.normalized_title == "two_and_a_half_men"
    assert show.release_year == 2003
    assert show.genres == ["Comedy", "Sitcom"]

    # Get provider mappings using the relationship
    assert len(show.provider_mappings) == 1
    assert show.provider_mappings[0].provider == "joyn"
    assert show.provider_mappings[0].provider_id == "GN_SERIES_184925"

    # Or use the to_dict() method to get provider_ids
    show_dict = show.to_dict()
    assert "joyn" in show_dict["provider_ids"]
    assert show_dict["provider_ids"]["joyn"] == "GN_SERIES_184925"

    # Add another provider mapping
    show2, created2 = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men (US)",
        provider="rtlplus",
        provider_id="12345",
        plot="Updated plot..."
    )

    assert created2 is False
    # Refresh show2 to get the updated mappings
    show_dict2 = show2.to_dict()
    assert show_dict2["provider_ids"].get("joyn") == "GN_SERIES_184925"
    assert show_dict2["provider_ids"].get("rtlplus") == "12345"

    found = db.get_show_by_id(show.id)
    assert found is not None
    assert found.title == "Two and a Half Men (US)"


def test_episode_operations(db):
    show, _ = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men",
        provider="joyn",
        provider_id="GN_SERIES_184925"
    )

    ep1, created1 = db.add_or_update_episode(
        show_id=show.id,
        provider="joyn",
        content_id="12345",
        season_number=1,
        episode_number=1,
        title="Pilot",
        duration_seconds=1260,
        manifest_url="http://backend/manifest"
    )

    assert created1 is True
    assert ep1.title == "Pilot"
    assert ep1.duration_seconds == 1260
    assert ep1.manifest_url == "http://backend/manifest"

    ep2, created2 = db.add_or_update_episode(
        show_id=show.id,
        provider="joyn",
        content_id="12346",
        season_number=1,
        episode_number=2,
        title="Big Flappy Bastards",
        duration_seconds=1260
    )
    assert created2 is True

    episodes = db.get_episodes_for_show(show.id)
    assert len(episodes) == 2

    count = db.mark_episodes_unavailable(show.id, "joyn")
    assert count == 2

    episodes = db.get_episodes_for_show(show.id)
    assert len(episodes) == 0


def test_movie_operations(db):
    movie, created = db.add_or_update_movie(
        provider="joyn",
        content_id="movie123",
        title="Dune",
        original_title="Dune",
        release_year=2021,
        duration_seconds=9300,
        genres=["Action", "Adventure", "Drama"],
        director="Denis Villeneuve",
        cast=["Timothée Chalamet", "Rebecca Ferguson"],
        manifest_url="http://backend/manifest"
    )

    assert created is True
    assert movie.title == "Dune"
    assert movie.release_year == 2021
    assert movie.director == "Denis Villeneuve"
    assert "Timothée Chalamet" in movie.cast
    assert movie.manifest_url == "http://backend/manifest"

    movie2, created2 = db.add_or_update_movie(
        provider="joyn",
        content_id="movie123",
        title="Dune (2021)",
        release_year=2021,
        is_highlight=False
    )

    assert created2 is False
    assert movie2.title == "Dune (2021)"
    assert movie2.is_highlight is False  # Updated

    found = db.get_movie_by_provider("joyn", "movie123")
    assert found is not None
    assert found.title == "Dune (2021)"

    # Test the movie dict
    movie_dict = found.to_dict()
    assert movie_dict["provider"] == "joyn"
    assert movie_dict["content_id"] == "movie123"

    count = db.mark_movies_unavailable("joyn")
    assert count == 1

    # Verify it's marked unavailable
    found2 = db.get_movie_by_provider("joyn", "movie123")
    assert found2 is not None
    assert found2.is_available is False


def test_crawl_history(db):
    history_id = db.start_crawl("joyn")
    assert history_id > 0

    db.finish_crawl(
        history_id=history_id,
        status="success",
        items_found=100,
        items_added=50,
        items_updated=30,
        items_removed=20
    )

    last = db.get_last_crawl("joyn")
    assert last is not None
    assert last.status == "success"
    assert last.items_found == 100
    assert last.items_added == 50

    history = db.get_crawl_history("joyn", limit=5)
    assert len(history) == 1

    # Test crawling history dict
    history_dict = history[0].to_dict()
    assert history_dict["provider"] == "joyn"
    assert history_dict["status"] == "success"


def test_stats(db):
    show, _ = db.get_or_create_show(
        normalized_title="show1",
        title="Show 1",
        provider="joyn",
        provider_id="id1"
    )

    db.add_or_update_episode(
        show_id=show.id,
        provider="joyn",
        content_id="ep1",
        season_number=1,
        episode_number=1,
        title="Episode 1"
    )

    db.add_or_update_movie(
        provider="joyn",
        content_id="movie1",
        title="Movie 1"
    )

    stats = db.get_stats()
    assert stats["total_tv_shows"] == 1
    assert stats["total_tv_episodes"] == 1
    assert stats["total_movies"] == 1
    assert "joyn" in stats["provider_stats"]
    assert stats["provider_stats"]["joyn"]["episodes"] == 1
    assert stats["provider_stats"]["joyn"]["movies"] == 1


def test_bulk_operations(db):
    # Create a show first
    show, _ = db.get_or_create_show(
        normalized_title="bulk_show",
        title="Bulk Show",
        provider="joyn",
        provider_id="bulk_id"
    )

    # Test bulk upsert for episodes
    episodes = [
        {
            "show_id": show.id,
            "provider": "joyn",
            "content_id": "ep1",
            "season_number": 1,
            "episode_number": 1,
            "title": "Episode 1",
            "duration_seconds": 1200
        },
        {
            "show_id": show.id,
            "provider": "joyn",
            "content_id": "ep2",
            "season_number": 1,
            "episode_number": 2,
            "title": "Episode 2",
            "duration_seconds": 1200
        }
    ]

    stats = db.bulk_upsert_episodes(episodes)
    assert stats["added"] == 2
    assert stats["updated"] == 0

    # Verify episodes were added
    all_episodes = db.get_episodes_for_show(show.id)
    assert len(all_episodes) == 2

    # Run again - should update
    stats = db.bulk_upsert_episodes(episodes)
    assert stats["added"] == 0
    assert stats["updated"] == 2

    # Test bulk upsert for movies
    movies = [
        {
            "provider": "joyn",
            "content_id": "movie_bulk_1",
            "title": "Bulk Movie 1",
            "release_year": 2024
        },
        {
            "provider": "joyn",
            "content_id": "movie_bulk_2",
            "title": "Bulk Movie 2",
            "release_year": 2024
        }
    ]

    stats = db.bulk_upsert_movies(movies)
    assert stats["added"] == 2
    assert stats["updated"] == 0

    # Run again - should update
    stats = db.bulk_upsert_movies(movies)
    assert stats["added"] == 0
    assert stats["updated"] == 2


def test_clear_provider_data(db):
    # Create some data
    show, _ = db.get_or_create_show(
        normalized_title="clear_test",
        title="Clear Test",
        provider="joyn",
        provider_id="clear_id"
    )

    db.add_or_update_episode(
        show_id=show.id,
        provider="joyn",
        content_id="clear_ep1",
        season_number=1,
        episode_number=1,
        title="Clear Episode"
    )

    db.add_or_update_movie(
        provider="joyn",
        content_id="clear_movie1",
        title="Clear Movie"
    )

    # Verify data exists
    stats = db.get_stats()
    assert stats["total_tv_shows"] == 1
    assert stats["total_tv_episodes"] == 1
    assert stats["total_movies"] == 1

    # Clear provider data
    results = db.clear_provider_data("joyn")
    assert results["episodes"] == 1
    assert results["movies"] == 1

    # Verify data is marked unavailable
    stats = db.get_stats()
    assert stats["total_tv_shows"] == 0  # Show is now unavailable
    assert stats["total_tv_episodes"] == 0
    assert stats["total_movies"] == 0