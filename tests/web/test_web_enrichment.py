"""Tests for web API enrichment endpoints."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.enrichment.manager import EnrichmentManager
from src.enrichment.registry import EnrichmentRegistry
from src.models.content import ContentType
from src.storage.manager import StorageManager
from src.web.enrichment_manager import reset_enrichment_manager
from tests.enrichment.test_enrichment_manager import (
    WrappedRequestErrorProvider,
    http_error,
    save_movie,
)
from tests.factories import authenticated_client, booted_web_app


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
            yield authenticated_client(app)
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

    def test_a_wrapped_provider_error_reaches_the_wire_derived(
        self, tmp_path: Path
    ) -> None:
        """Closes the chain from the manager's scrub to the body an operator reads.

        ``TestEnrichmentStatusApiKeyScrubbingRegression`` pins the manager end.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        save_movie(storage)
        registry = EnrichmentRegistry()
        registry._discovered = True
        registry.register(
            WrappedRequestErrorProvider(
                http_error(401), message="GET ?api_key=SECRET_KEY_123 failed"
            )
        )
        config = {
            "enrichment": {
                "batch_size": 10,
                "providers": {"wrapped_request": {"enabled": True}},
            }
        }
        manager = EnrichmentManager(storage, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        with (
            _client(MagicMock(spec=StorageManager), config) as client,
            patch("src.web.api.get_enrichment_manager", return_value=manager),
        ):
            response = client.get("/api/enrichment/status")

        assert response.status_code == 200
        assert response.json()["errors"] == ["wrapped_request: HTTP 401"]
        assert "SECRET_KEY_123" not in response.text


class TestEnrichmentStats:
    """Tests for GET /api/enrichment/stats endpoint."""

    def test_get_stats(self) -> None:
        """Test getting enrichment statistics."""
        storage = MagicMock(spec=StorageManager)
        storage.enrichment.stats.return_value = {
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


class TestEnrichmentReset:
    """Tests for POST /api/enrichment/reset endpoint."""

    def test_reset_all(self) -> None:
        """Test resetting all enrichment status."""
        storage = MagicMock(spec=StorageManager)
        storage.enrichment.reset.return_value = 50

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/reset", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 50
        assert "50" in data["message"]

    def test_reset_invalid_content_type(self) -> None:
        """Test error with invalid content type."""
        with _client(MagicMock(spec=StorageManager), {}) as client:
            response = client.post(
                "/api/enrichment/reset",
                json={"content_type": "invalid"},
            )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()
