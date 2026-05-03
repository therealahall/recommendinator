"""Tests for web API endpoints."""

import json
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.api import APP_VERSION, _item_to_response
from src.web.app import create_app
from src.web.enrichment_manager import WebEnrichmentManager
from src.web.epic_auth import EpicAuthError
from src.web.gog_auth import GogAuthError
from src.web.state import AppState, app_state
from src.web.sync_manager import SyncManager, reset_sync_manager


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

    def test_legacy_cache_busts_static_assets(self, client):
        """root() appends ?v={APP_VERSION} to legacy template static asset URLs.

        Bug context: The legacy template uses manual cache busting via version
        query parameters. We monkeypatch Path.exists so the dist/index.html is
        not found, forcing the legacy template code path.
        """
        original_exists = Path.exists

        def force_legacy(self: Path) -> bool:
            # Hide dist/index.html to force legacy template path
            if str(self).endswith("dist/index.html"):
                return False
            return original_exists(self)

        with patch.object(Path, "exists", force_legacy):
            response = client.get("/")
        assert response.status_code == 200
        # Verify at least one static asset gets the version query param
        assert f"?v={APP_VERSION}" in response.text

    def test_legacy_version_label_and_update_banner_present(self, client):
        """Legacy template includes DOM elements for version display."""
        original_exists = Path.exists

        def force_legacy(self: Path) -> bool:
            if str(self).endswith("dist/index.html"):
                return False
            return original_exists(self)

        with patch.object(Path, "exists", force_legacy):
            response = client.get("/")
        assert response.status_code == 200
        assert 'id="versionLabel"' in response.text
        assert 'id="updateBanner"' in response.text
        assert 'class="update-banner"' in response.text

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

    def test_legacy_body_has_data_version(self, client):
        """root() injects the app version into body data-version (legacy template)."""
        original_exists = Path.exists

        def force_legacy(self: Path) -> bool:
            if str(self).endswith("dist/index.html"):
                return False
            return original_exists(self)

        with patch.object(Path, "exists", force_legacy):
            response = client.get("/")
        assert response.status_code == 200
        assert f'data-version="{APP_VERSION}"' in response.text


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
        user_id=1,
    )


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
    """Tests for POST /api/update 409 Conflict when sync is already running."""

    def test_update_returns_409_when_sync_already_running(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """POST /api/update returns 409 when a sync job is already in progress.

        Bug coverage: The 409 conflict response path was untested.
        This verifies that when the SyncManager reports a running job,
        the endpoint returns HTTP 409 with an informative error message.
        """
        with patch("src.web.api.get_sync_manager") as mock_get_sync_manager:
            mock_manager = Mock(spec=SyncManager)
            mock_manager.is_running.return_value = True
            mock_manager.get_status.return_value = {
                "job": {"source": "goodreads"},
            }
            mock_get_sync_manager.return_value = mock_manager

            response = client.post("/api/update", json={"source": "steam"})

            assert response.status_code == 409
            detail = response.json()["detail"]
            assert "Sync already in progress" in detail
            assert "goodreads" in detail


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
