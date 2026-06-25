"""Tests for web API endpoints."""

import json
import threading
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.ingestion.sync import SyncResult
from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.utils.series import MAX_SEASONS
from src.web.api import APP_VERSION, _item_to_response
from src.web.app import create_app
from src.web.enrichment_manager import WebEnrichmentManager
from src.web.epic_auth import EpicAuthError
from src.web.gog_auth import GogAuthError
from src.web.state import AppState, app_state
from src.web.sync_manager import (
    SyncManager,
    get_sync_manager,
    reset_sync_manager,
)
from src.web.trakt_auth import DevicePollResult, DevicePollStatus, TraktAuthError


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
        patch("src.web.app.migrate_config_credentials"),
    ):
        # Setup mocks
        mock_storage_manager = Mock(spec=StorageManager)
        mock_storage_manager.get_credentials_for_source.return_value = {}
        mock_storage_manager.list_source_configs.return_value = []
        mock_storage.return_value = mock_storage_manager

        mock_client = Mock(spec=OllamaClient)
        mock_embedding_gen = Mock(spec=EmbeddingGenerator)
        mock_rec_gen = Mock(spec=RecommendationGenerator)
        mock_llm.return_value = (mock_client, mock_embedding_gen, mock_rec_gen)

        mock_engine_instance = Mock(spec=RecommendationEngine)
        mock_engine_instance.storage = mock_storage_manager
        mock_engine.return_value = mock_engine_instance

        # Reset app state to defaults
        fresh = AppState()
        for f in fields(fresh):
            setattr(app_state, f.name, getattr(fresh, f.name))

        # Create app
        app = create_app()

        # Store mocks in app state for access in tests
        app_state.storage = mock_storage_manager
        app_state.embedding_gen = mock_embedding_gen
        app_state.engine = mock_engine_instance
        app_state.config = mock_config

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


class TestRootEndpoint:
    """Tests for the root HTML endpoint."""

    def test_serves_html_with_branding(self, client):
        """Test root endpoint serves HTML with correct branding."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Recommendinator" in response.text

    def test_vite_spa_uses_hashed_assets(self, client):
        """When dist/index.html exists, root() serves Vite content-hashed assets.

        Uses a synthetic dist/index.html via monkeypatch so the test is
        deterministic regardless of whether `make build-frontend` has run.
        """
        fake_html = (
            '<script type="module" crossorigin '
            'src="/static/dist/assets/index-abc123.js"></script>'
        )
        original_exists = Path.exists
        original_read_text = Path.read_text

        def patched_exists(self: Path) -> bool:
            if str(self).endswith("dist/index.html"):
                return True
            return original_exists(self)

        def patched_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if str(self).endswith("dist/index.html"):
                return fake_html
            return original_read_text(self, *args, **kwargs)

        with (
            patch.object(Path, "exists", patched_exists),
            patch.object(Path, "read_text", patched_read_text),
        ):
            response = client.get("/")
        assert response.status_code == 200
        assert "/assets/" in response.text
        assert 'type="module"' in response.text

    def test_spa_has_no_inline_scripts(self, client):
        """Vite SPA dist/index.html must not contain inline scripts (CSP compliance).

        Uses a synthetic dist/index.html to verify the assertion logic.
        An inline script would violate CSP script-src 'self'.
        """
        import re

        fake_html = (
            '<script type="module" crossorigin '
            'src="/static/dist/assets/index-abc123.js"></script>'
        )
        original_exists = Path.exists
        original_read_text = Path.read_text

        def patched_exists(self: Path) -> bool:
            if str(self).endswith("dist/index.html"):
                return True
            return original_exists(self)

        def patched_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if str(self).endswith("dist/index.html"):
                return fake_html
            return original_read_text(self, *args, **kwargs)

        with (
            patch.object(Path, "exists", patched_exists),
            patch.object(Path, "read_text", patched_read_text),
        ):
            response = client.get("/")
        assert response.status_code == 200
        inline_scripts = re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>(?!<\/script>)", response.text
        )
        assert (
            not inline_scripts
        ), f"Inline scripts violate CSP script-src 'self': {inline_scripts}"

    def test_fallback_when_template_missing(self, client):
        """root() returns a fallback page when no HTML template exists."""
        original_exists = Path.exists

        def patched_exists(self: Path) -> bool:
            if self.name == "index.html":
                return False
            return original_exists(self)

        with patch.object(Path, "exists", patched_exists):
            response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Recommendinator API" in response.text


def test_app_title(mock_components):
    """Test that the FastAPI app title reflects the Recommendinator brand."""
    assert mock_components["app"].title == "Recommendinator API"


def test_status_endpoint(client):
    """Test status endpoint returns version from src.__version__."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["version"] == APP_VERSION
    assert isinstance(data["components"], dict)


class TestSecurityHeaders:
    """Tests for security-related HTTP headers."""

    def test_csp_script_src_self_only(self, client):
        """CSP script-src should be 'self' only (no CDN)."""
        response = client.get("/api/status")
        csp = response.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp
        assert "cdn.jsdelivr.net" not in csp

    def test_csp_frame_ancestors_none(self, client):
        """CSP should include frame-ancestors 'none'."""
        csp = client.get("/api/status").headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp

    def test_csp_style_src_no_unsafe_inline(self, client):
        """CSP style-src should not include 'unsafe-inline'."""
        csp = client.get("/api/status").headers["Content-Security-Policy"]
        assert "style-src 'self'" in csp
        assert "unsafe-inline" not in csp

    def test_referrer_policy(self, client):
        """Referrer-Policy header should be set."""
        headers = client.get("/api/status").headers
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, client):
        """Permissions-Policy header should restrict sensitive features."""
        policy = client.get("/api/status").headers["Permissions-Policy"]
        assert "camera=()" in policy
        assert "microphone=()" in policy
        assert "geolocation=()" in policy

    def test_x_frame_options_deny(self, client):
        """X-Frame-Options should be DENY."""
        assert client.get("/api/status").headers["X-Frame-Options"] == "DENY"


class TestStatusEndpointRegression:
    """Regression tests for the status endpoint."""

    def test_status_ready_when_ai_disabled_regression(self, client):
        """Regression: Status should be 'ready' when AI is disabled.

        Bug reported: "System is Initializing" banner displayed perpetually
        when AI features are disabled.
        Root cause: The status endpoint required embedding_generator to be
        non-None for 'ready' status, but it is always None when AI is disabled.
        Fix: Only require embedding_generator when ai_enabled is true.
        """
        # Simulate AI disabled: no embedding_gen, no features config
        app_state.embedding_gen = None
        app_state.config = {
            "features": {"ai_enabled": False},
        }

        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"


class TestStatusRecommendationsConfig:
    """Tests for recommendations_config in the /api/status response."""

    def test_status_includes_recommendations_config_defaults(self, client):
        """GET /api/status includes default max_count and default_count."""
        app_state.config = {"features": {"ai_enabled": False}}

        response = client.get("/api/status")
        assert response.status_code == 200
        rec_cfg = response.json()["recommendations_config"]
        assert rec_cfg["max_count"] == 20
        assert rec_cfg["default_count"] == 5

    def test_status_reads_recommendations_config_from_config(self, client):
        """GET /api/status surfaces max_count and default_count from config."""
        app_state.config = {
            "features": {"ai_enabled": False},
            "recommendations": {"max_count": 50, "default_count": 10},
        }

        response = client.get("/api/status")
        assert response.status_code == 200
        rec_cfg = response.json()["recommendations_config"]
        assert rec_cfg["max_count"] == 50
        assert rec_cfg["default_count"] == 10

    def test_status_with_no_config_uses_defaults(self, client):
        """GET /api/status returns defaults when config is None."""
        app_state.config = None

        response = client.get("/api/status")
        assert response.status_code == 200
        rec_cfg = response.json()["recommendations_config"]
        assert rec_cfg["max_count"] == 20
        assert rec_cfg["default_count"] == 5


def test_sync_sources_endpoint(client, mock_config):
    """Test sync sources endpoint returns only enabled sources from config."""
    response = client.get("/api/sync/sources")
    assert response.status_code == 200
    sources = response.json()
    assert isinstance(sources, list)
    # mock_config has exactly goodreads enabled
    assert len(sources) == 1
    goodreads = next((s for s in sources if s["id"] == "goodreads"), None)
    assert goodreads is not None
    assert goodreads["display_name"] == "Goodreads"
    assert goodreads["plugin_display_name"] == "Goodreads"


