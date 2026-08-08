"""Tests for web API enrichment endpoints."""

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.enrichment.manager import EnrichmentManager
from src.storage.manager import StorageManager
from src.web.enrichment_manager import reset_enrichment_manager
from tests.factories import booted_web_app


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


@contextmanager
def _client(storage: MagicMock, config: dict) -> Iterator[TestClient]:
    """Serve the app with ``storage`` and ``config`` bound into ``app_state``.

    Binding the state rather than patching whichever module imported
    ``get_storage``: the endpoints reach their components through the shared
    guards, and app_state is the one place both routers agree on.

    The enrichment manager is a module-level singleton of its own, so it is
    reset on both sides of the boot.
    """
    reset_enrichment_manager()
    try:
        with booted_web_app(storage, config) as app:
            yield TestClient(app)
    finally:
        reset_enrichment_manager()


class TestEnrichmentStart:
    """Tests for POST /api/enrichment/start endpoint."""

    def test_start_enrichment_success(self, mock_config: dict) -> None:
        """Test successful enrichment start."""
        with (
            _client(MagicMock(spec=StorageManager), mock_config) as client,
            patch("src.web.enrichment_manager.EnrichmentManager") as mock_manager_cls,
        ):
            mock_manager = MagicMock(spec=EnrichmentManager)
            mock_manager.start_enrichment.return_value = True
            mock_manager_cls.return_value = mock_manager

            response = client.post("/api/enrichment/start", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

    def test_start_enrichment_disabled(self, mock_config_disabled: dict) -> None:
        """Test error when enrichment is disabled."""
        with _client(MagicMock(spec=StorageManager), mock_config_disabled) as client:
            response = client.post("/api/enrichment/start", json={})

        assert response.status_code == 400
        assert "disabled" in response.json()["detail"].lower()

    def test_start_enrichment_with_content_type(self, mock_config: dict) -> None:
        """Test starting enrichment with content type filter."""
        with (
            _client(MagicMock(spec=StorageManager), mock_config) as client,
            patch("src.web.enrichment_manager.EnrichmentManager") as mock_manager_cls,
        ):
            mock_manager = MagicMock(spec=EnrichmentManager)
            mock_manager.start_enrichment.return_value = True
            mock_manager_cls.return_value = mock_manager

            response = client.post(
                "/api/enrichment/start",
                json={"content_type": "movie"},
            )

        assert response.status_code == 200
        assert "movie" in response.json()["message"].lower()

    def test_start_enrichment_invalid_content_type(self, mock_config: dict) -> None:
        """Test error with invalid content type."""
        with _client(MagicMock(spec=StorageManager), mock_config) as client:
            response = client.post(
                "/api/enrichment/start",
                json={"content_type": "invalid"},
            )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()


class TestEnrichmentStatus:
    """Tests for GET /api/enrichment/status endpoint."""

    def test_get_status_no_job(self) -> None:
        """Test status when no job exists."""
        with _client(MagicMock(spec=StorageManager), {}) as client:
            response = client.get("/api/enrichment/status")

        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False


class TestEnrichmentStats:
    """Tests for GET /api/enrichment/stats endpoint."""

    def test_get_stats(self) -> None:
        """Test getting enrichment statistics."""
        storage = MagicMock(spec=StorageManager)
        storage.get_enrichment_stats.return_value = {
            "total": 100,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "by_provider": {"tmdb": 50, "openlibrary": 30},
            "by_quality": {"high": 60, "medium": 20},
        }

        with _client(storage, {}) as client:
            response = client.get("/api/enrichment/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["total"] == 100
        assert data["enriched"] == 80
        assert data["pending"] == 15
        assert data["by_provider"]["tmdb"] == 50

    def test_get_stats_with_enrichment_enabled(self) -> None:
        """Test that enabled field is True when enrichment is enabled in config."""
        storage = MagicMock(spec=StorageManager)
        storage.get_enrichment_stats.return_value = {
            "total": 10,
            "enriched": 5,
            "pending": 5,
            "not_found": 0,
            "failed": 0,
            "by_provider": {},
            "by_quality": {},
        }

        with _client(storage, {"enrichment": {"enabled": True}}) as client:
            response = client.get("/api/enrichment/stats")

        assert response.status_code == 200
        assert response.json()["enabled"] is True


class TestEnrichmentReset:
    """Tests for POST /api/enrichment/reset endpoint."""

    def test_reset_all(self) -> None:
        """Test resetting all enrichment status."""
        storage = MagicMock(spec=StorageManager)
        storage.reset_enrichment_status.return_value = 50

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/reset", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 50
        assert "50" in data["message"]

    def test_reset_by_provider(self) -> None:
        """Test resetting enrichment by provider."""
        storage = MagicMock(spec=StorageManager)
        storage.reset_enrichment_status.return_value = 20

        with _client(storage, {}) as client:
            response = client.post(
                "/api/enrichment/reset",
                json={"provider": "tmdb"},
            )

        assert response.status_code == 200
        assert response.json()["count"] == 20
        storage.reset_enrichment_status.assert_called_once()

    def test_reset_by_content_type(self) -> None:
        """Test resetting enrichment by content type."""
        storage = MagicMock(spec=StorageManager)
        storage.reset_enrichment_status.return_value = 15

        with _client(storage, {}) as client:
            response = client.post(
                "/api/enrichment/reset",
                json={"content_type": "book"},
            )

        assert response.status_code == 200
        assert response.json()["count"] == 15

    def test_reset_invalid_content_type(self) -> None:
        """Test error with invalid content type."""
        with _client(MagicMock(spec=StorageManager), {}) as client:
            response = client.post(
                "/api/enrichment/reset",
                json={"content_type": "invalid"},
            )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()
