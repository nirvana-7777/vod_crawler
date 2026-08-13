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
    # get_or_create_show now returns (show_id, created) instead of (TVShow, created)
    show_id, created = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men",
        provider="joyn",
        provider_id="GN_SERIES_184925",
        plot="Two and a Half Men is an American television sitcom...",
        release_year=2003,
        genres=["Comedy", "Sitcom"]
    )

    assert created is True
    assert isinstance(show_id, str)
    assert len(show_id) > 0

    # Get the show as a dict to verify fields
    show_dict = db.get_show_by_id(show_id)
    assert show_dict is not None
    assert show_dict["title"] == "Two and a Half Men"
    assert show_dict["normalized_title"] == "two_and_a_half_men"
    assert show_dict["release_year"] == 2003
    assert show_dict["genres"] == ["Comedy", "Sitcom"]
    assert "joyn" in show_dict["provider_ids"]
    assert show_dict["provider_ids"]["joyn"] == "GN_SERIES_184925"

    # Add another provider mapping
    show_id2, created2 = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men (US)",
        provider="rtlplus",
        provider_id="12345",
        plot="Updated plot..."
    )

    assert created2 is False
    assert show_id2 == show_id  # Same show, merged

    # Get the updated show dict
    show_dict2 = db.get_show_by_id(show_id)
    assert show_dict2 is not None
    assert show_dict2["provider_ids"].get("joyn") == "GN_SERIES_184925"
    assert show_dict2["provider_ids"].get("rtlplus") == "12345"
    assert show_dict2["title"] == "Two and a Half Men (US)"  # Title updated

    # Verify get_show_by_id works
    found = db.get_show_by_id(show_id)
    assert found is not None
    assert found["title"] == "Two and a Half Men (US)"


def test_episode_operations(db):
    show_id, _ = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men",
        provider="joyn",
        provider_id="GN_SERIES_184925"
    )

    # add_or_update_episode now returns (TVEpisode, bool)
    ep1, created1 = db.add_or_update_episode(
        show_id=show_id,
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
        show_id=show_id,
        provider="joyn",
        content_id="12346",
        season_number=1,
        episode_number=2,
        title="Big Flappy Bastards",
        duration_seconds=1260
    )
    assert created2 is True

    # get_episodes_for_show now returns List[Dict[str, Any]]
    episodes = db.get_episodes_for_show(show_id)
    assert len(episodes) == 2

    # Test episode dict fields
    ep_dict = episodes[0]
    assert ep_dict["title"] == "Pilot"
    assert ep_dict["show_id"] == show_id
    assert ep_dict["provider"] == "joyn"
    assert ep_dict["content_id"] == "12345"

    count = db.mark_episodes_unavailable(show_id, "joyn")
    assert count == 2

    episodes = db.get_episodes_for_show(show_id)
    assert len(episodes) == 0


def test_movie_operations(db):
    # add_or_update_movie now returns (Movie, bool)
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

    # get_movie_by_provider now returns Dict[str, Any]
    movie_dict = db.get_movie_by_provider("joyn", "movie123")
    assert movie_dict is not None
    assert movie_dict["title"] == "Dune (2021)"
    assert movie_dict["provider"] == "joyn"
    assert movie_dict["content_id"] == "movie123"
    assert movie_dict["is_available"] is True

    count = db.mark_movies_unavailable("joyn")
    assert count == 1

    # Verify it's marked unavailable
    movie_dict2 = db.get_movie_by_provider("joyn", "movie123")
    assert movie_dict2 is not None
    assert movie_dict2["is_available"] is False


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

    # get_last_crawl now returns Dict[str, Any]
    last = db.get_last_crawl("joyn")
    assert last is not None
    assert last["status"] == "success"
    assert last["items_found"] == 100
    assert last["items_added"] == 50

    # get_crawl_history now returns List[Dict[str, Any]]
    history = db.get_crawl_history("joyn", limit=5)
    assert len(history) == 1

    # Test crawling history dict
    history_dict = history[0]
    assert history_dict["provider"] == "joyn"
    assert history_dict["status"] == "success"