def test_sync_sources_lists_all_with_enabled_flag(client):
    """All configured sources are listed; ``enabled`` flag exposed per source.

    The UI renders disabled sources in a muted state instead of hiding them
    entirely, so the listing endpoint must surface them. ``resolve_inputs``
    is the gate that filters to enabled-only for sync execution.
    """
    app_state.config = {
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
    by_id = {s["id"]: s for s in sources}

    assert by_id["goodreads"]["enabled"] is True
    assert by_id["sonarr"]["enabled"] is True
    assert by_id["steam"]["enabled"] is False
    assert by_id["radarr"]["enabled"] is False


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
    app_state.config["inputs"]["steam"] = {
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
    app_state.config["inputs"]["steam"] = {
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
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    data = response.json()
    assert "not properly configured" in data["detail"]
    assert "api_key" in data["detail"].lower()


def test_update_endpoint_steam_missing_id(client, mock_components):
    """Test update endpoint with missing Steam ID."""
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "",
        "vanity_url": "",
        "enabled": True,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    data = response.json()
    assert "not properly configured" in data["detail"]
    assert "steam_id" in data["detail"] or "vanity_url" in data["detail"]


def test_update_endpoint_steam_api_error(client, mock_components):
    """Test update endpoint handles Steam API error during validation.

    Note: With background sync, API errors during the actual sync are handled
    asynchronously. This test verifies the sync can be started when config is valid.
    """
    app_state.config["inputs"]["steam"] = {
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
    app_state.config["inputs"]["steam"] = {
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
            "variety_penalty": 4.0,
            "custom_rules": ["no horror"],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scorer_weights"] == {"genre_match": 5.0}
    assert data["series_in_order"] is False
    assert data["variety_penalty"] == 4.0
    assert data["custom_rules"] == ["no horror"]


def test_put_user_preferences_accepts_max_variety_penalty(client, mock_components):
    """variety_penalty at the 5.0 maximum is accepted and saved."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 5.0},
    )
    assert response.status_code == 200
    assert response.json()["variety_penalty"] == 5.0
    mock_components["storage"].save_user_preference_config.assert_called_once()


def test_put_user_preferences_accepts_zero_variety_penalty(client, mock_components):
    """variety_penalty at the 0.0 minimum is accepted and saved (penalty off)."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 0.0},
    )
    assert response.status_code == 200
    assert response.json()["variety_penalty"] == 0.0
    mock_components["storage"].save_user_preference_config.assert_called_once()


def test_put_user_preferences_rejects_out_of_range_variety_penalty(
    client, mock_components
):
    """variety_penalty above the 5.0 maximum is rejected with a 422."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 6.0},
    )
    assert response.status_code == 422
    mock_components["storage"].save_user_preference_config.assert_not_called()


def test_put_user_preferences_rejects_negative_variety_penalty(client, mock_components):
    """variety_penalty below 0.0 is rejected with a 422 and never saved."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig()
    )
    mock_components["storage"].save_user_preference_config = Mock()

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": -0.1},
    )
    assert response.status_code == 422
    mock_components["storage"].save_user_preference_config.assert_not_called()


def test_get_user_preferences_includes_variety_penalty(client, mock_components):
    """GET surfaces the numeric variety_penalty field."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig(variety_penalty=0.4)
    )

    response = client.get("/api/users/1/preferences")
    assert response.status_code == 200
    assert response.json()["variety_penalty"] == 0.4


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


def test_recommendations_include_variety_penalty(client, mock_components):
    """Recommendations response includes the variety_penalty field (issue #74)."""
    mock_item = ContentItem(
        id="1",
        title="Penalised Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.2,
            "similarity_score": 0.5,
            "preference_score": 0.5,
            "reasoning": "Recommended",
            "score_breakdown": {"genre_match": 0.9},
            "variety_penalty": 0.8,
        }
    ]
    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    response = client.get("/api/recommendations?type=book&count=1")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["variety_penalty"] == 0.8


def test_recommendations_variety_penalty_defaults_to_zero(client, mock_components):
    """variety_penalty defaults to 0.0 when the engine omits it."""
    mock_item = ContentItem(
        id="1",
        title="Plain Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    mock_recommendations = [
        {
            "item": mock_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Recommended",
            "score_breakdown": {"genre_match": 0.9},
        }
    ]
    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    response = client.get("/api/recommendations?type=book&count=1")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["variety_penalty"] == 0.0


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


def test_list_items_hides_ignored_by_default(client, mock_components):
    """GET /api/items defaults to include_ignored=False, hiding ignored items."""
    mock_components["storage"].get_content_items = Mock(return_value=[])

    response = client.get("/api/items?user_id=1")
    assert response.status_code == 200

    mock_components["storage"].get_content_items.assert_called_once()
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["include_ignored"] is False


def test_list_items_include_ignored_true(client, mock_components):
    """GET /api/items?include_ignored=true passes include_ignored=True to storage."""
    mock_components["storage"].get_content_items = Mock(return_value=[])

    response = client.get("/api/items?user_id=1&include_ignored=true")
    assert response.status_code == 200

    mock_components["storage"].get_content_items.assert_called_once()
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["include_ignored"] is True


def test_list_items_needs_rating_forces_completed_and_unrated(client, mock_components):
    """GET /api/items?needs_rating=true forwards status=completed + unrated_only."""
    mock_components["storage"].get_content_items.return_value = []

    response = client.get("/api/items?user_id=1&needs_rating=true")
    assert response.status_code == 200

    mock_components["storage"].get_content_items.assert_called_once()
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
    assert call_kwargs["unrated_only"] is True


def test_list_items_needs_rating_overrides_explicit_status(client, mock_components):
    """needs_rating forces completed status even when a different status is passed."""
    mock_components["storage"].get_content_items.return_value = []

    response = client.get("/api/items?user_id=1&status=unread&needs_rating=true")
    assert response.status_code == 200

    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
    assert call_kwargs["unrated_only"] is True


def test_list_items_default_does_not_filter_unrated(client, mock_components):
    """GET /api/items without needs_rating passes unrated_only=False to storage."""
    mock_components["storage"].get_content_items.return_value = []

    response = client.get("/api/items?user_id=1")
    assert response.status_code == 200

    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["unrated_only"] is False


def test_list_items_needs_rating_returns_only_completed_unrated(
    client, mock_components
):
    """needs_rating returns the completed+unrated set the storage layer produces.

    Storage applies the actual filter (covered by storage-layer tests); the
    endpoint must return whatever that filtered query yields unmodified.
    """
    completed_unrated = ContentItem(
        id="1",
        title="Completed Unrated",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=None,
    )
    mock_components["storage"].get_content_items.return_value = [completed_unrated]

    response = client.get("/api/items?user_id=1&needs_rating=true")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Completed Unrated"
    assert data[0]["status"] == "completed"
    assert data[0]["rating"] is None


def test_list_items_needs_rating_composes_with_type(client, mock_components):
    """needs_rating + type forwards content_type, completed status, and unrated_only."""
    mock_components["storage"].get_content_items.return_value = []

    response = client.get("/api/items?user_id=1&needs_rating=true&type=book")
    assert response.status_code == 200

    mock_components["storage"].get_content_items.assert_called_once()
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["content_type"] == ContentType.BOOK
    assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
    assert call_kwargs["unrated_only"] is True


def test_list_items_needs_rating_composes_with_include_ignored(client, mock_components):
    """needs_rating + include_ignored forwards both flags plus completed status."""
    mock_components["storage"].get_content_items.return_value = []

    response = client.get("/api/items?user_id=1&needs_rating=true&include_ignored=true")
    assert response.status_code == 200

    mock_components["storage"].get_content_items.assert_called_once()
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
    assert call_kwargs["unrated_only"] is True
    assert call_kwargs["include_ignored"] is True


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


# ---------------------------------------------------------------------------
# GET /api/items/{db_id} — Single item retrieval
# ---------------------------------------------------------------------------


def test_get_single_item(client, mock_components):
    """GET /api/items/{db_id} returns a single content item."""
    mock_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        review="Great",
    )
    mock_components["storage"].get_content_item = Mock(return_value=mock_item)

    response = client.get("/api/items/42?user_id=1")
    assert response.status_code == 200
    data = response.json()
    assert data["db_id"] == 42
    assert data["title"] == "Test Book"
    assert data["rating"] == 4
    assert data["review"] == "Great"
    assert data["status"] == "completed"

    mock_components["storage"].get_content_item.assert_called_once_with(42, user_id=1)


def test_get_single_item_not_found(client, mock_components):
    """GET /api/items/{db_id} returns 404 if item not found."""
    mock_components["storage"].get_content_item = Mock(return_value=None)

    response = client.get("/api/items/999?user_id=1")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/items/{db_id} — Item edit
# ---------------------------------------------------------------------------


def test_edit_item_status(client, mock_components):
    """PATCH /api/items/{db_id} updates item status."""
    updated_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=updated_item)

    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unread"

    mock_components["storage"].update_item_from_ui.assert_called_once_with(
        db_id=42,
        status="unread",
        rating=None,
        review=None,
        seasons_watched=None,
        genres=None,
        tags=None,
        description=None,
        user_id=1,
    )


def test_edit_item_rating(client, mock_components):
    """PATCH /api/items/{db_id} updates item rating."""
    updated_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
    )
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=updated_item)

    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "completed", "rating": 5},
    )
    assert response.status_code == 200
    assert response.json()["rating"] == 5


