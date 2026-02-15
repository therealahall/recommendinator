"""Tests for web API endpoints."""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.web.app import create_app
from src.web.state import app_state
from src.web.sync_manager import reset_sync_manager


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return {
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "mistral:7b",
            "embedding_model": "nomic-embed-text",
        },
        "storage": {
            "database_path": "data/test.db",
            "vector_db_path": "data/test_chroma",
        },
        "web": {
            "host": "0.0.0.0",
            "port": 8000,
        },
        "inputs": {
            "goodreads": {
                "plugin": "goodreads",
                "path": "inputs/goodreads_library_export.csv",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }


@pytest.fixture
def mock_components(mock_config):
    """Create mock components."""
    # Reset sync manager to ensure clean state between tests
    reset_sync_manager()

    with (
        patch("src.web.app.load_config", return_value=mock_config),
        patch("src.web.app.create_storage_manager") as mock_storage,
        patch("src.web.app.create_llm_components") as mock_llm,
        patch("src.web.app.create_recommendation_engine") as mock_engine,
    ):
        # Setup mocks
        mock_storage_manager = Mock()
        mock_storage.return_value = mock_storage_manager

        mock_client = Mock()
        mock_embedding_gen = Mock()
        mock_rec_gen = Mock()
        mock_llm.return_value = (mock_client, mock_embedding_gen, mock_rec_gen)

        mock_engine_instance = Mock()
        mock_engine.return_value = mock_engine_instance

        # Clear app state
        app_state.clear()

        # Create app
        app = create_app()

        # Store mocks in app state for access in tests
        app_state["storage"] = mock_storage_manager
        app_state["embedding_gen"] = mock_embedding_gen
        app_state["engine"] = mock_engine_instance
        app_state["config"] = mock_config

        yield {
            "app": app,
            "storage": mock_storage_manager,
            "embedding_gen": mock_embedding_gen,
            "engine": mock_engine_instance,
        }

        # Clean up sync manager after test
        reset_sync_manager()


@pytest.fixture
def client(mock_components):
    """Create test client."""
    return TestClient(mock_components["app"])


def test_root_endpoint(client):
    """Test root endpoint serves HTML."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_status_endpoint(client):
    """Test status endpoint."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "components" in data


def test_status_ready_when_ai_disabled_regression(client):
    """Regression test: Status should be 'ready' when AI is disabled.

    Bug reported: "System is Initializing" banner displayed perpetually
    when AI features are disabled.

    Root cause: The status endpoint required embedding_generator to be
    non-None for 'ready' status, but it is always None when AI is disabled.

    Fix: Only require embedding_generator when ai_enabled is true.
    """
    # Simulate AI disabled: no embedding_gen, no features config
    app_state["embedding_gen"] = None
    app_state["config"] = {
        "features": {"ai_enabled": False},
    }

    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"


def test_sync_sources_endpoint(client, mock_config):
    """Test sync sources endpoint returns only enabled sources from config."""
    response = client.get("/api/sync/sources")
    assert response.status_code == 200
    sources = response.json()
    assert isinstance(sources, list)
    # mock_config has goodreads enabled
    assert len(sources) >= 1
    goodreads = next((s for s in sources if s["id"] == "goodreads"), None)
    assert goodreads is not None
    assert goodreads["display_name"] == "Goodreads"
    assert goodreads["plugin_display_name"] == "Goodreads"


def test_sync_sources_only_enabled(client):
    """Test that disabled sources (enabled: false) are not returned."""
    # Override config: only goodreads and sonarr enabled
    app_state["config"] = {
        "inputs": {
            "goodreads": {
                "plugin": "goodreads",
                "path": "inputs/books.csv",
                "enabled": True,
            },
            "steam": {
                "plugin": "steam",
                "api_key": "x",
                "steam_id": "y",
                "enabled": False,
            },
            "sonarr": {
                "plugin": "sonarr",
                "url": "http://localhost:8989",
                "api_key": "key",
                "enabled": True,
            },
            "radarr": {
                "plugin": "radarr",
                "url": "http://localhost:7878",
                "api_key": "key",
                "enabled": False,
            },
        },
    }

    response = client.get("/api/sync/sources")
    assert response.status_code == 200
    sources = response.json()
    source_ids = [s["id"] for s in sources]

    assert "goodreads" in source_ids
    assert "sonarr" in source_ids
    assert "steam" not in source_ids
    assert "radarr" not in source_ids


def test_recommendations_endpoint(client, mock_components):
    """Test recommendations endpoint."""
    # Setup mock recommendations
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended highly similar",
        }
    ]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    response = client.get("/api/recommendations?type=book&count=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Test Book"
    assert data[0]["author"] == "Test Author"


def test_recommendations_invalid_type(client):
    """Test recommendations endpoint with invalid type."""
    response = client.get("/api/recommendations?type=invalid&count=1")
    assert response.status_code == 400


def test_complete_endpoint(client, mock_components):
    """Test complete endpoint."""
    mock_components["embedding_gen"].generate_content_embedding.return_value = [
        0.1
    ] * 768
    mock_components["storage"].save_content_item.return_value = 1

    response = client.post(
        "/api/complete",
        json={
            "content_type": "book",
            "title": "Test Book",
            "author": "Test Author",
            "rating": 4,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "id" in data


def test_complete_invalid_rating(client):
    """Test complete endpoint with invalid rating."""
    response = client.post(
        "/api/complete",
        json={
            "content_type": "book",
            "title": "Test Book",
            "rating": 6,  # Invalid
        },
    )

    # Pydantic validation returns 422 for invalid data
    assert response.status_code == 422


def test_update_endpoint(client, mock_components):
    """Test update endpoint starts background sync."""
    # Mock the parser
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
    )

    with (
        patch(
            "src.ingestion.sources.goodreads.GoodreadsPlugin.fetch",
            return_value=iter([mock_item]),
        ),
        patch(
            "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
            return_value=[],
        ),
    ):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "goodreads"})

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        # New async behavior: returns "sync started" message, not count
        assert "started" in data["message"].lower() or "sources" in data