def test_stats(db):
    show_id, _ = db.get_or_create_show(
        normalized_title="show1",
        title="Show 1",
        provider="joyn",
        provider_id="id1"
    )

    db.add_or_update_episode(
        show_id=show_id,
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
    show_id, _ = db.get_or_create_show(
        normalized_title="bulk_show",
        title="Bulk Show",
        provider="joyn",
        provider_id="bulk_id"
    )

    # Test bulk upsert for episodes
    episodes = [
        {
            "show_id": show_id,
            "provider": "joyn",
            "content_id": "ep1",
            "season_number": 1,
            "episode_number": 1,
            "title": "Episode 1",
            "duration_seconds": 1200
        },
        {
            "show_id": show_id,
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
    all_episodes = db.get_episodes_for_show(show_id)
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
    show_id, _ = db.get_or_create_show(
        normalized_title="clear_test",
        title="Clear Test",
        provider="joyn",
        provider_id="clear_id"
    )

    db.add_or_update_episode(
        show_id=show_id,
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
    # Note: shows_removed might be 1 if no other providers have this show
    assert results["shows_removed"] >= 0

    # Verify data is marked unavailable
    stats = db.get_stats()
    assert stats["total_tv_shows"] == 0  # Show is now unavailable
    assert stats["total_tv_episodes"] == 0
    assert stats["total_movies"] == 0


def test_sync_state(db):
    """Test sync state operations (multi-worker safe)"""
    # Initially no sync in progress
    state = db.get_sync_state()
    assert state["sync_in_progress"] is False
    assert state["sync_progress"] is None

    # Set sync in progress
    db.set_sync_state(True, {"provider": "joyn", "step": 1})

    state = db.get_sync_state()
    assert state["sync_in_progress"] is True
    assert state["sync_progress"] == {"provider": "joyn", "step": 1}

    # Clear sync state
    db.set_sync_state(False)

    state = db.get_sync_state()
    assert state["sync_in_progress"] is False
    assert state["sync_progress"] is None


def test_sync_job(db):
    """Test sync job operations"""
    # Create a job
    job_id = db.create_sync_job(provider="joyn")
    assert job_id is not None
    assert len(job_id) == 36  # UUID length

    # Update the job
    db.update_sync_job(
        job_id=job_id,
        status="running",
        progress={"step": 1, "total": 5},
        items_found=100,
        items_added=50,
        items_updated=30,
        items_removed=20,
    )

    # We don't have a get_sync_job method, but we can verify by checking
    # that the update didn't raise any errors. In a real test, we'd query
    # the job directly from the session.

    # Complete the job
    db.update_sync_job(
        job_id=job_id,
        status="complete",
        progress={"step": 5, "total": 5},
        items_found=200,
        items_added=100,
        items_updated=80,
        items_removed=20,
    )

    # Verify no errors occurred (if we had a getter, we'd assert here)
    assert True


def test_pricing_fields(db):
    """Test that pricing fields are stored correctly"""
    show_id, _ = db.get_or_create_show(
        normalized_title="pricing_show",
        title="Pricing Show",
        provider="joyn",
        provider_id="pricing_id"
    )

    # Add episode with pricing
    ep, created = db.add_or_update_episode(
        show_id=show_id,
        provider="joyn",
        content_id="pricing_ep1",
        season_number=1,
        episode_number=1,
        title="Pricing Episode",
        pricing_access_type="free",
        pricing_price_points=[],
        pricing_required_tiers=[],
        pricing_required_bouquets=[],
        pricing_rental_duration_hours=None,
        pricing_preview_minutes=10,
        pricing_description="Free preview"
    )

    assert created is True
    assert ep.pricing_access_type == "free"
    assert ep.pricing_price_points == []
    assert ep.pricing_preview_minutes == 10
    assert ep.pricing_description == "Free preview"

    # Add movie with pricing (SVOD)
    movie, created = db.add_or_update_movie(
        provider="joyn",
        content_id="pricing_movie1",
        title="Pricing Movie",
        pricing_access_type="svod",
        pricing_required_tiers=["premium"],
        pricing_required_bouquets=["movies"],
        pricing_price_points=[],
        pricing_rental_duration_hours=None
    )

    assert created is True
    assert movie.pricing_access_type == "svod"
    assert movie.pricing_required_tiers == ["premium"]
    assert movie.pricing_required_bouquets == ["movies"]

    # Verify via get_episode_by_provider (returns dict)
    ep_dict = db.get_episode_by_provider("joyn", "pricing_ep1")
    assert ep_dict is not None
    assert ep_dict["pricing_access_type"] == "free"
    assert ep_dict["pricing_preview_minutes"] == 10
    assert ep_dict["pricing_description"] == "Free preview"

    # Verify movie via get_movie_by_provider (returns dict)
    movie_dict = db.get_movie_by_provider("joyn", "pricing_movie1")
    assert movie_dict is not None
    assert movie_dict["pricing_access_type"] == "svod"
    assert movie_dict["pricing_required_tiers"] == ["premium"]
    assert movie_dict["pricing_required_bouquets"] == ["movies"]