"""Tests for web API enrichment endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.web.enrichment_manager import reset_enrichment_manager


@pytest.fixture
def mock_config() -> dict:
    """Create mock config with enrichment enabled."""
    return {
        "enrichment": {
            "enabled": True,
            "batch_size": 50,
            "providers": {
                "tmdb": {"enabled": True, "api_key": "test-key"},
            },
        },
    }


@pytest.fixture
def mock_config_disabled() -> dict:
    """Create mock config with enrichment disabled."""
    return {
        "enrichment": {
            "enabled": False,
        },
    }


@pytest.fixture
def client(mock_config: dict) -> TestClient:
    """Create test client with mocked dependencies."""
    reset_enrichment_manager()

    with patch("src.web.api.get_storage") as mock_storage:
        with patch("src.web.api.get_config") as mock_get_config:
            mock_storage.return_value = MagicMock()
            mock_get_config.return_value = mock_config

            from src.web.app import app

            yield TestClient(app)

    reset_enrichment_manager()


class TestEnrichmentStart:
    """Tests for POST /api/enrichment/start endpoint."""

    def test_start_enrichment_success(self, mock_config: dict) -> None:
        """Test successful enrichment start."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage.return_value = MagicMock()
                mock_get_config.return_value = mock_config

                with patch(
                    "src.web.enrichment_manager.EnrichmentManager"
                ) as mock_manager_cls:
                    mock_manager = MagicMock()
                    mock_manager.start_enrichment.return_value = True
                    mock_manager_cls.return_value = mock_manager

                    from src.web.app import app

                    client = TestClient(app)
                    response = client.post("/api/enrichment/start", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

        reset_enrichment_manager()

    def test_start_enrichment_disabled(self, mock_config_disabled: dict) -> None:
        """Test error when enrichment is disabled."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage.return_value = MagicMock()
                mock_get_config.return_value = mock_config_disabled

                from src.web.app import app

                client = TestClient(app)
                response = client.post("/api/enrichment/start", json={})

        assert response.status_code == 400
        assert "disabled" in response.json()["detail"].lower()

        reset_enrichment_manager()

    def test_start_enrichment_with_content_type(self, mock_config: dict) -> None:
        """Test starting enrichment with content type filter."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage.return_value = MagicMock()
                mock_get_config.return_value = mock_config

                with patch(
                    "src.web.enrichment_manager.EnrichmentManager"
                ) as mock_manager_cls:
                    mock_manager = MagicMock()
                    mock_manager.start_enrichment.return_value = True
                    mock_manager_cls.return_value = mock_manager

                    from src.web.app import app

                    client = TestClient(app)
                    response = client.post(
                        "/api/enrichment/start",
                        json={"content_type": "movie"},
                    )

        assert response.status_code == 200
        assert "movie" in response.json()["message"].lower()

        reset_enrichment_manager()

    def test_start_enrichment_invalid_content_type(self, mock_config: dict) -> None:
        """Test error with invalid content type."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage.return_value = MagicMock()
                mock_get_config.return_value = mock_config

                from src.web.app import app

                client = TestClient(app)
                response = client.post(
                    "/api/enrichment/start",
                    json={"content_type": "invalid"},
                )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()

        reset_enrichment_manager()


class TestEnrichmentStatus:
    """Tests for GET /api/enrichment/status endpoint."""

    def test_get_status_no_job(self) -> None:
        """Test status when no job exists."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage.return_value = MagicMock()
                mock_get_config.return_value = {}

                from src.web.app import app

                client = TestClient(app)
                response = client.get("/api/enrichment/status")

        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False

        reset_enrichment_manager()


class TestEnrichmentStats:
    """Tests for GET /api/enrichment/stats endpoint."""

    def test_get_stats(self) -> None:
        """Test getting enrichment statistics."""
        mock_stats = {
            "total": 100,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50, "openlibrary": 30},
            "by_quality": {"high": 60, "medium": 20},
        }

        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage_instance = MagicMock()
                mock_storage_instance.get_enrichment_stats.return_value = mock_stats
                mock_storage.return_value = mock_storage_instance
                mock_get_config.return_value = {}

                from src.web.app import app

                client = TestClient(app)
                response = client.get("/api/enrichment/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["total"] == 100
        assert data["enriched"] == 80
        assert data["pending"] == 15
        assert data["by_provider"]["tmdb"] == 50

        reset_enrichment_manager()

    def test_get_stats_with_enrichment_enabled(self) -> None:
        """Test that enabled field is True when enrichment is enabled in config."""
        mock_stats = {
            "total": 10,
            "enriched": 5,
            "pending": 5,
            "not_found": 0,
            "failed": 0,
            "by_provider": {},
            "by_quality": {},
        }

        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage_instance = MagicMock()
                mock_storage_instance.get_enrichment_stats.return_value = mock_stats
                mock_storage.return_value = mock_storage_instance
                mock_get_config.return_value = {"enrichment": {"enabled": True}}

                from src.web.app import app

                client = TestClient(app)
                response = client.get("/api/enrichment/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

        reset_enrichment_manager()


class TestEnrichmentReset:
    """Tests for POST /api/enrichment/reset endpoint."""

    def test_reset_all(self) -> None:
        """Test resetting all enrichment status."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage_instance = MagicMock()
                mock_storage_instance.reset_enrichment_status.return_value = 50
                mock_storage.return_value = mock_storage_instance
                mock_get_config.return_value = {}

                from src.web.app import app

                client = TestClient(app)
                response = client.post("/api/enrichment/reset", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 50
        assert "50" in data["message"]

        reset_enrichment_manager()

    def test_reset_by_provider(self) -> None:
        """Test resetting enrichment by provider."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage_instance = MagicMock()
                mock_storage_instance.reset_enrichment_status.return_value = 20
                mock_storage.return_value = mock_storage_instance
                mock_get_config.return_value = {}

                from src.web.app import app

                client = TestClient(app)
                response = client.post(
                    "/api/enrichment/reset",
                    json={"provider": "tmdb"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 20

        # Verify the storage method was called with correct params
        mock_storage_instance.reset_enrichment_status.assert_called_once()

        reset_enrichment_manager()

    def test_reset_by_content_type(self) -> None:
        """Test resetting enrichment by content type."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage_instance = MagicMock()
                mock_storage_instance.reset_enrichment_status.return_value = 15
                mock_storage.return_value = mock_storage_instance
                mock_get_config.return_value = {}

                from src.web.app import app

                client = TestClient(app)
                response = client.post(
                    "/api/enrichment/reset",
                    json={"content_type": "book"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 15

        reset_enrichment_manager()

    def test_reset_invalid_content_type(self) -> None:
        """Test error with invalid content type."""
        reset_enrichment_manager()

        with patch("src.web.api.get_storage") as mock_storage:
            with patch("src.web.api.get_config") as mock_get_config:
                mock_storage.return_value = MagicMock()
                mock_get_config.return_value = {}

                from src.web.app import app

                client = TestClient(app)
                response = client.post(
                    "/api/enrichment/reset",
                    json={"content_type": "invalid"},
                )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()

        reset_enrichment_manager()