def test_update_endpoint_steam(client, mock_components):
    """Test update endpoint starts background sync for Steam."""
    # Update app_state config to include Steam
    app_state["config"]["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    # Background sync: returns "sync started" message
    assert "started" in data["message"].lower() or "sources" in data


def test_update_endpoint_steam_disabled(client, mock_components):
    """Test update endpoint with disabled Steam source."""
    app_state["config"]["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": False,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 200
    data = response.json()
    assert "disabled" in data["message"].lower()
    assert data["count"] == 0


def test_update_endpoint_steam_missing_api_key(client, mock_components):
    """Test update endpoint with missing Steam API key."""
    app_state["config"]["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    data = response.json()
    assert "API key" in data["detail"] or "required" in data["detail"]


def test_update_endpoint_steam_missing_id(client, mock_components):
    """Test update endpoint with missing Steam ID."""
    app_state["config"]["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "",
        "vanity_url": "",
        "enabled": True,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    data = response.json()
    assert "steam_id" in data["detail"] or "vanity_url" in data["detail"]


def test_update_endpoint_steam_api_error(client, mock_components):
    """Test update endpoint handles Steam API error during validation.

    Note: With background sync, API errors during the actual sync are handled
    asynchronously. This test verifies the sync can be started when config is valid.
    """
    app_state["config"]["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    # With background sync, the endpoint returns 200 to start the sync
    # API errors are reported via the sync status endpoint
    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 200
    data = response.json()
    assert "message" in data


def test_update_endpoint_all_sources(client, mock_components):
    """Test update endpoint with 'all' source starts background sync."""
    app_state["config"]["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    mock_book = ContentItem(
        id="1",
        title="Test Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
    )

    mock_game = ContentItem(
        id="12345",
        title="Test Game",
        author=None,
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
    )

    with (
        patch(
            "src.ingestion.sources.goodreads.GoodreadsPlugin.fetch",
            return_value=iter([mock_book]),
        ),
        patch(
            "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
            return_value=[],
        ),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.fetch",
            return_value=iter([mock_game]),
        ),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.validate_config",
            return_value=[],
        ),
    ):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "all"})

        assert response.status_code == 200
        data = response.json()
        # New async behavior: returns sync started message with sources list
        assert "message" in data
        assert "sources" in data
        assert "goodreads" in data["sources"]
        assert "steam" in data["sources"]


# ---------------------------------------------------------------------------
# User preferences endpoint tests (Phase 5)
# ---------------------------------------------------------------------------


def test_get_user_preferences_defaults(client, mock_components):
    """GET /api/users/1/preferences returns defaults for new user."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )

    response = client.get("/api/users/1/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["scorer_weights"] == {}
    assert data["series_in_order"] is True
    assert data["custom_rules"] == []


def test_put_user_preferences_partial(client, mock_components):
    """PUT /api/users/1/preferences merges partial update."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    response = client.put(
        "/api/users/1/preferences",
        json={"scorer_weights": {"genre_match": 3.0}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scorer_weights"] == {"genre_match": 3.0}
    assert data["series_in_order"] is True  # unchanged default


def test_put_user_preferences_full(client, mock_components):
    """PUT /api/users/1/preferences can update all fields."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    response = client.put(
        "/api/users/1/preferences",
        json={
            "scorer_weights": {"genre_match": 5.0},
            "series_in_order": False,
            "variety_after_completion": True,
            "custom_rules": ["no horror"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scorer_weights"] == {"genre_match": 5.0}
    assert data["series_in_order"] is False
    assert data["variety_after_completion"] is True
    assert data["custom_rules"] == ["no horror"]


def test_list_users(client, mock_components):
    """Test GET /api/users returns user list."""
    mock_components["storage"].get_all_users = Mock(
        return_value=[
            {"id": 1, "username": "default", "display_name": "Default User"},
            {"id": 2, "username": "alice", "display_name": "Alice"},
        ]
    )

    response = client.get("/api/users")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["username"] == "default"
    assert data[1]["username"] == "alice"


def test_list_items(client, mock_components):
    """Test GET /api/items returns filtered items."""
    mock_items = [
        ContentItem(
            id="1",
            title="Test Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            source="goodreads",
        )
    ]
    mock_components["storage"].get_content_items = Mock(return_value=mock_items)

    response = client.get("/api/items?type=book&status=completed&user_id=1&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Test Book"
    assert data[0]["content_type"] == "book"
    assert data[0]["status"] == "completed"


def test_list_items_invalid_type(client, mock_components):
    """Test GET /api/items with invalid type returns 400."""
    response = client.get("/api/items?type=invalid")
    assert response.status_code == 400


def test_list_items_invalid_status(client, mock_components):
    """Test GET /api/items with invalid status returns 400."""
    response = client.get("/api/items?status=invalid")
    assert response.status_code == 400


def test_recommendations_include_breakdown(client, mock_components):
    """Test recommendations response includes score_breakdown."""
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended highly similar",
            "score_breakdown": {"genre_match": 0.9, "creator_match": 0.5},
        }
    ]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    response = client.get("/api/recommendations?type=book&count=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "score_breakdown" in data[0]
    assert data[0]["score_breakdown"]["genre_match"] == 0.9
    assert data[0]["score_breakdown"]["creator_match"] == 0.5


def test_recommendations_with_user_id(client, mock_components):
    """GET /api/recommendations with user_id loads user preferences."""
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended highly similar",
        }
    ]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig(scorer_weights={"genre_match": 3.0})
    )

    response = client.get("/api/recommendations?type=book&count=1&user_id=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    # Verify engine was called with user_preference_config
    call_kwargs = mock_components["engine"].generate_recommendations.call_args.kwargs
    assert call_kwargs["user_preference_config"] is not None


# ---------------------------------------------------------------------------
# Ignore Item Tests
# ---------------------------------------------------------------------------


def test_ignore_item_success(client, mock_components):
    """PATCH /api/items/{db_id}/ignore sets item ignored status."""
    mock_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        ignored=False,
    )

    mock_components["storage"].get_content_item = Mock(return_value=mock_item)
    mock_components["storage"].set_item_ignored = Mock(return_value=True)

    response = client.patch(
        "/api/items/42/ignore?user_id=1",
        json={"ignored": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["db_id"] == 42
    assert data["title"] == "Test Book"
    assert data["ignored"] is True

    # Verify storage method was called
    mock_components["storage"].set_item_ignored.assert_called_once_with(
        42, True, user_id=1
    )


def test_ignore_item_not_found(client, mock_components):
    """PATCH /api/items/{db_id}/ignore returns 404 if item not found."""
    mock_components["storage"].get_content_item = Mock(return_value=None)

    response = client.patch(
        "/api/items/999/ignore?user_id=1",
        json={"ignored": True},
    )
    assert response.status_code == 404


def test_unignore_item(client, mock_components):
    """PATCH /api/items/{db_id}/ignore can unignore an item."""
    mock_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        ignored=True,
    )

    mock_components["storage"].get_content_item = Mock(return_value=mock_item)
    mock_components["storage"].set_item_ignored = Mock(return_value=True)

    response = client.patch(
        "/api/items/42/ignore?user_id=1",
        json={"ignored": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ignored"] is False

    mock_components["storage"].set_item_ignored.assert_called_once_with(
        42, False, user_id=1
    )


def test_list_items_includes_ignored(client, mock_components):
    """GET /api/items returns items with ignored field."""
    mock_items = [
        ContentItem(
            id="1",
            db_id=1,
            title="Book 1",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            ignored=False,
        ),
        ContentItem(
            id="2",
            db_id=2,
            title="Book 2",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            ignored=True,
        ),
    ]
    mock_components["storage"].get_content_items = Mock(return_value=mock_items)

    response = client.get("/api/items?user_id=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["ignored"] is False
    assert data[0]["db_id"] == 1
    assert data[1]["ignored"] is True
    assert data[1]["db_id"] == 2


def test_recommendations_include_db_id(client, mock_components):
    """GET /api/recommendations includes db_id in response."""
    mock_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended highly similar",
        }
    ]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    response = client.get("/api/recommendations?type=book&count=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["db_id"] == 42
    assert data[0]["title"] == "Test Book"