def test_edit_item_review(client, mock_components):
    """PATCH /api/items/{db_id} updates item review."""
    updated_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Game",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        review="Amazing game",
    )
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=updated_item)

    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "completed", "review": "Amazing game"},
    )
    assert response.status_code == 200
    assert response.json()["review"] == "Amazing game"


def test_edit_tv_show_seasons(client, mock_components):
    """PATCH /api/items/{db_id} passes seasons_watched for TV shows."""
    updated_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Test Show",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.CURRENTLY_CONSUMING,
        metadata={"seasons": 10, "seasons_watched": [1, 2, 3]},
    )
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=updated_item)

    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "currently_consuming", "seasons_watched": [1, 2, 3]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["seasons_watched"] == [1, 2, 3]
    assert data["total_seasons"] == 10

    mock_components["storage"].update_item_from_ui.assert_called_once_with(
        db_id=42,
        status="currently_consuming",
        rating=None,
        review=None,
        seasons_watched=[1, 2, 3],
        genres=None,
        tags=None,
        description=None,
        user_id=1,
    )


def test_edit_rejects_out_of_range_season_regression(client, mock_components):
    """PATCH /api/items/{db_id} rejects season numbers outside the cap.

    Regression: seasons_watched was unbounded, so a hostile value could feed
    an enormous range() downstream. The request model now bounds each element
    to 1..MAX_SEASONS and the list to MAX_SEASONS entries, rejecting bad input
    at the API boundary before any storage write.
    """
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)

    # Above the per-element cap.
    too_high = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "currently_consuming", "seasons_watched": [1, 2_000_000_000]},
    )
    assert too_high.status_code == 422

    # Below the per-element minimum (ge=1).
    too_low = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "currently_consuming", "seasons_watched": [0]},
    )
    assert too_low.status_code == 422

    # More entries than the list cap allows.
    too_many = client.patch(
        "/api/items/42?user_id=1",
        json={
            "status": "currently_consuming",
            "seasons_watched": [1] * (MAX_SEASONS + 1),
        },
    )
    assert too_many.status_code == 422

    mock_components["storage"].update_item_from_ui.assert_not_called()


def test_edit_item_not_found(client, mock_components):
    """PATCH /api/items/{db_id} returns 404 if item not found."""
    mock_components["storage"].update_item_from_ui = Mock(return_value=False)

    response = client.patch(
        "/api/items/999?user_id=1",
        json={"status": "unread"},
    )
    assert response.status_code == 404


def test_edit_invalid_status(client, mock_components):
    """PATCH /api/items/{db_id} returns 400 for invalid status."""
    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "invalid_status"},
    )
    assert response.status_code == 400
    assert "Invalid status" in response.json()["detail"]


def test_edit_response_includes_tv_metadata(client, mock_components):
    """GET /api/items response includes seasons_watched and total_seasons for TV."""
    mock_item = ContentItem(
        id="tv_1",
        db_id=10,
        title="Survivor",
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.CURRENTLY_CONSUMING,
        metadata={"seasons": 50, "seasons_watched": [1, 2, 3, 4, 5]},
    )
    mock_components["storage"].get_content_items = Mock(return_value=[mock_item])

    response = client.get("/api/items?user_id=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["seasons_watched"] == [1, 2, 3, 4, 5]
    assert data[0]["total_seasons"] == 50


# ---------------------------------------------------------------------------
# GET /api/items — enrichment filter and exposed fields
# ---------------------------------------------------------------------------


def test_list_items_filters_not_enriched(client, mock_components):
    """GET /api/items?enrichment=not_enriched forwards the filter to storage."""
    mock_components["storage"].get_content_items = Mock(return_value=[])

    response = client.get("/api/items?user_id=1&enrichment=not_enriched")

    assert response.status_code == 200
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["enrichment"] == "not_enriched"


def test_list_items_filters_enriched(client, mock_components):
    """GET /api/items?enrichment=enriched forwards the filter to storage."""
    mock_components["storage"].get_content_items = Mock(return_value=[])

    response = client.get("/api/items?user_id=1&enrichment=enriched")

    assert response.status_code == 200
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["enrichment"] == "enriched"


def test_list_items_invalid_enrichment_returns_422(client, mock_components):
    """GET /api/items?enrichment=bogus is rejected at the API boundary."""
    response = client.get("/api/items?user_id=1&enrichment=bogus")
    assert response.status_code == 422


def test_list_items_default_enrichment_is_none(client, mock_components):
    """GET /api/items without enrichment passes None (no filter)."""
    mock_components["storage"].get_content_items = Mock(return_value=[])

    response = client.get("/api/items?user_id=1")

    assert response.status_code == 200
    call_kwargs = mock_components["storage"].get_content_items.call_args[1]
    assert call_kwargs["enrichment"] is None


def test_list_items_response_exposes_enrichment_fields(client, mock_components):
    """GET /api/items exposes enriched plus genres/tags/description."""
    mock_item = ContentItem(
        id="movie_1",
        db_id=7,
        title="Test Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={
            "genres": ["Drama"],
            "tags": ["slow-burn"],
            "description": "A tense character study.",
        },
    )
    mock_item.enriched = True
    mock_components["storage"].get_content_items = Mock(return_value=[mock_item])

    response = client.get("/api/items?user_id=1")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["enriched"] is True
    assert data[0]["genres"] == ["Drama"]
    assert data[0]["tags"] == ["slow-burn"]
    assert data[0]["description"] == "A tense character study."


def test_get_single_item_exposes_enrichment_fields(client, mock_components):
    """GET /api/items/{db_id} exposes enriched plus genres/tags/description."""
    mock_item = ContentItem(
        id="movie_1",
        db_id=7,
        title="Test Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={"genres": ["Drama"], "tags": [], "description": None},
    )
    mock_item.enriched = False
    mock_components["storage"].get_content_item = Mock(return_value=mock_item)

    response = client.get("/api/items/7?user_id=1")

    assert response.status_code == 200
    data = response.json()
    assert data["enriched"] is False
    assert data["genres"] == ["Drama"]


def test_edit_item_manual_metadata(client, mock_components):
    """PATCH /api/items/{db_id} forwards manual genres/tags/description."""
    updated_item = ContentItem(
        id="movie_1",
        db_id=7,
        title="Test Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.UNREAD,
        metadata={
            "genres": ["Drama"],
            "tags": ["slow-burn"],
            "description": "Hand written.",
        },
    )
    updated_item.enriched = True
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=updated_item)

    response = client.patch(
        "/api/items/7?user_id=1",
        json={
            "status": "unread",
            "genres": ["Drama"],
            "tags": ["slow-burn"],
            "description": "Hand written.",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["genres"] == ["Drama"]
    assert data["tags"] == ["slow-burn"]
    assert data["description"] == "Hand written."
    assert data["enriched"] is True

    mock_components["storage"].update_item_from_ui.assert_called_once_with(
        db_id=7,
        status="unread",
        rating=None,
        review=None,
        seasons_watched=None,
        genres=["Drama"],
        tags=["slow-burn"],
        description="Hand written.",
        user_id=1,
    )


def test_edit_item_without_manual_metadata_passes_none(client, mock_components):
    """PATCH without manual fields forwards None for genres/tags/description."""
    updated_item = ContentItem(
        id="movie_1",
        db_id=7,
        title="Test Movie",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.COMPLETED,
    )
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=updated_item)

    response = client.patch(
        "/api/items/7?user_id=1",
        json={"status": "completed", "rating": 4},
    )

    assert response.status_code == 200
    mock_components["storage"].update_item_from_ui.assert_called_once_with(
        db_id=7,
        status="completed",
        rating=4,
        review=None,
        seasons_watched=None,
        genres=None,
        tags=None,
        description=None,
        user_id=1,
    )


def test_edit_rejects_oversized_manual_metadata(client, mock_components):
    """PATCH /api/items/{db_id} rejects manual metadata above the model caps.

    Bounds the manual-edit fields at the API boundary: at most 50 genres and
    100 tags, each genre/tag string at most 100 chars, and a description at
    most 10000 chars. Each over-cap payload must 422 before any storage write.
    """
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)

    too_many_genres = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread", "genres": ["g"] * 51},
    )
    assert too_many_genres.status_code == 422
    assert too_many_genres.json()["detail"]

    genre_too_long = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread", "genres": ["x" * 101]},
    )
    assert genre_too_long.status_code == 422
    assert genre_too_long.json()["detail"]

    tag_too_long = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread", "tags": ["x" * 101]},
    )
    assert tag_too_long.status_code == 422
    assert tag_too_long.json()["detail"]

    description_too_long = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread", "description": "x" * 10001},
    )
    assert description_too_long.status_code == 422
    assert description_too_long.json()["detail"]

    too_many_tags = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread", "tags": ["t"] * 101},
    )
    assert too_many_tags.status_code == 422
    assert too_many_tags.json()["detail"]

    review_too_long = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "unread", "review": "x" * 10001},
    )
    assert review_too_long.status_code == 422
    assert review_too_long.json()["detail"]

    mock_components["storage"].update_item_from_ui.assert_not_called()


