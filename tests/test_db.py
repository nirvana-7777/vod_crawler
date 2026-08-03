#!/usr/bin/env python3
"""
Test database operations using pytest
"""
import tempfile
from pathlib import Path
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
    assert show.provider_ids == {"joyn": "GN_SERIES_184925"}
    assert show.release_year == 2003
    
    show2, created2 = db.get_or_create_show(
        normalized_title="two_and_a_half_men",
        title="Two and a Half Men (US)",
        provider="rtlplus",
        provider_id="12345",
        plot="Updated plot..."
    )
    
    assert created2 is False
    assert show2.provider_ids == {"joyn": "GN_SERIES_184925", "rtlplus": "12345"}
    
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
    assert "Denis Villeneuve" in movie.director
    
    movie2, created2 = db.add_or_update_movie(
        provider="joyn",
        content_id="movie123",
        title="Dune (2021)",
        release_year=2021,
        is_highlight=False
    )
    
    assert created2 is False
    assert movie2.title == "Dune (2021)"
    
    found = db.get_movie_by_provider("joyn", "movie123")
    assert found is not None
    assert found.title == "Dune (2021)"
    
    count = db.mark_movies_unavailable("joyn")
    assert count == 1

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
    
    history = db.get_crawl_history("joyn", limit=5)
    assert len(history) == 1

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
