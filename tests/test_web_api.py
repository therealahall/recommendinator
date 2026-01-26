"""Tests for web API endpoints."""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.web.app import create_app
from src.web.state import app_state


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
    """Test update endpoint."""
    # Mock the parser
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
    )

    with patch("src.web.api.parse_goodreads_csv", return_value=[mock_item]):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "goodreads"})

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "count" in data


def test_update_endpoint_steam(client, mock_components):
    """Test update endpoint with Steam source."""
    # Update app_state config to include Steam
    app_state["config"]["inputs"]["steam"] = {
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    mock_steam_item = ContentItem(
        id="12345",
        title="Test Game",
        author=None,
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
    )

    with patch("src.web.api.parse_steam_games", return_value=[mock_steam_item]):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "steam"})

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "count" in data
        assert data["count"] == 1


def test_update_endpoint_steam_disabled(client, mock_components):
    """Test update endpoint with disabled Steam source."""
    app_state["config"]["inputs"]["steam"] = {
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
    """Test update endpoint with Steam API error."""
    from src.ingestion.sources.steam import SteamAPIError

    app_state["config"]["inputs"]["steam"] = {
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    with patch("src.web.api.parse_steam_games", side_effect=SteamAPIError("API error")):
        response = client.post("/api/update", json={"source": "steam"})

        assert response.status_code == 500
        data = response.json()
        assert "error" in data["detail"].lower() or "Steam" in data["detail"]


def test_update_endpoint_all_sources(client, mock_components):
    """Test update endpoint with 'all' source including Steam."""
    app_state["config"]["inputs"]["steam"] = {
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
        patch("src.web.api.parse_goodreads_csv", return_value=[mock_book]),
        patch("src.web.api.parse_steam_games", return_value=[mock_game]),
    ):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "all"})

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2  # Both book and game