# ---------------------------------------------------------------------------
# GOG Exchange Endpoint Tests
# ---------------------------------------------------------------------------


class TestExchangeGogTokenEndpoint:
    """Tests for POST /api/gog/exchange endpoint security behavior."""

    def test_successful_exchange_saves_to_db(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Token is saved to DB (not config file) and never returned in response."""
        app_state.config["inputs"]["gog"] = {"enabled": True}

        with (
            patch("src.web.api.extract_gog_code", return_value="valid_code"),
            patch(
                "src.web.api.exchange_gog_tokens",
                return_value={
                    "access_token": "access123",
                    "refresh_token": "super_secret_token",
                },
            ),
            patch("src.web.api.save_gog_token") as mock_save,
        ):
            response = client.post(
                "/api/gog/exchange", json={"code_or_url": "valid_code"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "refresh_token" not in body
        assert "super_secret_token" not in str(body)
        mock_save.assert_called_once_with(
            mock_components["storage"], "super_secret_token"
        )

    def test_exchange_succeeds_with_readonly_config(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Regression test: GOG exchange succeeds even when config is read-only.

        Bug: Docker mounts config read-only, causing OSError when
        update_config_with_token tried to write. Now tokens go to DB.
        """
        app_state.config["inputs"]["gog"] = {"enabled": True}

        with (
            patch("src.web.api.extract_gog_code", return_value="valid_code"),
            patch(
                "src.web.api.exchange_gog_tokens",
                return_value={
                    "access_token": "access123",
                    "refresh_token": "super_secret_token",
                },
            ),
            patch("src.web.api.save_gog_token"),
        ):
            response = client.post(
                "/api/gog/exchange", json={"code_or_url": "valid_code"}
            )

        # No manual_setup fallback — always succeeds via DB
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "manual_setup" not in body

    def test_auth_error_returns_generic_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Auth failure returns generic 400 without leaking error details."""
        app_state.config["inputs"]["gog"] = {"enabled": True}

        with patch(
            "src.web.api.extract_gog_code",
            side_effect=GogAuthError("Internal details that must not leak"),
        ):
            response = client.post("/api/gog/exchange", json={"code_or_url": "bad"})

        assert response.status_code == 400
        body = response.json()
        assert body["detail"] == "GOG authentication failed"
        assert "Internal details" not in str(body)

    def test_unexpected_exception_returns_generic_500(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Unexpected exceptions return a generic 500 without leaking error details."""
        app_state.config["inputs"]["gog"] = {"enabled": True}

        with patch(
            "src.web.api.extract_gog_code",
            side_effect=RuntimeError("Internal database state is corrupt"),
        ):
            response = client.post("/api/gog/exchange", json={"code_or_url": "any"})

        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Unexpected error during GOG authentication"
        assert "Internal database state" not in str(body)

    def test_gog_not_enabled_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Endpoint rejects requests when GOG is not enabled."""
        app_state.config["inputs"]["gog"] = {"enabled": False}

        response = client.post("/api/gog/exchange", json={"code_or_url": "some_code"})

        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Pagination and Sorting Tests (8I)
# ---------------------------------------------------------------------------


class TestPaginationAndSorting:
    """Tests for pagination offset and sort_by query params on /api/items."""

    def test_offset_is_passed_to_storage(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?offset=10 passes offset to storage layer."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items?offset=10")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["offset"] == 10

    def test_offset_defaults_to_zero(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items without offset defaults to 0."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["offset"] == 0

    def test_sort_by_is_passed_to_storage(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?sort_by=rating passes sort_by to storage layer."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items?sort_by=rating")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["sort_by"] == "rating"

    def test_sort_by_defaults_to_title(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items without sort_by defaults to 'title'."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["sort_by"] == "title"

    def test_sort_by_invalid_value_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?sort_by=invalid returns 400 with error detail."""
        response = client.get("/api/items?sort_by=invalid")
        assert response.status_code == 400
        assert "Invalid sort_by" in response.json()["detail"]

    def test_sort_by_case_insensitive(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?sort_by=Rating is accepted (case insensitive)."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items?sort_by=Rating")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["sort_by"] == "rating"

    def test_sort_by_updated_at(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?sort_by=updated_at is a valid sort option."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items?sort_by=updated_at")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["sort_by"] == "updated_at"

    def test_sort_by_created_at(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?sort_by=created_at is a valid sort option."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items?sort_by=created_at")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["sort_by"] == "created_at"

    def test_offset_and_sort_by_combined(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?offset=5&sort_by=rating passes both params correctly."""
        mock_components["storage"].get_content_items = Mock(return_value=[])

        response = client.get("/api/items?offset=5&sort_by=rating&limit=20")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["offset"] == 5
        assert call_kwargs["sort_by"] == "rating"
        assert call_kwargs["limit"] == 20


class TestSearchParam:
    """Tests for the search query param on /api/items."""

    def test_search_is_passed_to_storage(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?search=dune forwards the term to storage."""
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=[]
        )

        response = client.get("/api/items?search=dune")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["search"] == "dune"

    def test_search_defaults_to_none(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items without search forwards search=None to storage."""
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=[]
        )

        response = client.get("/api/items")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["search"] is None

    def test_search_combined_with_type_filter(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?search=dune&type=book forwards both to storage."""
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=[]
        )

        response = client.get("/api/items?search=dune&type=book")
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["search"] == "dune"
        assert call_kwargs["content_type"] == ContentType.BOOK

    def test_search_returns_matching_items(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/items?search=dune returns the items storage matched."""
        mock_items = [
            ContentItem(
                id="1",
                title="Dune",
                author="Frank Herbert",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                source="goodreads",
            )
        ]
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=mock_items
        )

        response = client.get("/api/items?search=dune")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Dune"


# ---------------------------------------------------------------------------
# Count > max_count validation (8J)
# ---------------------------------------------------------------------------


def test_recommendations_count_exceeds_max_returns_400(client, mock_components):
    """GET /api/recommendations returns 400 when count exceeds config max_count.

    The recommendations endpoint validates the requested count against the
    max_count value from the recommendations config section (default: 20).
    """
    # Set a low max_count in config
    app_state.config["recommendations"] = {"max_count": 5}

    response = client.get("/api/recommendations?type=book&count=10")
    assert response.status_code == 400
    assert "exceeds the maximum allowed" in response.json()["detail"]


def test_stream_recommendations_count_exceeds_max_returns_400(client, mock_components):
    """GET /api/recommendations/stream returns 400 when count exceeds config max_count.

    The streaming endpoint applies the same max_count enforcement as the
    non-streaming endpoint.
    """
    app_state.config["recommendations"] = {"max_count": 5}

    response = client.get("/api/recommendations/stream?type=book&count=10")
    assert response.status_code == 400
    assert "exceeds the maximum allowed" in response.json()["detail"]


def test_recommendations_count_at_max_is_allowed(client, mock_components):
    """GET /api/recommendations allows count == max_count (boundary)."""
    app_state.config["recommendations"] = {"max_count": 5}
    mock_components["engine"].generate_recommendations.return_value = []
    mock_components["storage"].get_user_preference_config.return_value = None
    mock_components["storage"].get_completed_items.return_value = []

    response = client.get("/api/recommendations?type=book&count=5")
    assert response.status_code == 200


def _rec_dict(item: ContentItem) -> dict:
    """Wrap a ContentItem in the recommendation dict shape the engine emits."""
    return {
        "item": item,
        "score": 0.85,
        "similarity_score": 0.8,
        "preference_score": 0.7,
        "reasoning": "Rule-based reasoning",
        "score_breakdown": {"genre_match": 0.9},
        "contributing_items": [],
    }


def test_recommendations_tv_season_payload_includes_db_id(client, mock_components):
    """GET /api/recommendations serializes a TV season rec with a non-null db_id.

    A season-expanded TV candidate carries its parent show's db_id (id is
    ``tvdb:42:s1`` but db_id is the show-level row).  The response must surface
    that db_id so the card renders the Mark complete / Ignore actions.
    """
    season_item = ContentItem(
        id="tvdb:42:s1",
        db_id=42,
        title="The Expanse (Season 1)",
        author=None,
        content_type=ContentType.TV_SHOW,
        status=ConsumptionStatus.UNREAD,
        parent_id="tvdb:42",
    )
    mock_components["engine"].generate_recommendations.return_value = [
        _rec_dict(season_item)
    ]
    mock_components["storage"].get_user_preference_config.return_value = None

    response = client.get("/api/recommendations?type=tv_show&count=5")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["title"] == "The Expanse (Season 1)"
    assert body[0]["db_id"] == 42


def test_recommendations_non_tv_payload_preserves_db_id(client, mock_components):
    """GET /api/recommendations keeps a book/movie/game rec's own db_id.

    Non-TV content is not season-expanded, so the payload db_id is the item's
    own library id, unchanged by the TV fix.
    """
    book_item = ContentItem(
        id="ol:1",
        db_id=7,
        title="Foundation",
        author="Isaac Asimov",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    mock_components["engine"].generate_recommendations.return_value = [
        _rec_dict(book_item)
    ]
    mock_components["storage"].get_user_preference_config.return_value = None

    response = client.get("/api/recommendations?type=book&count=5")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["db_id"] == 7


# ---------------------------------------------------------------------------
# Export Endpoint Tests (8E)
# ---------------------------------------------------------------------------


class TestExportEndpoint:
    """Tests for GET /api/items/export HTTP endpoint wiring."""

    def test_csv_export(self, client: TestClient, mock_components: dict) -> None:
        """CSV export returns attachment response with correct media type."""
        mock_items = [
            ContentItem(
                id="1",
                title="Test Book",
                author="Author",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genre": "Fantasy"},
            ),
        ]
        mock_components["storage"].get_content_items = Mock(return_value=mock_items)

        response = client.get("/api/items/export?type=book&format=csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert 'filename="books.csv"' in response.headers["content-disposition"]
        assert "Test Book" in response.text

    def test_json_export(self, client: TestClient, mock_components: dict) -> None:
        """JSON export returns attachment response with correct media type."""
        mock_items = [
            ContentItem(
                id="1",
                title="Test Movie",
                author="Director",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                metadata={},
            ),
        ]
        mock_components["storage"].get_content_items = Mock(return_value=mock_items)

        response = client.get("/api/items/export?type=movie&format=json")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        assert 'filename="movies.json"' in response.headers["content-disposition"]
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Movie"

    def test_invalid_format_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Invalid export format returns 400 error."""
        response = client.get("/api/items/export?type=book&format=xml")

        assert response.status_code == 400
        assert "Invalid format" in response.json()["detail"]

    def test_invalid_content_type_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Invalid content type returns 400 error."""
        response = client.get("/api/items/export?type=podcast&format=csv")

        assert response.status_code == 400
        assert "Invalid content type" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Update Endpoint 409 Conflict Tests (8F)
# ---------------------------------------------------------------------------


class TestUpdateEndpoint409Conflict:
    """POST /api/update returns 409 when the SAME source is already syncing.

    Distinct sources can run concurrently after issue #45, so the 409
    only fires when ``is_running(<source_label>)`` reports True.
    """

    def test_update_returns_409_when_same_source_already_running(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """409 surfaces start_sync's atomic check-and-set rejection."""
        with patch("src.web.api.get_sync_manager") as mock_get_sync_manager:
            mock_manager = Mock(spec=SyncManager)
            mock_manager.start_sync.return_value = (
                False,
                "Sync already in progress for Goodreads",
            )
            mock_get_sync_manager.return_value = mock_manager

            with patch(
                "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
                return_value=[],
            ):
                response = client.post("/api/update", json={"source": "goodreads"})

            assert response.status_code == 409
            detail = response.json()["detail"]
            assert "Sync already in progress" in detail
            assert "Goodreads" in detail
            assert mock_manager.start_sync.call_args.args[0] == "Goodreads"

    def test_update_allows_different_sources_concurrently(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """A second source is accepted while a different source is running.

        Plants a real RUNNING job for Steam in the global SyncManager
        before triggering a Goodreads sync. The endpoint must reject
        only when the SAME label is running — different labels return
        200 even with another sync still in flight.
        """
        # Plant a running Steam job so the manager genuinely has work in
        # progress when the second POST lands.
        from src.web.sync_manager import SyncJob, SyncStatus, get_sync_manager

        manager = get_sync_manager()
        with patch("src.web.sync_manager.threading.Thread"):
            # Start Steam to keep the daemon thread out of the way; the
            # real start_sync transition gives us a RUNNING job.
            manager.start_sync(source="Steam", sync_function=lambda _job: 0)
        assert manager.is_running("Steam") is True

        with patch(
            "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
            return_value=[],
        ):
            # Drop the captured execute_multi_source_sync into a no-op so
            # the second sync's daemon doesn't try to actually run.
            with patch(
                "src.web.api.execute_multi_source_sync",
                return_value=[SyncJob(source="Goodreads", status=SyncStatus.RUNNING)],
            ):
                response = client.post("/api/update", json={"source": "goodreads"})

        assert response.status_code == 200, response.text
        assert "Sync started" in response.json()["message"]
        # Manager now tracks both jobs; the Steam one is still running
        # and the Goodreads one was added on top.
        assert manager.is_running("Steam") is True
        assert "Goodreads" in {job["source"] for job in manager.get_status()["jobs"]}


class TestUpdateEndpointParallelSync:
    """Tests for max_workers wiring in POST /api/update (issue #45).

    The endpoint must read ``config['sync']['max_workers']`` and forward
    it to ``execute_multi_source_sync`` so the underlying ThreadPoolExecutor
    sizes correctly. ``GET /api/sync/status`` must include the per-source
    progress map in its response so the UI can render parallel progress.
    """

    @staticmethod
    def _make_capture(
        captured_kwargs: dict, completion: threading.Event
    ) -> Callable[..., list[SyncResult]]:
        """Build a fake execute_multi_source_sync that signals completion.

        The endpoint hands the real call off to a daemon thread, so the
        test must wait for that thread to invoke the patched function
        before asserting on captured kwargs. A ``threading.Event`` set
        from inside the fake is deterministic — no time-budget polling.
        """

        def fake_execute(**kwargs: object) -> list:
            try:
                captured_kwargs.update(kwargs)
                sources_arg = kwargs.get("sources") or []
                return [
                    SyncResult(source_name=plugin.display_name)
                    for plugin, _config in sources_arg  # type: ignore[misc]
                ]
            finally:
                completion.set()

        return fake_execute

    def test_config_max_workers_forwarded_to_executor(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """config['sync']['max_workers'] is passed to execute_multi_source_sync."""
        app_state.config["sync"] = {"max_workers": 7}

        captured_kwargs: dict = {}
        completion = threading.Event()
        with (
            patch(
                "src.web.api.execute_multi_source_sync",
                side_effect=self._make_capture(captured_kwargs, completion),
            ),
            patch(
                "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post("/api/update", json={"source": "all"})
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200
        assert captured_kwargs.get("max_workers") == 7

    def test_default_max_workers_is_four_when_unset(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """No config['sync'] block => max_workers defaults to 4."""
        app_state.config.pop("sync", None)

        captured_kwargs: dict = {}
        completion = threading.Event()
        with (
            patch(
                "src.web.api.execute_multi_source_sync",
                side_effect=self._make_capture(captured_kwargs, completion),
            ),
            patch(
                "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post("/api/update", json={"source": "all"})
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200
        assert captured_kwargs.get("max_workers") == 4

    def test_request_body_max_workers_overrides_config(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """max_workers in the POST body overrides config (CLI parity)."""
        app_state.config["sync"] = {"max_workers": 2}

        captured_kwargs: dict = {}
        completion = threading.Event()
        with (
            patch(
                "src.web.api.execute_multi_source_sync",
                side_effect=self._make_capture(captured_kwargs, completion),
            ),
            patch(
                "src.ingestion.sources.goodreads.GoodreadsPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post(
                "/api/update", json={"source": "all", "max_workers": 8}
            )
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200, response.text
        assert captured_kwargs.get("max_workers") == 8

    def test_request_body_max_workers_above_ceiling_rejected(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Pydantic le=MAX_WORKERS_CEILING rejects max_workers above the ceiling."""
        response = client.post("/api/update", json={"source": "all", "max_workers": 99})
        assert response.status_code == 422

    def test_sync_status_includes_per_source_progress(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/sync/status emits a `jobs[]` array with per-source progress."""
        manager = get_sync_manager()
        # Patch Thread so start_sync's daemon thread never runs and the
        # planted per-source progress survives until /sync/status is hit.
        with patch("src.web.sync_manager.threading.Thread"):
            success, _ = manager.start_sync(
                source="All Sources", sync_function=lambda _job: 0
            )
        assert success

        manager.update_progress(
            source="All Sources",
            items_processed=12,
            total_items=20,
            current_item="Book 12",
            current_source="goodreads",
        )
        manager.update_progress(
            source="All Sources",
            items_processed=3,
            total_items=10,
            current_item="Game 3",
            current_source="steam",
        )

        response = client.get("/api/sync/status")
        assert response.status_code == 200
        body = response.json()
        # New shape: top-level status + jobs[] (multi-job model).
        assert body["status"] == "running"
        assert len(body["jobs"]) == 1
        sources = body["jobs"][0]["sources"]
        assert len(sources) == 2
        assert [entry["source"] for entry in sources] == ["goodreads", "steam"]
        by_source = {entry["source"]: entry for entry in sources}
        assert by_source["goodreads"]["items_processed"] == 12
        assert by_source["goodreads"]["total_items"] == 20
        assert by_source["goodreads"]["current_item"] == "Book 12"
        assert by_source["goodreads"]["progress_percent"] == 60
        assert by_source["steam"]["items_processed"] == 3
        assert by_source["steam"]["progress_percent"] == 30

    def test_sync_status_idle_response_shape(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """GET /api/sync/status with no jobs returns the empty-list shape."""
        # Ensure no leftover jobs from earlier tests in this suite.
        from src.web.sync_manager import reset_sync_manager

        reset_sync_manager()

        response = client.get("/api/sync/status")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "idle"
        assert body["jobs"] == []

    def test_sync_status_lists_multiple_concurrent_jobs(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Two jobs keyed by different sources are both reported,
        regardless of insertion order — proves the sort is applied."""
        manager = get_sync_manager()
        with patch("src.web.sync_manager.threading.Thread"):
            # Insert in REVERSE alphabetical order so the assertion
            # below proves sorting, not insertion order.
            ok_steam, _ = manager.start_sync(
                source="Steam", sync_function=lambda _job: 0
            )
            ok_goodreads, _ = manager.start_sync(
                source="Goodreads", sync_function=lambda _job: 0
            )
        assert ok_steam and ok_goodreads

        response = client.get("/api/sync/status")
        assert response.status_code == 200
        body = response.json()
        sources_in_play = [job["source"] for job in body["jobs"]]
        assert sources_in_play == ["Goodreads", "Steam"]
        assert body["status"] == "running"


# ---------------------------------------------------------------------------
# SSE Streaming Endpoint Tests (8B)
# ---------------------------------------------------------------------------


def _parse_sse_events(response_text: str) -> list[dict]:
    """Parse SSE text into a list of JSON event dicts."""
    events = []
    for line in response_text.strip().splitlines():
        if line.startswith("data: "):
            payload = line[len("data: ") :]
            events.append(json.loads(payload))
    return events


class TestSSEStreamingEndpoint:
    """Tests for GET /api/recommendations/stream SSE endpoint."""

    def _make_recommendation(
        self,
        item_id: str = "1",
        title: str = "Test Book",
        author: str = "Author A",
    ) -> dict:
        """Create a mock recommendation dict matching engine output."""
        item = ContentItem(
            id=item_id,
            db_id=int(item_id),
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        return {
            "item": item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Rule-based reasoning",
            "score_breakdown": {"genre_match": 0.9},
            "contributing_items": [],
        }

    def test_phase1_recommendations_event(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream emits a phase 1 'recommendations' event with items."""
        rec = self._make_recommendation()
        mock_components["engine"].generate_recommendations.return_value = [rec]
        mock_components["engine"].generate_blurb_for_item.return_value = None
        mock_components["storage"].get_user_preference_config.return_value = None
        mock_components["storage"].get_completed_items.return_value = []

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=1"
        ) as response:
            assert response.status_code == 200
            body = response.read().decode()

        events = _parse_sse_events(body)
        rec_events = [e for e in events if e["type"] == "recommendations"]
        assert len(rec_events) == 1
        items = rec_events[0]["items"]
        assert len(items) == 1
        assert items[0]["title"] == "Test Book"
        assert items[0]["llm_reasoning"] is None
        assert items[0]["score"] == 0.85
        assert items[0]["score_breakdown"] == {"genre_match": 0.9}

    def test_phase1_tv_season_includes_db_id(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE phase 1 serializes a TV season rec with its parent show db_id.

        The streaming path shares ``_recommendation_payload`` with the sync
        endpoint, so a season-expanded candidate (id ``tvdb:42:s1``, db_id 42)
        must stream with a non-null db_id and keep the card actionable.
        """
        season_item = ContentItem(
            id="tvdb:42:s1",
            db_id=42,
            title="The Expanse (Season 1)",
            author=None,
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            parent_id="tvdb:42",
        )
        rec = {
            "item": season_item,
            "score": 0.85,
            "similarity_score": 0.8,
            "preference_score": 0.7,
            "reasoning": "Rule-based reasoning",
            "score_breakdown": {"genre_match": 0.9},
            "contributing_items": [],
        }
        mock_components["engine"].generate_recommendations.return_value = [rec]
        mock_components["engine"].generate_blurb_for_item.return_value = None
        mock_components["storage"].get_user_preference_config.return_value = None
        mock_components["storage"].get_completed_items.return_value = []

        with client.stream(
            "GET", "/api/recommendations/stream?type=tv_show&count=1"
        ) as response:
            body = response.read().decode()

        events = _parse_sse_events(body)
        rec_events = [e for e in events if e["type"] == "recommendations"]
        assert len(rec_events) == 1
        items = rec_events[0]["items"]
        assert items[0]["db_id"] == 42

    def test_blurb_events_streamed(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream emits 'blurb' events as LLM generates them."""
        rec = self._make_recommendation()
        mock_components["engine"].generate_recommendations.return_value = [rec]
        mock_components["engine"].generate_blurb_for_item.return_value = (
            "This is a great match."
        )
        mock_components["storage"].get_user_preference_config.return_value = None
        mock_components["storage"].get_completed_items.return_value = []

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=1"
        ) as response:
            body = response.read().decode()

        events = _parse_sse_events(body)
        blurb_events = [e for e in events if e["type"] == "blurb"]
        assert len(blurb_events) == 1
        assert blurb_events[0]["index"] == 0
        assert blurb_events[0]["llm_reasoning"] == "This is a great match."

    def test_done_event_is_final(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream ends with a 'done' event."""
        rec = self._make_recommendation()
        mock_components["engine"].generate_recommendations.return_value = [rec]
        mock_components["engine"].generate_blurb_for_item.return_value = None
        mock_components["storage"].get_user_preference_config.return_value = None
        mock_components["storage"].get_completed_items.return_value = []

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=1"
        ) as response:
            body = response.read().decode()

        events = _parse_sse_events(body)
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1
        # done should be the last event
        assert events[-1]["type"] == "done"

    def test_error_event_on_engine_failure(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream emits an 'error' event when the engine raises."""
        mock_components["engine"].generate_recommendations.side_effect = RuntimeError(
            "Engine failure"
        )
        mock_components["storage"].get_user_preference_config.return_value = None

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=1"
        ) as response:
            body = response.read().decode()

        events = _parse_sse_events(body)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "Failed to generate recommendations" in error_events[0]["message"]

    def test_invalid_content_type_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream endpoint returns 400 for invalid content type."""
        response = client.get("/api/recommendations/stream?type=invalid&count=1")
        assert response.status_code == 400
        assert "Invalid content type" in response.json()["detail"]

    def test_empty_recommendations_sends_done(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream sends empty items + done when no recommendations found."""
        mock_components["engine"].generate_recommendations.return_value = []
        mock_components["storage"].get_user_preference_config.return_value = None

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=5"
        ) as response:
            body = response.read().decode()

        events = _parse_sse_events(body)
        assert len(events) == 2
        assert events[0]["type"] == "recommendations"
        assert events[0]["items"] == []
        assert events[1]["type"] == "done"

    def test_blurb_failure_skips_event(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream does not emit a blurb event when blurb generation raises."""
        rec = self._make_recommendation()
        mock_components["engine"].generate_recommendations.return_value = [rec]
        mock_components["engine"].generate_blurb_for_item.side_effect = RuntimeError(
            "LLM unavailable"
        )
        mock_components["storage"].get_user_preference_config.return_value = None
        mock_components["storage"].get_completed_items.return_value = []

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=1"
        ) as response:
            body = response.read().decode()

        events = _parse_sse_events(body)
        blurb_events = [e for e in events if e["type"] == "blurb"]
        assert len(blurb_events) == 0
        # Should still get recommendations and done
        assert events[0]["type"] == "recommendations"
        assert events[-1]["type"] == "done"


class TestConfigReload:
    """Tests for POST /api/config/reload."""

    def test_reload_success(self, client, mock_components):
        """Successful config reload returns 200."""
        with patch("src.web.api.reload_config", return_value=True):
            response = client.post("/api/config/reload")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_reload_failure(self, client, mock_components):
        """Failed config reload returns 500."""
        with patch("src.web.api.reload_config", return_value=False):
            response = client.post("/api/config/reload")
        assert response.status_code == 500


class TestGogStatus:
    """Tests for GET /api/gog/status."""

    def test_gog_enabled_connected(self, client, mock_components):
        """GOG enabled and connected returns correct flags."""
        with (
            patch("src.web.api.is_gog_enabled", return_value=True),
            patch("src.web.api.has_gog_token", return_value=True),
            patch("src.web.api.get_gog_auth_url", return_value="https://auth.gog.com"),
        ):
            response = client.get("/api/gog/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["connected"] is True
        assert data["auth_url"] == "https://auth.gog.com"

    def test_gog_disabled(self, client, mock_components):
        """GOG disabled returns enabled=False and no auth_url."""
        with (
            patch("src.web.api.is_gog_enabled", return_value=False),
            patch("src.web.api.has_gog_token", return_value=False),
        ):
            response = client.get("/api/gog/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["connected"] is False
        assert data["auth_url"] is None

    def test_gog_status_no_config(self, client, mock_components):
        """No config returns 500."""
        app_state.config = None
        response = client.get("/api/gog/status")
        assert response.status_code == 500


class TestExchangeEpicTokenEndpoint:
    """Tests for POST /api/epic/exchange endpoint security behavior."""

    def test_successful_exchange_saves_to_db(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Token is saved to DB and never returned in response."""
        app_state.config["inputs"]["epic_games"] = {"enabled": True}

        with (
            patch("src.web.api.extract_epic_code", return_value="valid_code"),
            patch(
                "src.web.api.exchange_epic_tokens",
                return_value={
                    "access_token": "access123",
                    "refresh_token": "super_secret_token",
                },
            ),
            patch("src.web.api.save_epic_token") as mock_save,
        ):
            response = client.post(
                "/api/epic/exchange", json={"code_or_json": "valid_code"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert set(body.keys()) == {"success", "message"}
        assert "super_secret_token" not in str(body)
        assert "access123" not in str(body)
        mock_save.assert_called_once_with(
            mock_components["storage"], "super_secret_token"
        )

    def test_auth_error_returns_generic_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Auth failure returns generic 400 without leaking error details."""
        app_state.config["inputs"]["epic_games"] = {"enabled": True}

        with patch(
            "src.web.api.extract_epic_code",
            side_effect=EpicAuthError("Internal details that must not leak"),
        ):
            response = client.post("/api/epic/exchange", json={"code_or_json": "bad"})

        assert response.status_code == 400
        body = response.json()
        assert body["detail"] == "Epic Games authentication failed"
        assert "Internal details" not in str(body)

    def test_save_token_failure_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """DB save failure returns generic 400."""
        app_state.config["inputs"]["epic_games"] = {"enabled": True}

        with (
            patch("src.web.api.extract_epic_code", return_value="valid_code"),
            patch(
                "src.web.api.exchange_epic_tokens",
                return_value={
                    "access_token": "access123",
                    "refresh_token": "refresh456",
                },
            ),
            patch(
                "src.web.api.save_epic_token",
                side_effect=EpicAuthError("DB write failed"),
            ),
        ):
            response = client.post(
                "/api/epic/exchange", json={"code_or_json": "valid_code"}
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Epic Games authentication failed"

    def test_epic_not_enabled_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Requesting exchange when Epic is disabled returns 400."""
        app_state.config["inputs"]["epic_games"] = {"enabled": False}

        response = client.post("/api/epic/exchange", json={"code_or_json": "some_code"})

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "Epic Games is not enabled in the current configuration."
        )

    def test_no_storage_returns_500(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Missing storage returns 500."""
        app_state.config["inputs"]["epic_games"] = {"enabled": True}
        app_state.storage = None

        response = client.post("/api/epic/exchange", json={"code_or_json": "some_code"})

        assert response.status_code == 500
        assert response.json()["detail"] == "Storage not initialized"

    def test_unexpected_error_returns_500(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Unexpected errors produce a generic 500 without leaking internals."""
        app_state.config["inputs"]["epic_games"] = {"enabled": True}

        with (
            patch("src.web.api.extract_epic_code", return_value="valid_code"),
            patch(
                "src.web.api.exchange_epic_tokens",
                side_effect=RuntimeError("unexpected"),
            ),
        ):
            response = client.post(
                "/api/epic/exchange", json={"code_or_json": "valid_code"}
            )

        assert response.status_code == 500
        body = response.json()
        assert body["detail"] == "Unexpected error during Epic Games authentication"
        assert "RuntimeError" not in str(body)

    def test_no_config_returns_500(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Missing config returns 500."""
        app_state.config = None
        response = client.post("/api/epic/exchange", json={"code_or_json": "some_code"})
        assert response.status_code == 500
        assert response.json()["detail"] == "Config not initialized"


class TestEpicStatus:
    """Tests for GET /api/epic/status."""

    def test_epic_enabled_connected(self, client, mock_components):
        """Epic enabled and connected returns correct flags."""
        with (
            patch("src.web.api.is_epic_enabled", return_value=True),
            patch("src.web.api.has_epic_token", return_value=True),
            patch(
                "src.web.api.get_epic_auth_url",
                return_value="https://www.epicgames.com/id/login?test",
            ),
        ):
            response = client.get("/api/epic/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["connected"] is True
        assert data["auth_url"] == "https://www.epicgames.com/id/login?test"

    def test_epic_enabled_not_connected(self, client, mock_components):
        """Epic enabled but not connected returns auth_url for OAuth flow."""
        with (
            patch("src.web.api.is_epic_enabled", return_value=True),
            patch("src.web.api.has_epic_token", return_value=False),
            patch(
                "src.web.api.get_epic_auth_url",
                return_value="https://www.epicgames.com/id/login?test",
            ),
        ):
            response = client.get("/api/epic/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["connected"] is False
        assert data["auth_url"] == "https://www.epicgames.com/id/login?test"

    def test_epic_disabled(self, client, mock_components):
        """Epic disabled returns enabled=False and no auth_url."""
        with (
            patch("src.web.api.is_epic_enabled", return_value=False),
            patch("src.web.api.has_epic_token", return_value=False),
        ):
            response = client.get("/api/epic/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["connected"] is False
        assert data["auth_url"] is None

    def test_epic_enabled_auth_url_failure_returns_null(self, client, mock_components):
        """When get_epic_auth_url raises, status returns 200 with auth_url=None."""
        with (
            patch("src.web.api.is_epic_enabled", return_value=True),
            patch("src.web.api.has_epic_token", return_value=False),
            patch(
                "src.web.api.get_epic_auth_url",
                side_effect=RuntimeError("EPCAPI broken"),
            ),
        ):
            response = client.get("/api/epic/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["connected"] is False
        assert data["auth_url"] is None

    def test_epic_status_no_config(self, client, mock_components):
        """No config returns 500."""
        app_state.config = None
        response = client.get("/api/epic/status")
        assert response.status_code == 500
        assert response.json()["detail"] == "Config not initialized"


class TestExchangeEpicTokenEndpointRegression:
    """Guards against token persistence writing to config files in Docker."""

    def test_exchange_succeeds_with_readonly_config_regression(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Regression: Epic exchange succeeds even when config is read-only.

        Bug reported: Docker mounts config as a read-only volume. OAuth
        completion failed with OSError in Docker environments.
        Root cause: token persistence used config file write instead of DB.
        Fix: tokens are now saved exclusively via save_epic_token() to the
        credential database, which is never a read-only mount.
        """
        app_state.config["inputs"]["epic_games"] = {"enabled": True}

        with (
            patch("src.web.api.extract_epic_code", return_value="valid_code"),
            patch(
                "src.web.api.exchange_epic_tokens",
                return_value={
                    "access_token": "access123",
                    "refresh_token": "super_secret_token",
                },
            ),
            patch("src.web.api.save_epic_token") as mock_save,
        ):
            response = client.post(
                "/api/epic/exchange", json={"code_or_json": "valid_code"}
            )

        # Token goes to DB via save_epic_token, not to the config file.
        # The endpoint has no config-write path — this is the fix.
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        mock_save.assert_called_once_with(
            mock_components["storage"], "super_secret_token"
        )


class TestEnrichmentErrorPaths:
    """Tests for enrichment endpoint error paths."""

    def test_stop_enrichment_not_running(self, client, mock_components):
        """Stopping when not running returns 400."""
        with patch("src.web.api.get_enrichment_manager") as mock_get:
            manager = Mock(spec=WebEnrichmentManager)
            manager.stop_enrichment.return_value = (False, "No enrichment running")
            mock_get.return_value = manager
            response = client.post("/api/enrichment/stop")
        assert response.status_code == 400

    def test_reset_enrichment_no_storage(self, client, mock_components):
        """Reset when storage not available returns 500."""
        app_state.storage = None
        response = client.post(
            "/api/enrichment/reset",
            json={"reset_type": "all"},
        )
        assert response.status_code == 500


class TestIgnoreItem500:
    """Test PATCH /items/{id}/ignore 500 path."""

    def test_set_ignored_fails(self, client, mock_components):
        """set_item_ignored returning False produces 500."""
        mock_item = ContentItem(
            id="1",
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        mock_components["storage"].get_content_item.return_value = mock_item
        mock_components["storage"].set_item_ignored.return_value = False

        response = client.patch(
            "/api/items/1/ignore",
            json={"ignored": True},
        )
        assert response.status_code == 500


class TestItemToResponseInvalidSeasons:
    """Test _item_to_response with non-numeric seasons."""

    def test_invalid_seasons_returns_none(self, client, mock_components):
        """Non-numeric seasons metadata should not crash."""
        item = ContentItem(
            id="tv1",
            title="Test Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": "invalid"},
        )
        result = _item_to_response(item)
        assert result.total_seasons is None


class TestAuthDisconnectEndpoints:
    """Tests for DELETE /api/gog/token and /api/epic/token (matches CLI auth disconnect)."""

    def test_gog_disconnect_success(self, client, mock_components):
        """DELETE /api/gog/token removes stored refresh token."""
        storage = mock_components["storage"]
        storage.delete_credential.return_value = True

        response = client.delete("/api/gog/token")

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "GOG disconnected."}
        storage.delete_credential.assert_called_once_with(1, "gog", "refresh_token")

    def test_gog_disconnect_not_connected(self, client, mock_components):
        """DELETE /api/gog/token returns 404 when no credential exists."""
        mock_components["storage"].delete_credential.return_value = False

        response = client.delete("/api/gog/token")

        assert response.status_code == 404

    def test_gog_disconnect_custom_user_id(self, client, mock_components):
        """user_id query parameter is forwarded to storage."""
        storage = mock_components["storage"]
        storage.delete_credential.return_value = True

        response = client.delete("/api/gog/token?user_id=5")

        assert response.status_code == 200
        storage.delete_credential.assert_called_once_with(5, "gog", "refresh_token")

    def test_epic_disconnect_success(self, client, mock_components):
        """DELETE /api/epic/token removes stored Epic refresh token."""
        storage = mock_components["storage"]
        storage.delete_credential.return_value = True

        response = client.delete("/api/epic/token")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "Epic Games disconnected.",
        }
        storage.delete_credential.assert_called_once_with(
            1, "epic_games", "refresh_token"
        )

    def test_epic_disconnect_not_connected(self, client, mock_components):
        """DELETE /api/epic/token returns 404 when no credential exists."""
        mock_components["storage"].delete_credential.return_value = False

        response = client.delete("/api/epic/token")

        assert response.status_code == 404

    def test_trakt_disconnect_success(self, client, mock_components):
        """DELETE /api/trakt/token removes the stored Trakt refresh token."""
        storage = mock_components["storage"]
        storage.delete_credential.return_value = True

        response = client.delete("/api/trakt/token")

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Trakt disconnected."}
        storage.delete_credential.assert_called_once_with(1, "trakt", "refresh_token")

    def test_trakt_disconnect_not_connected(self, client, mock_components):
        """DELETE /api/trakt/token returns 404 when no credential exists."""
        mock_components["storage"].delete_credential.return_value = False

        response = client.delete("/api/trakt/token")

        assert response.status_code == 404


class TestTraktStatus:
    """Tests for GET /api/trakt/status."""

    def test_enabled_and_connected(self, client, mock_components) -> None:
        """Configured client creds + stored token returns enabled+connected."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.web.api.is_trakt_connected", return_value=True),
        ):
            response = client.get("/api/trakt/status")

        assert response.status_code == 200
        assert response.json() == {"enabled": True, "connected": True}

    def test_not_configured(self, client, mock_components) -> None:
        """Missing client creds returns enabled=False, connected=False."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                side_effect=TraktAuthError("not configured"),
            ),
            patch("src.web.api.is_trakt_connected", return_value=False),
        ):
            response = client.get("/api/trakt/status")

        assert response.status_code == 200
        assert response.json() == {"enabled": False, "connected": False}

    def test_stored_token_but_creds_removed_is_not_connected(
        self, client, mock_components
    ) -> None:
        """A stored token with unresolvable creds reports connected=False.

        If client credentials are removed after connecting, the source can no
        longer be used, so the status must stay coherent: not enabled implies
        not connected, even though a refresh token is still in storage.
        """
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                side_effect=TraktAuthError("not configured"),
            ),
            patch("src.web.api.is_trakt_connected", return_value=True),
        ):
            response = client.get("/api/trakt/status")

        assert response.status_code == 200
        assert response.json() == {"enabled": False, "connected": False}

    def test_no_storage_returns_not_connected(self, client, mock_components) -> None:
        """Status degrades to connected=False (not a 500) when storage is None."""
        app_state.storage = None

        with patch(
            "src.web.api.resolve_trakt_client_credentials",
            side_effect=TraktAuthError("not configured"),
        ):
            response = client.get("/api/trakt/status")

        assert response.status_code == 200
        assert response.json() == {"enabled": False, "connected": False}


class TestTraktStartDeviceFlow:
    """Tests for POST /api/trakt/start-device-flow."""

    def test_returns_user_code_and_url(self, client, mock_components) -> None:
        """Start returns the user code/verification URL, never the secret."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.start_device_auth_flow",
                return_value={
                    "device_code": "dev123",
                    "user_code": "ABCD1234",
                    "verification_url": "https://trakt.tv/activate",
                    "expires_in": 600,
                    "interval": 5,
                },
            ),
        ):
            response = client.post("/api/trakt/start-device-flow")

        assert response.status_code == 200
        data = response.json()
        assert data == {
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "device_code": "dev123",
            "expires_in": 600,
            "interval": 5,
        }
        assert "secret" not in response.text

    def test_not_configured_returns_400(self, client, mock_components) -> None:
        """Start returns 400 with a generic message when creds are missing.

        The raw resolver error (which can name config internals) must never
        reach the client; only the generic message is surfaced.
        """
        with patch(
            "src.web.api.resolve_trakt_client_credentials",
            side_effect=TraktAuthError("Trakt is not configured."),
        ):
            response = client.post("/api/trakt/start-device-flow")

        assert response.status_code == 400
        assert response.json()["detail"] == "Trakt authentication failed"

    def test_no_storage_returns_500(self, client, mock_components) -> None:
        """Start returns 500 'Storage not initialized' when storage is None."""
        app_state.storage = None

        response = client.post("/api/trakt/start-device-flow")

        assert response.status_code == 500
        assert response.json()["detail"] == "Storage not initialized"


class TestTraktPollDeviceApproval:
    """Tests for POST /api/trakt/poll-device-approval."""

    def test_success_saves_token(self, client, mock_components) -> None:
        """A SUCCESS poll saves the refresh token and reports connected."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.SUCCESS, "refresh-xyz"),
            ),
            patch("src.web.api.save_trakt_token") as mock_save,
        ):
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
            )

        assert response.status_code == 200
        assert response.json()["connected"] is True
        mock_save.assert_called_once_with(
            mock_components["storage"], "refresh-xyz", user_id=1
        )

    def test_pending_returns_status(self, client, mock_components) -> None:
        """A PENDING poll returns connected=False with the status."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.PENDING),
            ),
            patch("src.web.api.save_trakt_token") as mock_save,
        ):
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False
        assert data["status"] == "pending"
        mock_save.assert_not_called()

    def test_invalid_device_code_returns_400(self, client, mock_components) -> None:
        """A poll error (e.g. invalid device code) returns 400."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.poll_device_token",
                side_effect=TraktAuthError("invalid"),
            ),
        ):
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "badbadbad1"}
            )

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "status",
        [
            DevicePollStatus.SLOW_DOWN,
            DevicePollStatus.EXPIRED,
            DevicePollStatus.DENIED,
        ],
    )
    def test_non_terminal_statuses_return_message(
        self, client, mock_components, status
    ) -> None:
        """SLOW_DOWN/EXPIRED/DENIED polls return connected=False with a message.

        The endpoint must surface a human-readable message for every documented
        device-poll status, not just PENDING — the frontend renders it verbatim.
        """
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.poll_device_token",
                return_value=DevicePollResult(status),
            ),
            patch("src.web.api.save_trakt_token") as mock_save,
        ):
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["connected"] is False
        assert data["status"] == status.value
        assert isinstance(data["message"], str) and data["message"]
        mock_save.assert_not_called()

    def test_success_without_refresh_token_returns_500(
        self, client, mock_components
    ) -> None:
        """A SUCCESS result missing a refresh token fails closed with a 500.

        The endpoint must not save an empty credential or 200 a non-connection;
        an explicit check (not a stripped assert) guards this.
        """
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.SUCCESS, None),
            ),
            patch("src.web.api.save_trakt_token") as mock_save,
        ):
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
            )

        assert response.status_code == 500
        assert response.json()["detail"] == "Trakt authentication failed"
        mock_save.assert_not_called()

    def test_poll_error_message_is_generic(self, client, mock_components) -> None:
        """A poll TraktAuthError surfaces only the generic message, never raw."""
        with (
            patch(
                "src.web.api.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.web.api.poll_device_token",
                side_effect=TraktAuthError("invalid device code 0xdeadbeef"),
            ),
        ):
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "Trakt authentication failed"

    def test_short_device_code_rejected(self, client, mock_components) -> None:
        """A device_code shorter than the min length is rejected before polling."""
        with patch("src.web.api.poll_device_token") as mock_poll:
            response = client.post(
                "/api/trakt/poll-device-approval", json={"device_code": "short"}
            )

        assert response.status_code == 422
        mock_poll.assert_not_called()

    def test_no_storage_returns_500(self, client, mock_components) -> None:
        """Poll returns 500 'Storage not initialized' when storage is None."""
        app_state.storage = None

        response = client.post(
            "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Storage not initialized"
