"""Tests for web API endpoints."""

import asyncio
import csv
import io
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from math import inf
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import src.sources.service
import src.web.api
from src.auth.epic import EpicAuthError
from src.auth.gog import GogAuthError
from src.auth.trakt import DevicePollResult, DevicePollStatus, TraktAuthError
from src.config.service import load_config
from src.ingestion.paths import get_allowed_source_roots
from src.ingestion.sync import ALL_SOURCES_KEY, SyncResult, SyncResultCallback
from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_RELEASE_YEAR,
    MAX_REVIEW_LENGTH,
    MIN_RELEASE_YEAR,
    ConsumptionStatus,
    ContentItem,
    ContentType,
    ExternalId,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from src.recommendations.scorers import SCORER_NAME_MAP
from src.settings.metadata import default_of
from src.settings.service import build_settings_view
from src.sources.service import SOURCE_MISCONFIGURED_DETAIL
from src.storage.manager import (
    UNSET,
    SavedItem,
    SaveOutcome,
    StorageManager,
    UncorrectableFieldError,
)
from src.storage.schema import update_user_settings
from src.utils.dotted_path import get_leaf
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.utils.text import LINE_BREAKS
from src.web.api import APP_VERSION, CompletionRequest
from src.web.app import (
    _raised_refusal_json_can_carry,
)
from src.web.auth import SESSION_COOKIE, require_session
from src.web.enrichment_manager import (
    WebEnrichmentManager,
    _enrichment_manager_lock,
    get_enrichment_manager,
    reset_enrichment_manager,
)
from src.web.state import (
    _config_lock,
    app_state,
    locked_running_config,
    reload_config,
)
from src.web.sync_manager import (
    SyncManager,
    _sync_manager_lock,
    get_sync_manager,
    reset_sync_manager,
)
from tests.factories import (
    authenticated_client,
    back_mock_preference_store,
    booted_web_app,
    make_item,
    spec_sub_stores,
)


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return {
        "storage": {"database_path": "data/test.db"},
        "web": {
            "host": "0.0.0.0",
            "port": 8000,
        },
        "inputs": {
            "goodreads_rss": {
                "plugin": "goodreads_rss",
                "user_id": "12345",
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

    mock_storage_manager = Mock(spec=StorageManager)
    spec_sub_stores(mock_storage_manager)
    mock_storage_manager.credentials.get_for_source.return_value = {}
    mock_storage_manager.sources.list.return_value = []
    mock_storage_manager.sources.get.return_value = None

    mock_engine_instance = Mock(spec=RecommendationEngine)
    mock_engine_instance.storage = mock_storage_manager

    with booted_web_app(
        mock_storage_manager,
        mock_config,
        engine=mock_engine_instance,
    ) as app:
        yield {
            "app": app,
            "storage": mock_storage_manager,
            "engine": mock_engine_instance,
        }

    # Clean up sync manager after test
    reset_sync_manager()


@pytest.fixture
def client(mock_components):
    """Create test client."""
    return authenticated_client(mock_components["app"])


@pytest.fixture
def anonymous_client(mock_components):
    """Create a test client carrying no API token."""
    return TestClient(mock_components["app"])


def _cors_kwargs(app) -> dict:
    """Return the kwargs actually handed to the CORS middleware.

    Read off the middleware rather than ``app_state.config``: the config keeps
    whatever YAML supplied, and the type guard in ``create_app`` is what decides
    the value that reaches Starlette.
    """
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware.kwargs
    raise AssertionError("CORSMiddleware is not installed")


def _cors_origins(app) -> list[str]:
    """Return the origin list actually handed to the CORS middleware."""
    return _cors_kwargs(app)["allow_origins"]


class TestCreateAppSettingsMigration:
    """create_app assembles DB-overlaid settings before reading global config."""

    def test_create_app_overlays_db_settings_onto_config(self, mock_config, tmp_path):
        """create_app runs the real settings assembly against an isolated DB.

        Drives the *real* ``migrate_config_settings`` hook with a real temp-DB
        StorageManager (no stub): a stored DB leaf must win over the YAML value
        on the running config that create_app stores in app_state, and boot
        must not write anything to the settings table.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        # A stored DB leaf must win over the YAML value on boot.
        storage_manager.settings.set("recommendations.default_count", 9)
        with booted_web_app(storage_manager, mock_config):
            # Real hook overlaid the DB leaf onto the in-memory config.
            assert app_state.config["recommendations"]["default_count"] == 9
            # Boot seeded nothing: only the pre-existing leaf remains in the DB.
            assert storage_manager.settings.list() == {
                "recommendations.default_count": 9
            }
        reset_sync_manager()

    def test_debug_resolves_from_yaml_not_the_db_overlay(self, mock_config, tmp_path):
        """A stale ``web.debug`` DB row must not enable the OpenAPI docs.

        Regression: create_app read ``web.debug`` from the config AFTER
        migrate_config_settings ran. ``web`` is still an in-scope section and the
        overlay applies unknown/legacy leaves, so a ``web.debug`` row left by an
        earlier build — when it was briefly a registry leaf — re-enabled /docs
        and /redoc here while src/web/main.py (raw YAML) ignored it. The row is
        also unreachable from the app, since `settings reset` refuses a key with
        no registry entry. Debug must resolve pre-overlay, matching the launcher.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        # Write the row directly: the settings API would reject this key now.
        storage_manager.settings.set("web.debug", True)
        with booted_web_app(storage_manager, mock_config) as app:
            # mock_config carries no web.debug, so the bootstrap default (False)
            # applies and the docs stay closed despite the stored row.
            assert app.docs_url is None
            assert app.redoc_url is None
            # The schema too: docs_url=None alone would still serve the full
            # route inventory at /openapi.json.
            assert app.openapi_url is None
        reset_sync_manager()

    def test_yaml_debug_true_opens_the_openapi_docs(self, mock_config, tmp_path):
        """The positive half: YAML ``web.debug`` is what actually gates /docs.

        Without this, ``debug_mode`` could be hardcoded False and the negative
        test above would still pass — proving the docs are closed, but nothing
        about where the value comes from.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {**mock_config.get("web", {}), "debug": True}}
        with booted_web_app(storage_manager, config) as app:
            assert app.docs_url == "/docs"
            assert app.redoc_url == "/redoc"
            # Swagger and ReDoc need the schema, so it opens with them.
            assert app.openapi_url == "/openapi.json"
        reset_sync_manager()

    def test_non_dict_web_section_does_not_crash_boot(self, mock_config, tmp_path):
        """A ``web:`` header with no children must not take the app down.

        Regression: the debug read moved above ``migrate_config_settings``, which
        is what heals a non-dict section — so ``config.get("web", {})`` returned
        None (the default only fires on an ABSENT key) and boot died with an
        AttributeError outside the try/except.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": None}
        with booted_web_app(storage_manager, config) as app:
            # Fails closed on both counts: no debug, and CORS pinned to the
            # restrictive default rather than whatever a malformed section
            # produced.
            assert app.docs_url is None
            assert _cors_origins(app) == default_of("web.allowed_origins")
        reset_sync_manager()

    def test_configured_origins_reach_the_middleware(self, mock_config, tmp_path):
        """A well-formed non-default list must pass through, not fall back.

        Every other CORS test asserts the FALLBACK, so replacing the guard with
        an unconditional `default_of(...)` would keep them all green while
        silently discarding the CORS policy of every operator who configured one.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {
            **mock_config,
            "web": {"allowed_origins": ["https://app.example.com"]},
        }
        with booted_web_app(storage_manager, config) as app:
            assert _cors_origins(app) == ["https://app.example.com"]
        reset_sync_manager()

    @pytest.mark.parametrize("bad_origins", [None, "https://app.example.com", [1, 2]])
    def test_unusable_allowed_origins_falls_back_to_the_default(
        self, mock_config, tmp_path, bad_origins
    ):
        """A malformed CORS list must not crash boot or widen the policy.

        Regression: a blank ``allowed_origins:`` yields None and ``"*" not in
        None`` raised outside the try/except, so boot died with a bare
        traceback. A scalar string was worse — Starlette's origin check is
        ``origin in self.allow_origins``, which on a string is a substring test,
        so ``https://app.example.co`` would have been accepted against a
        configured ``https://app.example.com``.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {"allowed_origins": bad_origins}}
        with booted_web_app(storage_manager, config) as app:
            assert _cors_origins(app) == default_of("web.allowed_origins")
        reset_sync_manager()

    def test_create_app_migrates_config_secret_into_storage(self, tmp_path) -> None:
        """create_app sweeps a YAML provider secret into encrypted storage.

        Regression: the ``migrate_config_secrets`` boot hook must actually run
        during ``create_app`` — asserted end-to-end against a real temp-DB (no
        stub). The plaintext api_key must land in encrypted storage and be
        stripped from the running config held in app_state.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {
            "storage": {"database_path": str(tmp_path / "test.db")},
            "enrichment": {"providers": {"tmdb": {"api_key": "tmdb-secret"}}},
        }
        with booted_web_app(storage_manager, config):
            # The secret was encrypted into storage on boot.
            assert (
                storage_manager.secrets.has("enrichment.providers.tmdb.api_key") is True
            )
            # And stripped from the running config, so no plaintext lingers.
            providers = app_state.config["enrichment"]["providers"]
            assert providers.get("tmdb", {}).get("api_key") is None
        reset_sync_manager()


class TestBootStartsFromCleanStateRegression:
    """A boot must not inherit what an earlier test left in ``app_state``.

    Bug reported: ``config_watcher`` is the one ``AppState`` field
    ``create_app`` never assigns, so a test that installed a mock watcher
    handed that mock to every app booted after it — a later lifespan would
    start and stop somebody else's mock instead of a watcher of its own.
    Root cause: ``booted_web_app`` snapshotted ``app_state`` and restored it,
    which protects the caller's own mutations but carries a pre-existing leak
    straight into the boot.
    Fix: the helper installs ``AppState()`` defaults before the boot as well as
    restoring the snapshot after, so it is clean-start and leak-free both.
    """

    def test_no_field_survives_the_boot_and_every_one_comes_back(
        self, mock_config, tmp_path, monkeypatch
    ) -> None:
        """Clean-start and leak-free hold for the whole dataclass, not one field.

        A sentinel per field, so a field the helper forgot to reset shows up as
        the caller's object reaching the boot, and one it forgot to restore
        shows up as the caller's object not coming back.
        """
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        sentinels = {field.name: object() for field in fields(app_state)}
        for name, sentinel in sentinels.items():
            monkeypatch.setattr(app_state, name, sentinel)

        with booted_web_app(storage_manager, mock_config):
            inherited = {
                name
                for name, sentinel in sentinels.items()
                if getattr(app_state, name) is sentinel
            }

        assert inherited == set()
        assert {name: getattr(app_state, name) for name in sentinels} == sentinels


class TestRootEndpoint:
    """Tests for the root HTML endpoint."""

    def test_serves_html_with_branding(self, client):
        """Test root endpoint serves HTML with correct branding."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Recommendinator" in response.text

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


def test_status_endpoint(client):
    """Test status endpoint returns version from src.__version__."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["version"] == APP_VERSION
    assert isinstance(data["components"], dict)


class TestStatusRecommendationsConfig:
    """Tests for recommendations_config in the /api/status response."""

    def test_status_includes_recommendations_config_defaults(self, client):
        """GET /api/status includes default max_count and default_count."""
        app_state.config = {}

        response = client.get("/api/status")
        assert response.status_code == 200
        rec_cfg = response.json()["recommendations_config"]
        assert rec_cfg["max_count"] == 20
        assert rec_cfg["default_count"] == 5

    def test_status_reads_recommendations_config_from_config(self, client):
        """GET /api/status surfaces max_count and default_count from config."""
        app_state.config = {
            "recommendations": {"max_count": 50, "default_count": 10},
        }

        response = client.get("/api/status")
        assert response.status_code == 200
        rec_cfg = response.json()["recommendations_config"]
        assert rec_cfg["max_count"] == 50
        assert rec_cfg["default_count"] == 10


def test_sync_sources_endpoint(client, mock_config):
    """Test sync sources endpoint returns only enabled sources from config."""
    response = client.get("/api/sync/sources")
    assert response.status_code == 200
    sources = response.json()
    assert isinstance(sources, list)
    # mock_config has exactly goodreads_rss enabled
    assert len(sources) == 1
    goodreads = next((s for s in sources if s["id"] == "goodreads_rss"), None)
    assert goodreads is not None
    assert goodreads["display_name"] == "Goodreads Rss"
    assert goodreads["plugin_display_name"] == "Goodreads (Public Shelves via RSS)"


def test_sync_sources_lists_all_with_enabled_flag(client):
    """All configured sources are listed; ``enabled`` flag exposed per source.

    The UI renders disabled sources in a muted state instead of hiding them
    entirely, so the listing endpoint must surface them. ``resolve_inputs``
    is the gate that filters to enabled-only for sync execution.
    """
    app_state.config = {
        "inputs": {
            "goodreads_rss": {
                "plugin": "goodreads_rss",
                "user_id": "12345",
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

    assert by_id["goodreads_rss"]["enabled"] is True
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
        Recommendation(
            item=mock_item, score=0.85, reasoning="Recommended highly similar"
        )
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
    mock_components["storage"].complete_content_item.return_value = 1

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
    assert data["message"] == "Marked 'Test Book' as completed"
    assert data["id"] == 1


def _client_on(app, storage: StorageManager) -> TestClient:
    """Swap *storage* into app state and sign a fresh client in against it.

    Sessions live in the database, so a client made before the swap carries a
    cookie the new database has never heard of.
    """
    app_state.storage = storage
    return authenticated_client(app)


def test_complete_endpoint_dates_by_the_host_calendar_day_regression(
    mock_components, tmp_path, host_timezone
):
    """POST /api/complete dates a completion by the day the user is living.

    Bug reported: an item finished at 21:00 in America/Los_Angeles came back
    dated tomorrow. The endpoint stamped ``datetime.now(UTC).date()``, so west
    of UTC an evening completion crossed into the next UTC day — while a date
    arriving from an import was narrowed to the host's zone. The ``TZ`` a
    Docker operator sets was honoured for one and ignored for the other.
    Root cause: the endpoint chose the date itself, in UTC.
    Fix: the endpoint sends no date; the storage door stamps today in the
    host's zone. The clock is frozen because under the suite's UTC default the
    two implementations agree, and a live clock disagrees with itself across
    UTC midnight.
    """
    host_timezone("America/Los_Angeles")
    storage = StorageManager(sqlite_path=tmp_path / "complete.db")
    client = _client_on(mock_components["app"], storage)

    with patch(
        "src.utils.dates.utc_now", return_value=datetime(2026, 3, 15, 4, 0, tzinfo=UTC)
    ):
        response = client.post(
            "/api/complete",
            json={"content_type": "book", "title": "Piranesi", "rating": 4},
        )

    assert response.status_code == 200, response.text
    stored = storage.get_content_item(response.json()["id"])
    assert stored is not None
    assert stored.date_completed == date(2026, 3, 14)


def test_complete_endpoint_preserves_an_imported_completion_date_regression(
    mock_components, tmp_path
):
    """POST /api/complete does not re-date an item that already has a date.

    Bug reported: completing an item imported with
    ``date_completed = 2020-01-01`` rewrote the date to today — silent loss of
    a date the user owns, which feeds the variety ladder's ordering.
    Root cause: the endpoint stamped today's date onto the item it built, and
    the sync door's later-date-wins rule takes today over any past date.
    Fix: the endpoint sends no date; the door fills an empty one and keeps a
    stored one.
    """
    storage = StorageManager(sqlite_path=tmp_path / "complete.db")
    db_id = storage.save_content_item(
        ContentItem(
            id="book-1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2020, 1, 1),
        )
    )
    client = _client_on(mock_components["app"], storage)

    response = client.post(
        "/api/complete",
        json={"content_type": "book", "title": "Dune", "rating": 2},
    )

    assert response.status_code == 200, response.text
    stored = storage.get_content_item(db_id)
    assert stored is not None
    assert stored.date_completed == date(2020, 1, 1)
    assert stored.rating == 2


def test_complete_endpoint_stores_a_movie_director_regression(
    mock_components, tmp_path
):
    """POST /api/complete keeps the creator of a non-book content type.

    Bug reported: posting a movie with ``author`` set returned 200 and stored
    no director, so the completed item showed none and exported a blank
    creator cell.
    Root cause: the endpoint passed ``author`` through for books alone,
    because no other content type had anywhere to keep a creator.
    Fix: every type stores its creator in the column its type declares, so
    the endpoint hands the value over whatever the type. The CLI door's half
    of this is in ``tests/test_cli.py``.
    """
    storage = StorageManager(sqlite_path=tmp_path / "creator.db")
    client = _client_on(mock_components["app"], storage)

    response = client.post(
        "/api/complete",
        json={
            "content_type": "movie",
            "title": "Arrival",
            "author": "Denis Villeneuve",
        },
    )

    assert response.status_code == 200, response.text
    stored = storage.get_content_item(response.json()["id"])
    assert stored is not None
    assert stored.author == "Denis Villeneuve"


def test_complete_endpoint_overwrites_existing_rating_regression(
    mock_components, tmp_path
):
    """POST /api/complete replaces the rating an item already has.

    Bug reported: completing an already-rated item through the API returns
    200 with "Marked 'Dune' as completed" while the stored rating is left at
    its old value, so the user's correction is silently discarded and
    preference analysis keeps scoring on the stale rating.
    Root cause: the endpoint persisted through ``save_content_item`` — the
    ingestion/sync door, whose fill-only rule never overwrites a user-owned
    field that already has a value — rather than an explicit-user-action door.
    Marking something complete from the UI is an explicit user action.
    Fix: the completion door applies the explicit-action rules, so the
    supplied rating and review win.
    """
    storage = StorageManager(sqlite_path=tmp_path / "complete.db")
    db_id = storage.save_content_item(
        ContentItem(
            id="book-1",
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            review="Loved it",
        )
    )
    client = _client_on(mock_components["app"], storage)

    response = client.post(
        "/api/complete",
        json={"content_type": "book", "title": "Dune", "rating": 2},
    )

    assert response.status_code == 200, response.text
    stored = storage.get_content_item(db_id)
    assert stored is not None
    assert stored.rating == 2


class TestCompletionEndpointRefusesABlankReview:
    """Typed ``str | None``, ``{"review": ""}`` reaches the overwriting
    completion door and erases a review the user wrote. The CLI half of this
    defect is covered in ``tests/cli/test_cli_error_disclosure.py``."""

    @pytest.mark.parametrize("blank_review", ["", "   "])
    def test_the_request_model_refuses_it_regression(self, blank_review: str) -> None:
        with pytest.raises(ValidationError) as caught:
            CompletionRequest(content_type="book", title="Dune", review=blank_review)

        assert [error["loc"] for error in caught.value.errors()] == [("review",)]


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
            "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.fetch",
            return_value=iter([mock_item]),
        ),
        patch(
            "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
            return_value=[],
        ),
    ):
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "goodreads_rss"})

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        # New async behavior: returns "sync started" message, not count
        assert "started" in data["message"].lower() or "sources" in data


def test_update_endpoint_steam(client, mock_components):
    """Test update endpoint starts background sync for Steam.

    The sync manager is stubbed because the real one spawns a thread that
    outlives the test calling the live Steam API.
    """
    # Update app_state config to include Steam
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }
    sync_manager = Mock(spec=SyncManager)
    sync_manager.is_running.return_value = False
    sync_manager.start_sync.return_value = (True, "Started sync for Steam")

    with patch("src.web.api.get_sync_manager", return_value=sync_manager):
        response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    # Background sync: returns "sync started" message
    assert "started" in data["message"].lower() or "sources" in data


def test_update_endpoint_steam_disabled(client, mock_components):
    """A disabled source is rejected with 400, not a 200 dead-end.

    The single-source /update branch must answer 4xx for a disabled or
    unconfigured source so the web UI's Sync button clears its optimistic
    "syncing" state. A 200 "message" body left the button stuck spinning
    because no SyncJob is ever created to end the frontend polling.
    """
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": False,
    }

    response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    data = response.json()
    assert "disabled or not configured" in data["detail"]


def test_update_endpoint_steam_missing_api_key(client, mock_components, caplog):
    """Test update endpoint with missing Steam API key."""
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "",
        "steam_id": "76561198000000000",
        "enabled": True,
    }

    with caplog.at_level(logging.WARNING, logger="src.web.api"):
        response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Source is not properly configured — check its 'api_key' setting."
    )
    assert "api_key" in caplog.text


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
            "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.fetch",
            return_value=iter([mock_book]),
        ),
        patch(
            "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
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
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "all"})

        assert response.status_code == 200
        data = response.json()
        # New async behavior: returns sync started message with sources list
        assert "message" in data
        assert "sources" in data
        assert "goodreads_rss" in data["sources"]
        assert "steam" in data["sources"]


class TestUpdateDetailKeepsCallerInputOffTheWireRegression:
    """Reported: ``POST /api/update`` echoed the caller's source id and the
    plugin's validation text. The id is unbounded caller input and the text
    names configured paths, so both go to the log alone now, as
    ``require_plugin`` already did — see ``TestSyncLogsTheReasonItRefused``.
    """

    def test_an_unknown_source_id_is_logged_not_echoed(self, client, caplog):
        """The id is unbounded caller input, so it stays out of the body."""
        with caplog.at_level(logging.INFO, logger="src.web.api"):
            response = client.post("/api/update", json={"source": "probe-me-42"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Source is disabled or not configured."
        assert "probe-me-42" not in response.text
        assert "probe-me-42" in caplog.text

    def test_the_source_id_stays_off_a_validation_refusal_too(
        self, client, mock_components, caplog
    ):
        """The id and the plugin's prose stay off the wire; field names go on.

        Named distinctly because the steam plugin's own messages say "steam",
        which would pass this assertion without the endpoint keeping quiet.
        """
        app_state.config["inputs"]["probe_me_42"] = {
            "plugin": "steam",
            "api_key": "",
            "steam_id": "",
            "vanity_url": "",
            "enabled": True,
        }

        with caplog.at_level(logging.WARNING, logger="src.web.api"):
            response = client.post("/api/update", json={"source": "probe_me_42"})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Source is not properly configured — check these: "
            "'api_key', 'steam_id', 'vanity_url'."
        )
        # The plugin's own sentence — and the signup URL inside it — is logged.
        assert "steamcommunity.com" not in response.text
        assert "probe_me_42" not in response.text
        assert "probe_me_42" in caplog.text
        assert "api_key" in caplog.text

    def test_prose_naming_no_field_falls_back_to_the_generic_refusal(
        self, client, mock_components
    ):
        """A "not found" names no field, so the answer says nothing about it.

        Keeps the oracle closed: whether the file is there must not change the
        wording, only the status code the caller already had.
        """
        app_state.config["inputs"]["games"] = {
            "plugin": "roms",
            "paths": ["/srv/private/roms"],
            "enabled": True,
        }

        with patch(
            "src.ingestion.sources.roms.roms.RomScannerPlugin.validate_config",
            return_value=["Directory not found: /srv/private/roms"],
        ):
            response = client.post("/api/update", json={"source": "games"})

        assert response.status_code == 400
        assert response.json()["detail"] == SOURCE_MISCONFIGURED_DETAIL
        assert "/srv/private" not in response.text

    def test_a_containment_refusal_discloses_neither_path_nor_allowlist(self, client):
        """Unmocked: the real refusal quotes the path and the config key.

        Neither reaches the wire — the caller learns only that the source is
        misconfigured, which is what keeps the arbitrary-read oracle closed.
        """
        app_state.config["inputs"]["games"] = {
            "plugin": "roms",
            "paths": ["/etc/shadow"],
            "enabled": True,
        }

        response = client.post("/api/update", json={"source": "games"})

        assert response.status_code == 400
        assert response.json()["detail"] == SOURCE_MISCONFIGURED_DETAIL
        assert "/etc/shadow" not in response.text
        assert "allowed_source_roots" not in response.text

    def test_a_newline_in_the_source_id_cannot_forge_a_log_line(self, client, caplog):
        """CR/LF is escaped before the id reaches the log (CWE-117)."""
        with caplog.at_level(logging.INFO, logger="src.web.api"):
            response = client.post(
                "/api/update", json={"source": "ok\nWARNING forged line"}
            )

        assert response.status_code == 400
        assert "\\nWARNING forged line" in caplog.text


class TestUpdateResolvesTheSourceOnceRegression:
    """Reported: the handler resolved the source id twice, and a delete landing
    between the two made the second lookup miss — answering with the caller's
    own unbounded id. Fixed by validating the entry already in hand.
    """

    def test_the_second_lookup_is_never_reached(self, client, mock_components):
        """A source vanishing after the first lookup changes no answer.

        The sync manager is stubbed because the real one spawns a thread
        that outlives the test calling the live Steam API.
        """
        app_state.config["inputs"]["probe_me_42"] = {
            "plugin": "steam",
            "api_key": "test_api_key",
            "steam_id": "76561198000000000",
            "enabled": True,
        }
        sync_manager = Mock(spec=SyncManager)
        sync_manager.is_running.return_value = False
        sync_manager.start_sync.return_value = (True, "Started sync for Probe Me 42")

        with (
            patch("src.web.api.get_sync_manager", return_value=sync_manager),
            patch(
                "src.sources.service.get_sync_handler", return_value=None
            ) as second_lookup,
        ):
            response = client.post("/api/update", json={"source": "probe_me_42"})

        assert response.status_code == 200
        assert response.json()["sources"] == ["probe_me_42"]
        second_lookup.assert_not_called()


def _sync_a_source_typed(client, content_type):
    """Sync one source carrying *content_type* and run its completion hook.

    Returns the response and the content type auto-enrichment was started
    with, which is the only place the endpoint's reading of the config value
    is observable.
    """
    app_state.config["enrichment"] = {"enabled": True, "auto_enrich_on_sync": True}
    app_state.config["inputs"]["typed"] = {
        "plugin": "goodreads_rss",
        "content_type": content_type,
        "enabled": True,
    }
    sync_manager = Mock(spec=SyncManager)
    sync_manager.is_running.return_value = False
    sync_manager.start_sync.return_value = (True, "Started sync for Typed")
    enrichment_manager = Mock(spec=WebEnrichmentManager)
    enrichment_manager.start_enrichment.return_value = (True, "started")

    with (
        patch("src.web.api.get_sync_manager", return_value=sync_manager),
        patch(
            "src.web.sync_dispatch.get_enrichment_manager",
            return_value=enrichment_manager,
        ),
        patch(
            "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
            return_value=[],
        ),
    ):
        response = client.post("/api/update", json={"source": "typed"})
        sync_manager.start_sync.call_args.kwargs["on_complete"]()

    started_with = enrichment_manager.start_enrichment.call_args.kwargs["content_type"]
    return response, started_with


class TestUpdateEnrichmentContentType:
    """What ``inputs.<id>.content_type`` does to the enrichment auto-start."""

    def test_a_valid_content_type_narrows_the_enrichment(self, client, mock_components):
        """Only the synced type is enriched when the source declares one."""
        response, started_with = _sync_a_source_typed(client, "book")

        assert response.status_code == 200
        assert started_with is ContentType.BOOK

    def test_an_unknown_content_type_enriches_every_type(
        self, client, mock_components, caplog
    ):
        """A value no ``ContentType`` member matches is a warning, not a refusal.

        The sync still starts and enrichment falls back to every type, so a
        typo in the config file cannot strand the source.
        """
        with caplog.at_level(logging.WARNING, logger="src.web.sync_dispatch"):
            response, started_with = _sync_a_source_typed(client, "paperback")

        assert response.status_code == 200
        assert started_with is None
        assert "Invalid content_type 'paperback'" in caplog.text

    def test_a_falsy_content_type_is_read_as_unset(
        self, client, mock_components, caplog
    ):
        """``content_type: false`` is a missing value, not a wrong one.

        Coercing ahead of the emptiness check would make it the string
        "False" and warn about a source nobody misconfigured.
        """
        with caplog.at_level(logging.WARNING, logger="src.web.sync_dispatch"):
            response, started_with = _sync_a_source_typed(client, False)

        assert response.status_code == 200
        assert started_with is None
        assert "Invalid content_type" not in caplog.text


class TestNonStringContentTypeRegression:
    """Reported: ``content_type: 2024`` in YAML reached ``sanitize_for_log`` as
    an ``int``, which raised ``TypeError`` out of the handler — a 500 and no
    sync — because ``config`` is ``dict[str, Any]`` and nothing coerced it.
    """

    def test_a_yaml_integer_content_type_still_starts_the_sync(
        self, client, mock_components
    ):
        """The value is coerced at the read, so the warning path survives it."""
        response, started_with = _sync_a_source_typed(client, 2024)

        assert response.status_code == 200
        assert started_with is None


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


def test_put_user_preferences_full(client, mock_components):
    """PUT /api/users/1/preferences can update all fields."""
    back_mock_preference_store(mock_components["storage"])

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


def test_put_user_preferences_rejects_out_of_range_variety_penalty(
    client, mock_components
):
    """variety_penalty above the 5.0 maximum is rejected with a 422."""
    merge = back_mock_preference_store(mock_components["storage"])

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 6.0},
    )
    assert response.status_code == 422
    merge.assert_not_called()


def test_put_user_preferences_keeps_stored_fields_the_request_omits(
    client, mock_components
):
    """A partial update merges onto what was stored, not onto the defaults."""
    back_mock_preference_store(
        mock_components["storage"],
        UserPreferenceConfig(theme="midnight", custom_rules=["no horror"]),
    )

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 1.0},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["variety_penalty"] == 1.0
    assert data["theme"] == "midnight"
    assert data["custom_rules"] == ["no horror"]


def test_put_user_preferences_merges_for_the_user_named_in_the_path(
    client, mock_components
):
    """The path id is what storage merges on.

    Every other case on this route uses user 1, so a handler that merged a
    hardcoded 1 would keep the whole suite green.
    """
    merge = back_mock_preference_store(mock_components["storage"])

    response = client.put(
        "/api/users/2/preferences",
        json={"variety_penalty": 1.0},
    )

    assert response.status_code == 200
    assert merge.call_args.args[0] == 2


_OVER_LONG_THEME_ID = "k" * (UserPreferenceConfig.MAX_THEME_ID_LENGTH + 1)
_CONTENT_TYPE_NAMES = [member.value for member in ContentType]


class TestUserPreferenceBounds:
    """The merge is additive, so keys a request names stay in
    ``users.settings`` for good and every recommendation request parses them.
    The sibling ``ItemEditRequest`` bounds its collections; this one did not.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {
                    "custom_rules": ["no horror"]
                    * (UserPreferenceConfig.MAX_CUSTOM_RULES + 1)
                },
                id="too-many-rules",
            ),
            pytest.param(
                {
                    "custom_rules": [
                        "r" * (UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH + 1)
                    ]
                },
                id="over-long-rule",
            ),
            pytest.param({"theme": _OVER_LONG_THEME_ID}, id="over-long-theme"),
        ],
    )
    def test_an_over_long_payload_is_rejected_rather_than_persisted(
        self, client, mock_components, payload
    ):
        """Validation refuses it, so storage is never asked to merge it."""
        merge = back_mock_preference_store(mock_components["storage"])

        response = client.put("/api/users/1/preferences", json=payload)

        assert response.status_code == 422
        merge.assert_not_called()

    def test_a_payload_sitting_exactly_on_every_bound_is_accepted(
        self, client, mock_components
    ):
        """An off-by-one the other way refuses what the bound allows.

        The rule is astral-plane characters: ``max_length`` counting UTF-8
        bytes would cut this one to a quarter of the documented 500.
        """
        merge = back_mock_preference_store(mock_components["storage"])
        at_bound_theme_id = "k" * UserPreferenceConfig.MAX_THEME_ID_LENGTH
        at_bound_rule = "🎬" * UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH

        response = client.put(
            "/api/users/1/preferences",
            json={
                "scorer_weights": dict.fromkeys(SCORER_NAME_MAP, 1.0),
                "custom_rules": [at_bound_rule] * UserPreferenceConfig.MAX_CUSTOM_RULES,
                "content_length_preferences": dict.fromkeys(
                    _CONTENT_TYPE_NAMES, "long"
                ),
                "theme": at_bound_theme_id,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["scorer_weights"] == dict.fromkeys(SCORER_NAME_MAP, 1.0)
        assert body["content_length_preferences"] == dict.fromkeys(
            _CONTENT_TYPE_NAMES, "long"
        )
        assert body["custom_rules"][0] == at_bound_rule
        assert body["theme"] == at_bound_theme_id
        merge.assert_called_once()

    @pytest.mark.parametrize("literal", ["Infinity", "NaN"])
    def test_a_non_finite_scorer_weight_is_refused_rather_than_stored(
        self, settings_app, literal
    ):
        """``JSONResponse`` will not render one, so a stored one answers 500
        on every later read of the page. Sent raw because ``json=`` declines
        to encode it, and read back over the real store.
        """
        client, _storage = settings_app
        tolerant = authenticated_client(client.app, raise_server_exceptions=False)

        written = tolerant.put(
            "/api/users/1/preferences",
            content=f'{{"scorer_weights": {{"recency": {literal}}}}}',
            headers={"Content-Type": "application/json"},
        )
        read_back = tolerant.get("/api/users/1/preferences")

        assert (written.status_code, read_back.status_code) == (422, 200)

    def test_a_weight_stored_before_the_bound_no_longer_500s_the_read(
        self, settings_app
    ):
        """The refusal above arrived after rows like this were already written.

        Poisoned through ``update_user_settings`` because every door the app
        offers now refuses one, which is exactly why the read has to cope.
        """
        client, storage = settings_app
        with storage.sqlite_db.connection() as conn:
            update_user_settings(
                conn, 1, {"preference_config": {"scorer_weights": {"recency": inf}}}
            )
        tolerant = authenticated_client(client.app, raise_server_exceptions=False)

        read_back = tolerant.get("/api/users/1/preferences")

        assert read_back.status_code == 200
        assert read_back.json()["scorer_weights"] == {}

    @pytest.mark.parametrize("literal", ["Infinity", "NaN"])
    def test_a_non_finite_variety_penalty_is_refused_the_same_way(
        self, settings_app, literal
    ):
        """The sibling instance: the bound already rejected it, and quoting it
        back in the 422 body is what turned the refusal into a 500.
        """
        client, _storage = settings_app
        tolerant = authenticated_client(client.app, raise_server_exceptions=False)

        written = tolerant.put(
            "/api/users/1/preferences",
            content=f'{{"variety_penalty": {literal}}}',
            headers={"Content-Type": "application/json"},
        )

        assert written.status_code == 422

    def test_a_stored_weight_survives_a_write_that_fills_the_bound(self, settings_app):
        """An earlier fix evicted by insertion order, so one write naming every
        scorer discarded the weight the user set first. The filling write omits
        that one, or overwriting it would read as surviving.
        """
        client, _storage = settings_app
        client.put(
            "/api/users/1/preferences",
            json={"scorer_weights": {"genre_match": 4.5}},
        )
        rest = {name: 1.0 for name in SCORER_NAME_MAP if name != "genre_match"}

        filled = client.put("/api/users/1/preferences", json={"scorer_weights": rest})

        assert filled.status_code == 200
        stored = client.get("/api/users/1/preferences").json()["scorer_weights"]
        assert stored == {**rest, "genre_match": 4.5}


class TestPreferenceKeysAreAClosedSet:
    """Reported: the web accepted any scorer name where the CLI checks against
    ``SCORER_NAME_MAP``. The engine drops an unknown one, so it weighted
    nothing and grew the blob every recommendation request parses.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"scorer_weights": {"recency": 1.0}}, id="unknown-scorer"),
            pytest.param(
                {"content_length_preferences": {"audiobook": "short"}},
                id="unknown-content-type",
            ),
            pytest.param(
                {"content_length_preferences": {"book": "brief"}},
                id="unknown-length-preference",
            ),
        ],
    )
    def test_a_key_outside_the_set_is_refused_rather_than_stored(
        self, client, mock_components, payload
    ):
        merge = back_mock_preference_store(mock_components["storage"])

        response = client.put("/api/users/1/preferences", json=payload)

        assert response.status_code == 422
        merge.assert_not_called()


class TestPreferenceWriteNamingAnUnknownUser:
    """Reported: the write is an ``UPDATE`` keyed on the id, so for a missing
    user it changed nothing, committed, and answered 200. The 404 comes from
    that write's refusal — a pre-check would be a second answer to it.
    """

    def test_a_real_store_answers_404_for_a_row_it_does_not_have(self, settings_app):
        """Against SQLite, not a mock: the id is the one with no users row."""
        client, _storage = settings_app

        missing = client.put(
            "/api/users/999/preferences", json={"series_in_order": False}
        )
        seeded = client.put("/api/users/1/preferences", json={"series_in_order": False})

        assert missing.status_code == 404
        assert missing.json()["detail"] == "User not found."
        assert seeded.status_code == 200
        assert seeded.json()["series_in_order"] is False

    @pytest.mark.parametrize("user_id", [0, -1])
    def test_a_non_positive_user_id_is_rejected(self, client, mock_components, user_id):
        """A path id matching no row is validation's answer, not storage's."""
        merge = back_mock_preference_store(mock_components["storage"])

        response = client.put(
            f"/api/users/{user_id}/preferences", json={"series_in_order": False}
        )

        assert response.status_code == 422
        merge.assert_not_called()


def test_list_users(client, mock_components):
    """Test GET /api/users returns user list."""
    mock_components["storage"].get_all_users = Mock(
        return_value=[
            {"id": 1, "username": "default", "display_name": "Default User"},
            {"id": 2, "username": "alice", "display_name": "Alice"},
        ]
    )
    # The password stamp is fetched per row, and an unstubbed Mock is not
    # subscriptable.
    mock_components["storage"].accounts.describe = Mock(return_value=None)

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
        Recommendation(
            item=mock_item,
            score=0.85,
            reasoning="Recommended highly similar",
            score_breakdown={"genre_match": 0.9, "creator_match": 0.5},
        )
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
        Recommendation(
            item=mock_item,
            score=0.2,
            reasoning="Recommended",
            score_breakdown={"genre_match": 0.9},
            variety_penalty=0.8,
        )
    ]
    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    response = client.get("/api/recommendations?type=book&count=1")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["variety_penalty"] == 0.8


def test_recommendations_with_user_id(client, mock_components):
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        Recommendation(
            item=mock_item, score=0.85, reasoning="Recommended highly similar"
        )
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


# ---------------------------------------------------------------------------
# GET /api/items/{db_id} — Single item retrieval
# ---------------------------------------------------------------------------


def test_get_single_item(client, mock_components):
    """GET /api/items/{db_id} returns a single content item."""
    mock_item = ContentItem(
        id="ext_1",
        external_ids=[ExternalId(source="goodreads_csv", external_id="ext_1")],
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
    assert data["external_ids"] == [{"source": "goodreads_csv", "external_id": "ext_1"}]
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
        rating=UNSET,
        review=UNSET,
        seasons_watched=None,
        genres=None,
        tags=None,
        description=None,
        release_year=None,
        creator=None,
        user_id=1,
    )


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
        rating=UNSET,
        review=UNSET,
        seasons_watched=[1, 2, 3],
        genres=None,
        tags=None,
        description=None,
        release_year=None,
        creator=None,
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


def test_edit_rejects_blank_review_regression(client, mock_components):
    """PATCH /api/items/{db_id} rejects a review that is empty or all spaces.

    Regression: ``review`` was bounded above but not below, so a hand-written
    request storing ``""`` reached the state ``library edit --review ""``
    already refuses — an empty string reads as a review the user wrote, and it
    blocks any later import from filling the field in. The request model now
    requires a non-blank review, so clearing one is only ever the explicit
    null the CLI spells ``--clear-review``.
    """
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)

    empty = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "completed", "review": ""},
    )
    assert empty.status_code == 400
    assert "null" in empty.json()["detail"]

    whitespace = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "completed", "review": "   "},
    )
    assert whitespace.status_code == 400

    mock_components["storage"].update_item_from_ui.assert_not_called()


def test_edit_item_status_preserves_rating_regression(client, mock_components):
    """PATCH with only a status leaves the stored rating and review alone.

    Bug reported: a status-only edit from the library UI silently nulled the
    item's rating and review.
    Root cause: the endpoint always forwarded request.rating / request.review,
    both defaulting to None, and storage wrote whatever it was handed — so an
    omitted field and a cleared field were indistinguishable.
    Fix: fields absent from the request body are forwarded as UNSET, which
    storage leaves untouched; an explicit null still clears.
    """
    updated_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Rated Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        review="Loved it",
    )
    mock_components["storage"].update_item_from_ui = Mock(
        spec=StorageManager.update_item_from_ui, return_value=True
    )
    mock_components["storage"].get_content_item = Mock(
        spec=StorageManager.get_content_item, return_value=updated_item
    )

    response = client.patch("/api/items/42?user_id=1", json={"status": "completed"})

    assert response.status_code == 200
    data = response.json()
    assert data["rating"] == 5
    assert data["review"] == "Loved it"

    call_kwargs = mock_components["storage"].update_item_from_ui.call_args[1]
    assert call_kwargs["rating"] is UNSET
    assert call_kwargs["review"] is UNSET


def test_edit_item_explicit_null_clears_rating(client, mock_components):
    """PATCH with an explicit null rating still clears it (the edit dialog's path)."""
    updated_item = ContentItem(
        id="ext_1",
        db_id=42,
        title="Unrated Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
    )
    mock_components["storage"].update_item_from_ui = Mock(
        spec=StorageManager.update_item_from_ui, return_value=True
    )
    mock_components["storage"].get_content_item = Mock(
        spec=StorageManager.get_content_item, return_value=updated_item
    )

    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "completed", "rating": None, "review": None},
    )

    assert response.status_code == 200
    call_kwargs = mock_components["storage"].update_item_from_ui.call_args[1]
    assert call_kwargs["rating"] is None
    assert call_kwargs["review"] is None


def test_edit_invalid_status(client, mock_components):
    """PATCH /api/items/{db_id} returns 400 for invalid status."""
    response = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "invalid_status"},
    )
    assert response.status_code == 400
    assert "Invalid status" in response.json()["detail"]


class TestEditRouteCompletesEverySeasonRegression:
    """Completing a show over PATCH left the checklist partial (#123).

    Symptom: a stated completed kept the old half-ticked list. Cause: status
    derived from seasons, never the reverse. Fix: the stated side fills the
    other.
    """

    def test_patch_completed_ticks_every_season_regression(
        self, mock_components, tmp_path
    ):
        """PATCH {"status": "completed"} marks every season of the show watched."""
        storage = StorageManager(sqlite_path=tmp_path / "edit.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="show-1",
                title="The Expanse",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={"seasons": 5, "seasons_watched": [1, 2]},
            ),
            user_id=1,
        )
        client = _client_on(mock_components["app"], storage)

        response = client.patch(
            f"/api/items/{db_id}?user_id=1", json={"status": "completed"}
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "completed"
        assert data["seasons_watched"] == [1, 2, 3, 4, 5]


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


def test_list_items_invalid_enrichment_returns_422(client, mock_components):
    """GET /api/items?enrichment=bogus is rejected at the API boundary."""
    response = client.get("/api/items?user_id=1&enrichment=bogus")
    assert response.status_code == 422


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
        rating=UNSET,
        review=UNSET,
        seasons_watched=None,
        genres=["Drama"],
        tags=["slow-burn"],
        description="Hand written.",
        release_year=None,
        creator=None,
        user_id=1,
    )


def test_edit_rejects_oversized_manual_metadata(client, mock_components):
    """PATCH /api/items/{db_id} rejects manual metadata above the model caps.

    Bounds the manual-edit fields at the API boundary: at most 50 genres and
    100 tags, each genre/tag string at most 100 chars, and a description at
    most 10000 chars. Each over-cap payload must be refused before any storage
    write. The review bound is checked with the rest of the dialog's own
    refusals, which are worded for it to render.
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
        app_state.config["inputs"]["gog"] = {"plugin": "gog", "enabled": True}

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
            mock_components["storage"], "super_secret_token", source_id="gog"
        )

    def test_exchange_succeeds_with_readonly_config(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Regression test: GOG exchange succeeds even when config is read-only.

        Bug: Docker mounts config read-only, causing OSError when
        update_config_with_token tried to write. Now tokens go to DB.
        """
        app_state.config["inputs"]["gog"] = {"plugin": "gog", "enabled": True}

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
        app_state.config["inputs"]["gog"] = {"plugin": "gog", "enabled": True}

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
        app_state.config["inputs"]["gog"] = {"plugin": "gog", "enabled": True}

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
        app_state.config["inputs"]["gog"] = {"plugin": "gog", "enabled": False}

        response = client.post("/api/gog/exchange", json={"code_or_url": "some_code"})

        assert response.status_code == 400
        assert "not enabled" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Pagination and Sorting Tests (8I)
# ---------------------------------------------------------------------------


class TestPaginationAndSorting:
    """Tests for pagination offset and sort_by query params on /api/items."""

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

    def test_over_long_search_term_is_rejected_regression(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """An unbounded search term is a free scan of the whole library.

        Bug reported: the search parameter had no maximum length. Matching
        normalizes both sides and then slides a SequenceMatcher window over
        every candidate title, and the query has no SQL LIMIT to shorten the
        candidate set, so the term's length multiplies the cost of a scan an
        anonymous caller can start.
        Root cause: ``search`` was declared as a plain optional string.
        Fix: the parameter is bounded, and the CLI's ``--search`` refuses the
        same length so the two interfaces agree on what a valid search is.
        """
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=[]
        )

        response = client.get(
            "/api/items", params={"search": "x" * (MAX_SEARCH_LENGTH + 1)}
        )

        assert response.status_code == 422
        mock_components["storage"].get_content_items.assert_not_called()

    def test_search_term_at_the_limit_is_accepted(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """The bound is inclusive, so a term of exactly the limit still runs."""
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=[]
        )
        term = "x" * MAX_SEARCH_LENGTH

        response = client.get("/api/items", params={"search": term})

        assert response.status_code == 200
        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["search"] == term


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


def test_recommendations_count_at_max_is_allowed(client, mock_components):
    """GET /api/recommendations allows count == max_count (boundary)."""
    app_state.config["recommendations"] = {"max_count": 5}
    mock_components["engine"].generate_recommendations.return_value = []
    mock_components["storage"].get_user_preference_config.return_value = None
    mock_components["storage"].get_completed_items.return_value = []

    response = client.get("/api/recommendations?type=book&count=5")
    assert response.status_code == 200


def _rec_record(item: ContentItem) -> Recommendation:
    """Wrap a ContentItem in the recommendation record the engine emits."""
    return Recommendation(
        item=item,
        score=0.85,
        reasoning="Rule-based reasoning",
        score_breakdown={"genre_match": 0.9},
    )


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
        _rec_record(season_item)
    ]
    mock_components["storage"].get_user_preference_config.return_value = None

    response = client.get("/api/recommendations?type=tv_show&count=5")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["title"] == "The Expanse (Season 1)"
    assert body[0]["db_id"] == 42


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

    def test_csv_download_neutralises_a_formula_cell_regression(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Bug: the download body carried a title verbatim, so a spreadsheet
        evaluated it. Root cause: the CSV writer emitted every cell as stored.
        Fix: an apostrophe guards a leading formula character.
        """
        mock_components["storage"].get_content_items = Mock(
            return_value=[
                ContentItem(
                    id="1",
                    title='=HYPERLINK("http://evil","x")',
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    metadata={},
                )
            ]
        )

        response = client.get("/api/items/export?type=book&format=csv")

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert response.status_code == 200
        assert rows[0]["title"] == '\'=HYPERLINK("http://evil","x")'

    def test_invalid_format_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Invalid export format returns 400 error."""
        response = client.get("/api/items/export?type=book&format=xml")

        assert response.status_code == 400
        assert "Invalid format" in response.json()["detail"]


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
            mock_manager.is_running.return_value = False
            mock_manager.start_sync.return_value = (False, "Sync already in progress")
            mock_get_sync_manager.return_value = mock_manager

            with patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ):
                response = client.post("/api/update", json={"source": "goodreads_rss"})

            assert response.status_code == 409
            assert response.json()["detail"] == "A sync is already in progress"
            assert mock_manager.start_sync.call_args.args[0] == "Goodreads Rss"

    def test_update_allows_different_sources_concurrently(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """A second source is accepted while a different source is running.

        Plants a real RUNNING job for Steam in the global SyncManager
        before triggering a Goodreads RSS sync. The endpoint must reject
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
            "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
            return_value=[],
        ):
            # Drop the captured execute_multi_source_sync into a no-op so
            # the second sync's daemon doesn't try to actually run.
            with patch(
                "src.web.sync_dispatch.execute_multi_source_sync",
                return_value=[
                    SyncJob(source="Goodreads Rss", status=SyncStatus.RUNNING)
                ],
            ):
                response = client.post("/api/update", json={"source": "goodreads_rss"})

        assert response.status_code == 200, response.text
        assert "Sync started" in response.json()["message"]
        # Manager now tracks both jobs; the Steam one is still running
        # and the Goodreads one was added on top.
        assert manager.is_running("Steam") is True
        assert "Goodreads Rss" in {
            job["source"] for job in manager.get_status()["jobs"]
        }


class TestSyncingEverythingWaitsForTheRunInFlight:
    def test_all_is_refused_while_one_source_is_already_syncing(
        self, client: TestClient, mock_components: dict
    ) -> None:
        manager = get_sync_manager()
        with patch("src.web.sync_manager.threading.Thread"):
            manager.start_sync(source="Steam", sync_function=lambda _job: 0)

        response = client.post("/api/update", json={"source": "all"})

        assert response.status_code == 409
        assert response.json()["detail"] == "A sync is already in progress"
        assert "All Sources" not in {
            job["source"] for job in manager.get_status()["jobs"]
        }

    def test_only_the_umbrella_job_refuses_a_source_not_a_namesake_of_its_label(
        self, client: TestClient, mock_components: dict
    ) -> None:
        manager = get_sync_manager()
        with patch("src.web.sync_manager.threading.Thread"):
            # The source id ``all_sources`` is legal, and humanizes to this.
            manager.start_sync(source="All Sources", sync_function=lambda _job: 0)
            allowed = client.post("/api/update", json={"source": "goodreads_rss"})
            manager.start_sync(source=ALL_SOURCES_KEY, sync_function=lambda _job: 0)
            refused = client.post("/api/update", json={"source": "goodreads_rss"})

        assert allowed.status_code != 409, allowed.text
        assert refused.status_code == 409
        assert refused.json()["detail"] == "A sync is already in progress"


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
                "src.web.sync_dispatch.execute_multi_source_sync",
                side_effect=self._make_capture(captured_kwargs, completion),
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post("/api/update", json={"source": "all"})
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200
        assert captured_kwargs.get("max_workers") == 7

    def test_request_body_max_workers_overrides_config(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """max_workers in the POST body overrides config (CLI parity)."""
        app_state.config["sync"] = {"max_workers": 2}

        captured_kwargs: dict = {}
        completion = threading.Event()
        with (
            patch(
                "src.web.sync_dispatch.execute_multi_source_sync",
                side_effect=self._make_capture(captured_kwargs, completion),
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
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


class TestSyncStatusNamesTheSourceThatFailedRegression:
    """Reported: a failed source showed the web a badge reading "1 error".

    Cause: errors reached the job as bare strings, keyed on the job label.
    Fix: each names its source, so the UI shows the CLI's text.
    """

    REMEDY = (
        "TLS verification failed for Sonarr at https://sonarr.example: bad "
        "handshake. Set verify_ssl to false if the certificate is not "
        "publicly trusted."
    )

    def test_a_run_that_synced_items_still_reports_the_remedy(
        self, client: TestClient, mock_components: dict
    ) -> None:
        completion = threading.Event()

        def fake_execute(
            *, result_callback: SyncResultCallback, **_kwargs: object
        ) -> list[SyncResult]:
            try:
                result_callback(SyncResult(source_name="Sonarr", errors=[self.REMEDY]))
                return [SyncResult(source_name="Goodreads Csv", items_synced=3)]
            finally:
                completion.set()

        with (
            patch(
                "src.web.sync_dispatch.execute_multi_source_sync",
                side_effect=fake_execute,
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post("/api/update", json={"source": "all"})
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200
        # The run saved items, which is the shape that used to leave the
        # message nowhere in the response at all.
        job = client.get("/api/sync/status").json()["jobs"][0]
        assert job["status"] == "completed"
        assert job["errors"] == [{"source": "Sonarr", "message": self.REMEDY}]


class TestUpdateEndpointRecordsTheRun:
    def test_a_web_sync_records_the_run_the_cli_would_have(
        self, client: TestClient, mock_components: dict
    ) -> None:
        storage = mock_components["storage"]
        recorded = threading.Event()
        storage.sync_runs.record.side_effect = lambda *_args, **_kwargs: recorded.set()
        storage.save_content_item_outcome.return_value = SavedItem(
            db_id=1, outcome=SaveOutcome.ADDED
        )

        with (
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.fetch",
                return_value=iter([make_item("Dune", item_id="b1")]),
            ),
        ):
            response = client.post("/api/update", json={"source": "goodreads_rss"})
            assert recorded.wait(timeout=5.0), "background sync did not record a run"

        assert response.status_code == 200, response.text
        args, kwargs = storage.sync_runs.record.call_args
        assert args == (1, "goodreads_rss")
        assert kwargs["status"] == "completed"


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


class TestExchangeEpicTokenEndpoint:
    """Tests for POST /api/epic/exchange endpoint security behavior."""

    def test_successful_exchange_saves_to_db(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Token is saved to DB and never returned in response."""
        app_state.config["inputs"]["epic_games"] = {
            "plugin": "epic_games",
            "enabled": True,
        }

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
            mock_components["storage"], "super_secret_token", source_id="epic_games"
        )

    def test_auth_error_returns_generic_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Auth failure returns generic 400 without leaking error details."""
        app_state.config["inputs"]["epic_games"] = {
            "plugin": "epic_games",
            "enabled": True,
        }

        with patch(
            "src.web.api.extract_epic_code",
            side_effect=EpicAuthError("Internal details that must not leak"),
        ):
            response = client.post("/api/epic/exchange", json={"code_or_json": "bad"})

        assert response.status_code == 400
        body = response.json()
        assert body["detail"] == "Epic Games authentication failed"
        assert "Internal details" not in str(body)

    def test_epic_not_enabled_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Requesting exchange when Epic is disabled returns 400."""
        app_state.config["inputs"]["epic_games"] = {
            "plugin": "epic_games",
            "enabled": False,
        }

        response = client.post("/api/epic/exchange", json={"code_or_json": "some_code"})

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "Epic Games is not enabled in the current configuration."
        )

    def test_unexpected_error_returns_500(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Unexpected errors produce a generic 500 without leaking internals."""
        app_state.config["inputs"]["epic_games"] = {
            "plugin": "epic_games",
            "enabled": True,
        }

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
        app_state.config["inputs"]["epic_games"] = {
            "plugin": "epic_games",
            "enabled": True,
        }

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
            mock_components["storage"], "super_secret_token", source_id="epic_games"
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


class TestAuthDisconnectEndpoints:
    """Tests for DELETE /api/gog/token and /api/epic/token (matches CLI auth disconnect)."""

    @pytest.fixture(autouse=True)
    def oauth_sources(self, mock_components):
        """Declare the three sources these deletes address.

        The route resolves the id before deleting anything, so an undeclared
        one is refused ahead of storage.
        """
        app_state.config["inputs"].update(
            {
                "gog": {"plugin": "gog", "enabled": True},
                "epic_games": {"plugin": "epic_games", "enabled": True},
                "trakt": {"plugin": "trakt", "enabled": True},
            }
        )

    @pytest.mark.parametrize(
        ("provider", "source_id"),
        [("gog", "trakt"), ("epic", "gog"), ("trakt", "epic_games")],
    )
    def test_disconnect_refuses_a_source_running_another_plugin(
        self, client, mock_components, provider, source_id
    ):
        """The id is the credential key, so each route owns which ones it may use."""
        storage = mock_components["storage"]
        storage.credentials.delete.return_value = True

        response = client.delete(f"/api/{provider}/token?source_id={source_id}")

        assert response.status_code == 404, response.text
        storage.credentials.delete.assert_not_called()

    def test_gog_disconnect_success(self, client, mock_components):
        """DELETE /api/gog/token removes stored refresh token."""
        storage = mock_components["storage"]
        storage.credentials.delete.return_value = True

        response = client.delete("/api/gog/token")

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "GOG disconnected."}
        storage.credentials.delete.assert_called_once_with(1, "gog", "refresh_token")

    def test_gog_disconnect_not_connected(self, client, mock_components):
        """DELETE /api/gog/token returns 404 when no credential exists."""
        mock_components["storage"].credentials.delete.return_value = False

        response = client.delete("/api/gog/token")

        assert response.status_code == 404

    def test_gog_disconnect_custom_user_id(self, client, mock_components):
        """user_id query parameter is forwarded to storage."""
        storage = mock_components["storage"]
        storage.credentials.delete.return_value = True

        response = client.delete("/api/gog/token?user_id=5")

        assert response.status_code == 200
        storage.credentials.delete.assert_called_once_with(5, "gog", "refresh_token")


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
            mock_components["storage"], "refresh-xyz", source_id="trakt", user_id=1
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


# Sensitive and non-sensitive leaves reused across the settings endpoint tests.
_SETTINGS_SECRET_KEY = "enrichment.providers.tmdb.api_key"
_SETTINGS_INT_KEY = "recommendations.default_count"


class TestSettingsEndpoints:
    """Global settings API: grouped view, updates + live-apply, reset, secrets.

    Drives ``create_app`` with a real isolated temp-DB StorageManager (mirrors
    the per-source config suite) so persistence and secret masking are exercised
    end-to-end without mocks.
    """

    @pytest.fixture()
    def settings_env(self, tmp_path: Path):
        reset_sync_manager()
        storage = StorageManager(sqlite_path=tmp_path / "settings.db")
        config = {
            "storage": {"database_path": str(tmp_path / "settings.db")},
            "recommendations": {"default_count": 5, "max_count": 20},
            "web": {"host": "127.0.0.1", "port": 18473},
        }
        with booted_web_app(storage, config) as app:
            yield authenticated_client(app), storage, config
        reset_sync_manager()

    def _find(self, body: dict, key: str) -> dict:
        for section in body["sections"]:
            for setting in section["settings"]:
                if setting["key"] == key:
                    return setting
        raise AssertionError(f"{key} not in settings body")

    def test_get_grouped_shape_and_masked_secret(self, settings_env) -> None:
        client, _storage, _config = settings_env

        response = client.get("/api/settings")

        assert response.status_code == 200
        body = response.json()
        assert body["sections"][0]["section"] == "recommendations"

        numeric = self._find(body, _SETTINGS_INT_KEY)
        assert numeric["value"] == 5
        assert numeric["db_overridden"] is False
        assert "has_secret" not in numeric

        secret = self._find(body, _SETTINGS_SECRET_KEY)
        assert secret["sensitive"] is True
        assert secret["has_secret"] is False
        assert "value" not in secret
        assert "db_overridden" not in secret

    def test_put_persists_and_live_applies(self, settings_env) -> None:
        client, storage, config = settings_env

        response = client.put("/api/settings", json={"updates": {_SETTINGS_INT_KEY: 7}})

        assert response.status_code == 200
        assert storage.settings.get(_SETTINGS_INT_KEY) == 7
        # Live-applied to the running config held in app_state.
        assert config["recommendations"]["default_count"] == 7
        setting = self._find(response.json(), _SETTINGS_INT_KEY)
        assert setting["value"] == 7
        assert setting["db_overridden"] is True

    def test_put_invalid_returns_422_no_partial_write(self, settings_env) -> None:
        client, storage, config = settings_env

        response = client.put(
            "/api/settings",
            json={"updates": {_SETTINGS_INT_KEY: 9, "recommendations.max_count": 0}},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["key"] == "recommendations.max_count"
        # Nothing persisted and the running config is untouched.
        assert storage.settings.list() == {}
        assert config["recommendations"]["default_count"] == 5

    def test_a_rejected_update_leaves_the_next_one_writable(self, settings_env) -> None:
        """``SettingsValidationError`` unwinds ``writable_config``, lock included.

        A leaked ``_config_lock`` would hang the next settings write rather
        than name a failure, so the lock is asserted free between the two PUTs
        and the second one landing is the behavioural half.
        """
        client, storage, _config = settings_env

        rejected = client.put(
            "/api/settings", json={"updates": {"recommendations.max_count": 0}}
        )
        assert not _config_lock.locked()

        accepted = client.put("/api/settings", json={"updates": {_SETTINGS_INT_KEY: 7}})

        assert rejected.status_code == 422
        assert accepted.status_code == 200
        assert storage.settings.get(_SETTINGS_INT_KEY) == 7

    def test_put_restart_required_persists_but_flagged(self, settings_env) -> None:
        client, storage, config = settings_env

        response = client.put(
            "/api/settings", json={"updates": {"logging.level": "DEBUG"}}
        )

        assert response.status_code == 200
        assert storage.settings.get("logging.level") == "DEBUG"
        # Restart-required: persisted but the running config is unchanged.
        assert config["logging"]["level"] == "INFO"
        setting = self._find(response.json(), "logging.level")
        assert setting["restart_required"] is True
        assert setting["db_overridden"] is True
        assert setting["value"] == "INFO"

    def test_delete_resets_to_default(self, settings_env) -> None:
        client, storage, config = settings_env
        client.put("/api/settings", json={"updates": {_SETTINGS_INT_KEY: 7}})

        response = client.delete(f"/api/settings/{_SETTINGS_INT_KEY}")

        assert response.status_code == 200
        assert storage.settings.get(_SETTINGS_INT_KEY) is None
        assert config["recommendations"]["default_count"] == 5
        setting = self._find(response.json(), _SETTINGS_INT_KEY)
        assert setting["db_overridden"] is False
        assert setting["value"] == 5

    def test_delete_unknown_key_returns_404(self, settings_env) -> None:
        client, _storage, _config = settings_env

        response = client.delete("/api/settings/web.nonsense")

        assert response.status_code == 404

    def test_the_file_import_allowlist_is_not_reachable(self, settings_env) -> None:
        """``security.allowed_source_roots`` is config.yaml only, by design.

        A caller able to widen it could point a file-based source anywhere and
        read the host's filesystem, so it is deliberately not a registry leaf.
        """
        client, storage, _config = settings_env
        key = "security.allowed_source_roots"
        before = get_allowed_source_roots()

        listed = client.get("/api/settings").json()
        assert not any(
            setting["key"] == key
            for section in listed["sections"]
            for setting in section["settings"]
        )

        response = client.put("/api/settings", json={"updates": {key: ["/"]}})

        assert response.status_code == 422
        assert response.json()["detail"] == {"key": key, "reason": "unknown setting"}
        assert storage.settings.list() == {}
        assert get_allowed_source_roots() == before

    def test_a_malformed_cors_origin_is_refused_over_http(self, settings_env) -> None:
        """The origin grammar is tested at the service; this is the door."""
        client, storage, _config = settings_env

        response = client.put(
            "/api/settings",
            json={"updates": {"web.allowed_origins": ["not an origin"]}},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["key"] == "web.allowed_origins"
        assert storage.settings.get("web.allowed_origins") is None

    def test_delete_sensitive_key_is_graceful_not_500(self, settings_env) -> None:
        """DELETE /api/settings/{key} on a sensitive key must not 500.

        Reported: resetting a secret leaf via the web returns 500 Internal
        Server Error, while the CLI ``settings reset <secret>`` rejects it
        cleanly (exit != 0) — a CLI/UI parity break and an ungraceful crash.

        Root cause: ``reset_setting_endpoint`` only guards ``get_entry(key) is
        None`` (404). A sensitive key IS registered, so it falls through to
        ``reset_setting``, which raises ``SettingsValidationError`` ("use the
        secret endpoint for secrets"). That exception is uncaught in the DELETE
        handler (unlike the PUT and secret handlers), so FastAPI returns 500.

        Expected fix: map the sensitive-key rejection to 422 with the offending
        key + reason, mirroring the PUT ``/api/settings`` handler so the settings
        API has one uniform ``SettingsValidationError`` -> HTTP contract. This
        test asserts that exact 422 shape and that no server crash leaks.
        """
        client, _storage, _config = settings_env

        # The defect surfaces either as an uncaught exception (TestClient
        # re-raises) or, with raise_server_exceptions off, a 500. Both are
        # failures; the fix should yield a graceful 422 instead.
        try:
            response = client.delete(f"/api/settings/{_SETTINGS_SECRET_KEY}")
        except Exception as error:  # noqa: BLE001 - defect: unhandled in handler
            pytest.fail(
                "resetting a sensitive key raised an uncaught server error "
                f"instead of a graceful 422: {error!r}"
            )

        assert response.status_code == 422, (
            "resetting a sensitive key should map to 422, "
            f"got {response.status_code}"
        )
        assert response.json()["detail"] == {
            "key": _SETTINGS_SECRET_KEY,
            "reason": "use the secret endpoint for secrets",
        }

    def test_secret_put_and_delete_are_masked(self, settings_env) -> None:
        client, storage, _config = settings_env

        put = client.put(
            "/api/settings/secret",
            json={"key": _SETTINGS_SECRET_KEY, "value": "tmdb-key"},
        )

        assert put.status_code == 204
        assert storage.secrets.has(_SETTINGS_SECRET_KEY) is True
        # The secret is never persisted in the plaintext settings table.
        assert storage.settings.list() == {}
        # And it surfaces only as has_secret, never as a value.
        secret = self._find(client.get("/api/settings").json(), _SETTINGS_SECRET_KEY)
        assert secret["has_secret"] is True
        assert "value" not in secret

        delete = client.delete(f"/api/settings/secret/{_SETTINGS_SECRET_KEY}")

        assert delete.status_code == 204
        assert storage.secrets.has(_SETTINGS_SECRET_KEY) is False

    def test_secret_put_rejects_non_sensitive_key(self, settings_env) -> None:
        client, storage, _config = settings_env

        response = client.put(
            "/api/settings/secret",
            json={"key": _SETTINGS_INT_KEY, "value": "nope"},
        )

        assert response.status_code == 400
        assert storage.settings.list() == {}

    def test_secret_delete_rejects_non_sensitive_key(self, settings_env) -> None:
        client, _storage, _config = settings_env

        response = client.delete(f"/api/settings/secret/{_SETTINGS_INT_KEY}")

        assert response.status_code == 400

    def test_a_reset_survives_a_reload_that_lands_mid_request(
        self, settings_env
    ) -> None:
        """The reset writer's half of what the config lock guarantees.

        ``TestConfigReloadRacingASettingsSaveRegression`` pins the same thing
        for ``PUT``: a writer must publish into the config the server is
        running, not into the one its ``RequiredConfig`` dependency resolved
        before ``POST /api/config/reload`` replaced it. Both writers resolve
        the binding inside ``writable_config`` for that reason, and this is the
        one that says so for ``DELETE``.
        """
        client, storage, _config = settings_env
        # Pinned, not just performed: this test asserts an absence, and without
        # the override in place the const default is already what both final
        # assertions look for — so a broken arrangement reads as a pass.
        arrange = client.put("/api/settings", json={"updates": {_SETTINGS_INT_KEY: 11}})
        assert arrange.status_code == 200
        assert storage.settings.get(_SETTINGS_INT_KEY) == 11
        reset_holds_its_config = threading.Event()
        reload_finished = threading.Event()

        def hand_over_the_config_then_pause() -> dict[str, Any] | None:
            config = app_state.config
            reset_holds_its_config.set()
            reload_finished.wait(timeout=_STALL_TIMEOUT_SECONDS)
            return config

        with (
            patch("src.web.guards.get_config", hand_over_the_config_then_pause),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            reset = pool.submit(client.delete, f"/api/settings/{_SETTINGS_INT_KEY}")
            assert reset_holds_its_config.wait(timeout=_STALL_TIMEOUT_SECONDS)

            assert client.post("/api/config/reload").status_code == 200

            reload_finished.set()
            assert reset.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200

        assert storage.settings.get(_SETTINGS_INT_KEY) is None
        assert get_leaf(
            app_state.config, tuple(_SETTINGS_INT_KEY.split("."))
        ) == default_of(
            _SETTINGS_INT_KEY
        ), "the database and the running config disagree about a reset setting"


_STORAGE_UNAVAILABLE = "Storage unavailable"
_CONFIG_UNAVAILABLE = "Config unavailable"
_ENGINE_UNAVAILABLE = "Recommendation engine unavailable"

# The 503 message each guarded component produces, keyed by its AppState field.
_UNAVAILABLE_DETAIL = {
    "storage": _STORAGE_UNAVAILABLE,
    "config": _CONFIG_UNAVAILABLE,
    "engine": _ENGINE_UNAVAILABLE,
}


@dataclass(frozen=True)
class _Endpoint:
    """One API endpoint and the components it needs initialised.

    ``requires`` names ``AppState`` fields, and is empty for an endpoint that
    needs no initialised component at all. Which of several a handler names
    first is not observable to a caller, so the tests below assert the set and
    never the order — reordering two adjacent guard calls changes nothing a
    caller can see and must not turn anything red.
    """

    method: str
    route: str
    requires: tuple[str, ...] = ()
    url: str | None = None
    body: dict[str, object] | None = None

    @property
    def target(self) -> str:
        return self.url or self.route

    @property
    def details(self) -> set[str]:
        return {_UNAVAILABLE_DETAIL[component] for component in self.requires}


_GUARDED_ENDPOINTS = [
    _Endpoint(
        "GET",
        "/api/recommendations",
        ("engine",),
        url="/api/recommendations?type=book",
    ),
    _Endpoint("GET", "/api/users", ("storage",)),
    _Endpoint(
        "PATCH",
        "/api/users/{user_id}",
        ("storage",),
        url="/api/users/1",
        body={"username": "owner", "display_name": "Owner"},
    ),
    _Endpoint(
        "PUT",
        "/api/users/{user_id}/password",
        ("storage",),
        url="/api/users/1/password",
        body={"current_password": "old", "new_password": "new-password"},
    ),
    _Endpoint("GET", "/api/items", ("storage",)),
    _Endpoint(
        "GET",
        "/api/items/export",
        ("storage",),
        url="/api/items/export?type=book",
    ),
    # No body: the upload is multipart, and both guards answer before the form
    # is parsed, which is the order this asserts.
    _Endpoint("POST", "/api/import", ("storage", "config")),
    _Endpoint(
        "PATCH",
        "/api/items/{db_id}/ignore",
        ("storage",),
        url="/api/items/1/ignore",
        body={"ignored": True},
    ),
    _Endpoint("GET", "/api/items/{db_id}", ("storage",), url="/api/items/1"),
    _Endpoint(
        "PATCH",
        "/api/items/{db_id}",
        ("storage",),
        url="/api/items/1",
        body={"status": "completed"},
    ),
    _Endpoint("GET", "/api/duplicates", ("storage",)),
    _Endpoint("GET", "/api/duplicates/declined", ("storage",)),
    _Endpoint(
        "POST",
        "/api/duplicates/declined",
        ("storage",),
        body={"one_id": 1, "other_id": 2},
    ),
    _Endpoint(
        "DELETE",
        "/api/duplicates/declined/{one_id}/{other_id}",
        ("storage",),
        url="/api/duplicates/declined/1/2",
    ),
    _Endpoint("GET", "/api/merges", ("storage",)),
    _Endpoint(
        "POST",
        "/api/merges",
        ("storage",),
        body={"survivor_id": 1, "absorbed_id": 2},
    ),
    _Endpoint("DELETE", "/api/merges/{merge_id}", ("storage",), url="/api/merges/1"),
    _Endpoint(
        "GET",
        "/api/users/{user_id}/preferences",
        ("storage",),
        url="/api/users/1/preferences",
    ),
    _Endpoint(
        "PUT",
        "/api/users/{user_id}/preferences",
        ("storage",),
        url="/api/users/1/preferences",
        body={},
    ),
    _Endpoint(
        "POST",
        "/api/complete",
        ("storage",),
        body={"content_type": "book", "title": "Dune"},
    ),
    _Endpoint("POST", "/api/update", ("storage", "config"), body={"source": "all"}),
    _Endpoint("GET", "/api/sync/sources", ("config", "storage")),
    # Both read both halves: creating refuses an id YAML already holds, and
    # deleting decides off the sources left whether a credential stranded
    # under the plugin name goes with the last one of them.
    _Endpoint(
        "POST",
        "/api/sync/sources",
        ("storage", "config"),
        body={"id": "my_books", "plugin": "goodreads_rss"},
    ),
    _Endpoint(
        "DELETE",
        "/api/sync/sources/{source_id}",
        ("storage", "config"),
        url="/api/sync/sources/my_books",
    ),
    # Every route below resolves the id through ``require_plugin``, which
    # reads both halves of the truth and so guards both.
    _Endpoint(
        "GET",
        "/api/sync/sources/{source_id}/schema",
        ("storage", "config"),
        url="/api/sync/sources/my_books/schema",
    ),
    _Endpoint(
        "GET",
        "/api/sync/sources/{source_id}/config",
        ("storage", "config"),
        url="/api/sync/sources/my_books/config",
    ),
    _Endpoint(
        "POST",
        "/api/sync/sources/{source_id}/migrate",
        ("storage", "config"),
        url="/api/sync/sources/my_books/migrate",
    ),
    _Endpoint(
        "PUT",
        "/api/sync/sources/{source_id}/config",
        ("storage", "config"),
        url="/api/sync/sources/my_books/config",
        body={"values": {}},
    ),
    _Endpoint(
        "PUT",
        "/api/sync/sources/{source_id}/secret/{key}",
        ("storage", "config"),
        url="/api/sync/sources/my_books/secret/api_key",
        body={"value": "secret"},
    ),
    _Endpoint(
        "DELETE",
        "/api/sync/sources/{source_id}/secret/{key}",
        ("storage", "config"),
        url="/api/sync/sources/my_books/secret/api_key",
    ),
    _Endpoint(
        "PUT",
        "/api/sync/sources/{source_id}/enabled",
        ("storage", "config"),
        url="/api/sync/sources/my_books/enabled",
        body={"enabled": True},
    ),
    _Endpoint(
        "PUT",
        "/api/sync/sources/{source_id}/schedule",
        ("storage", "config"),
        url="/api/sync/sources/my_books/schedule",
        body={"interval": "daily"},
    ),
    _Endpoint("GET", "/api/settings", ("config", "storage")),
    _Endpoint("PUT", "/api/settings", ("config", "storage"), body={"updates": {}}),
    _Endpoint(
        "DELETE",
        "/api/settings/{key}",
        ("config", "storage"),
        url=f"/api/settings/{_SETTINGS_INT_KEY}",
    ),
    _Endpoint(
        "PUT",
        "/api/settings/secret",
        ("storage",),
        body={"key": _SETTINGS_SECRET_KEY, "value": "x"},
    ),
    _Endpoint(
        "DELETE",
        "/api/settings/secret/{key}",
        ("storage",),
        url=f"/api/settings/secret/{_SETTINGS_SECRET_KEY}",
    ),
    _Endpoint("POST", "/api/enrichment/start", ("storage", "config"), body={}),
    _Endpoint("GET", "/api/enrichment/stats", ("config", "storage")),
    _Endpoint("POST", "/api/enrichment/reset", ("storage",), body={}),
    _Endpoint("GET", "/api/gog/status", ("config", "storage")),
    _Endpoint(
        "POST",
        "/api/gog/exchange",
        ("config", "storage"),
        body={"code_or_url": "code"},
    ),
    _Endpoint("DELETE", "/api/gog/token", ("config", "storage")),
    _Endpoint("GET", "/api/epic/status", ("config", "storage")),
    _Endpoint(
        "POST",
        "/api/epic/exchange",
        ("config", "storage"),
        body={"code_or_json": "code"},
    ),
    _Endpoint("DELETE", "/api/epic/token", ("config", "storage")),
    _Endpoint("GET", "/api/trakt/status", ("config", "storage")),
    _Endpoint("POST", "/api/trakt/start-device-flow", ("config", "storage")),
    _Endpoint(
        "POST",
        "/api/trakt/poll-device-approval",
        ("config", "storage"),
        body={"device_code": "dev1234567"},
    ),
    _Endpoint("DELETE", "/api/trakt/token", ("config", "storage")),
    _Endpoint("GET", "/api/profile", ("storage",)),
    _Endpoint("POST", "/api/profile/regenerate", ("storage",)),
]

# Endpoints that serve off constants, the filesystem or a manager of their own,
# so an uninitialised component is not their problem.
_DEPENDENCY_FREE_ENDPOINTS = [
    _Endpoint("GET", "/api/status"),
    # Listed here rather than guarded: an unset ``config_path`` makes
    # ``reload_config`` return False, which the handler turns into a 500. Only
    # ``create_app`` sets that field, and it sets it or raises.
    _Endpoint("POST", "/api/config/reload"),
    _Endpoint("GET", "/api/plugins"),
    _Endpoint("GET", "/api/importers"),
    _Endpoint("GET", "/api/import/templates"),
    _Endpoint(
        "GET",
        "/api/import/templates/download",
        url="/api/import/templates/download?importer=csv_import&content_type=book",
    ),
    _Endpoint("GET", "/api/sync/status"),
    _Endpoint("POST", "/api/enrichment/stop"),
    _Endpoint("GET", "/api/enrichment/status"),
    _Endpoint("GET", "/api/themes"),
    _Endpoint("GET", "/api/themes/default"),
]


# The sign-in surface: guarded like any other route, but reachable without a
# session, because a signed-out browser has to reach it to stop being one.
_OPEN_ENDPOINTS = [
    _Endpoint("GET", "/api/auth/session", ("storage",)),
    _Endpoint(
        "POST",
        "/api/auth/setup",
        ("storage",),
        body={"username": "owner", "display_name": "", "password": "long enough"},
    ),
    _Endpoint(
        "POST",
        "/api/auth/login",
        ("storage",),
        body={"username": "owner", "password": "long enough"},
    ),
    _Endpoint("POST", "/api/auth/logout", ("storage",)),
]


def _endpoint_id(endpoint: _Endpoint) -> str:
    return f"{endpoint.method} {endpoint.route}"


# One case per (endpoint, component) pair, so every guard on a multi-component
# handler is exercised on its own rather than shadowed by the first one.
_GUARD_CASES = [
    pytest.param(endpoint, component, id=f"{_endpoint_id(endpoint)} [{component}]")
    for endpoint in _GUARDED_ENDPOINTS + _OPEN_ENDPOINTS
    for component in endpoint.requires
]


def _served_api_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``(method, path)`` under ``/api`` the app actually serves."""
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api")
        for method in route.methods
    }


_CLASSIFIED_API_ROUTES = {
    (endpoint.method, endpoint.route)
    for endpoint in _GUARDED_ENDPOINTS + _DEPENDENCY_FREE_ENDPOINTS + _OPEN_ENDPOINTS
}


def _clear_dependencies() -> None:
    """Drop every component but storage, ahead of a request.

    Storage stays because authentication reads the session out of it: gone, a
    503 answers before any handler's own guard runs.
    """
    app_state.config = None
    app_state.engine = None


class TestDependencyGuards:
    """Every uninitialised dependency answers 503, one message per dependency.

    An absent component is unavailability, not a server fault, and one server
    state has to read the same way everywhere: a 500 from ``/api/items`` and a
    503 from ``/api/profile`` described the identical outage two ways.
    """

    @pytest.mark.parametrize(("endpoint", "component"), _GUARD_CASES)
    def test_each_dependency_is_guarded_on_its_own(
        self, client, endpoint, component
    ) -> None:
        """Down alone, each component is named by every endpoint that needs it.

        Clearing all five at once only ever reaches a handler's first guard, so
        the second guard on a two-component handler could be deleted with the
        suite staying green. One component down at a time is what pins it.
        """
        setattr(app_state, component, None)

        response = client.request(endpoint.method, endpoint.target, json=endpoint.body)

        assert response.status_code == 503
        assert response.json()["detail"] == _UNAVAILABLE_DETAIL[component]

    @pytest.mark.parametrize("endpoint", _DEPENDENCY_FREE_ENDPOINTS, ids=_endpoint_id)
    def test_dependency_free_endpoint_still_answers(self, client, endpoint) -> None:
        """These serve off constants or a manager of their own, so they answer.

        ``< 500`` rather than ``!= 503``: a handler that grows a dependency and
        hand-rolls a 500 for it belongs in the guarded list, and this is what
        says so. The classification test alone only proves no route is
        unlisted, not that it is listed in the right place.
        """
        _clear_dependencies()
        # /config/reload re-reads whatever ``config_path`` names, so it is
        # pinned to the example file here rather than mocked out: a mocked
        # ``reload_config`` decides the assertion by itself, and an unpinned
        # path is the developer's real config.yaml.
        app_state.config_path = str(Path("config/example.yaml").resolve())

        response = client.request(endpoint.method, endpoint.target, json=endpoint.body)

        assert response.status_code < 500

    def test_status_reports_initializing_when_components_are_down(self, client) -> None:
        """``/api/status`` answers 200 and names the outage, rather than being it.

        The counterpart to every 503 above, and the reason it is not guarded:
        reporting which components are down is the whole of its contract, so a
        503 here would replace the report with the thing it reports on.
        """
        _clear_dependencies()

        response = client.get("/api/status")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "initializing"
        assert body["components"]["engine"] is False
        assert (
            body["components"]["storage"] is True
        ), "no caller reaches this route with storage down: it authenticated them"

    def test_every_api_route_is_classified(self, client, mock_components) -> None:
        assert _served_api_routes(mock_components["app"]) == _CLASSIFIED_API_ROUTES, (
            "a new handler is unlisted until someone classifies it, and the "
            "dependency-free list exempts it from every guard case above"
        )


_WRONG_SESSION = "wrong-session-0f0e0d0c0b0a09080706050403020100"

# The sign-in surface, which a signed-out browser has to reach to sign in.
_OPEN_API_ROUTES = {
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
}


def _authenticates(route: APIRoute) -> bool:
    """Whether ``require_session`` is anywhere in *route*'s dependency tree."""
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is require_session:
            return True
        pending.extend(dependency.dependencies)
    return False


def _exempt_api_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``/api`` route the app serves without demanding a session."""
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api")
        for method in route.methods
        if not _authenticates(route)
    }


class TestEveryApiRouteRequiresASession:
    """Attached to the routers, so a route is authenticated by being
    registered. Only the four sign-in routes are exempt: ``GET /api/status``
    publishes a fingerprint, and nothing here probes it signed out.
    """

    def test_a_request_with_an_unknown_cookie_is_401(self, mock_components) -> None:
        """A dead session is refused like an absent one.

        Every other route: ``test_the_sign_in_routes_are_the_only_exempt_ones``.
        """
        client = TestClient(
            mock_components["app"], cookies={SESSION_COOKIE: _WRONG_SESSION}
        )

        response = client.get("/api/items")

        assert response.status_code == 401

    def test_a_live_session_gets_past_authentication(self, client) -> None:
        """The negative control: the cases above must not pass on a dead app."""
        assert client.get("/api/status").status_code == 200

    def test_the_spa_shell_is_served_without_one(self, anonymous_client) -> None:
        """It is what collects the credentials, so it cannot require them.

        A browser navigating to ``/`` carries no cookie yet, and the shell
        holds no library data — only the sign-in form.
        """
        assert anonymous_client.get("/").status_code == 200

    def test_the_sign_in_routes_are_the_only_exempt_ones(self, mock_components) -> None:
        """Read off the dependency tree, so an exemption has to be deliberate.

        Named rather than counted: a new open route would otherwise arrive as
        an edit to a number, which is not a decision anybody reviews.
        """
        assert _exempt_api_routes(mock_components["app"]) == _OPEN_API_ROUTES


class TestTheSessionTokenStaysOutOfEverythingObservable:
    """The cookie is a credential, so nothing a caller or an operator reads
    back may carry it."""

    @pytest.fixture()
    def real_boot(self, mock_config, tmp_path, caplog):
        """Boot on a real database, capturing everything logged."""
        reset_sync_manager()
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        with caplog.at_level(logging.DEBUG):
            with booted_web_app(storage, mock_config) as app:
                yield app
        reset_sync_manager()

    def test_nothing_logged_while_signing_in_contains_it(
        self, real_boot, caplog
    ) -> None:
        """A token in the server log is a token in whoever reads the log."""
        app_state.storage.accounts.claim("owner", None, "correct horse battery")
        client = TestClient(real_boot)

        response = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "correct horse battery"},
        )

        assert response.status_code == 200
        assert client.cookies[SESSION_COOKIE] not in caplog.text

    def test_the_session_cookie_is_closed_to_javascript(self, real_boot) -> None:
        """An XSS that can read the cookie is an XSS that keeps the account."""
        response = authenticated_client(real_boot).post(
            "/api/auth/logout",
        )

        assert response.status_code == 204
        assert "httponly" in response.headers["set-cookie"].lower()


# The methods whose callers have never sent a body, so one appearing on them
# is a break rather than an addition.
_BODYLESS_METHODS = {"GET", "DELETE"}


class TestInjectedDependenciesStayOffTheWire:
    """A ``Depends`` parameter must not turn into something a caller sends.

    Drop the ``Depends`` from ``RequiredStorage``, ``RequiredEngine`` or
    ``ResolvedPlugin`` and FastAPI raises at route registration: those
    annotations are classes it cannot build a field from, so the mistake never
    reaches a caller. ``RequiredConfig`` is the quiet one. ``dict[str, Any]``
    is a shape FastAPI can serialise, so unrecognised it becomes a **required
    request body** — on ``GET /api/settings`` and ``DELETE
    /api/settings/{key}`` among others, breaking every existing caller of a
    route that has never had one. A body is not a parameter, so reading
    ``operation["parameters"]`` is exactly what misses it.
    """

    @staticmethod
    def _operations(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
        return [
            (method, route.path, route)
            for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api")
            for method in route.methods & _BODYLESS_METHODS
        ]

    def test_no_get_or_delete_route_asks_for_a_body(self, mock_components) -> None:
        """``body_field`` is the field FastAPI would populate from the request."""
        operations = self._operations(mock_components["app"])

        assert operations, "no bodyless /api operations to check"
        assert {
            f"{method} {path}"
            for method, path, route in operations
            if route.body_field is not None
        } == set()


class TestSourceReadGuardsRegression:
    """One server state, one answer, across every route on a source."""

    @pytest.mark.parametrize(
        "url",
        ["/api/sync/sources/my_books/schema", "/api/sync/sources/my_books/config"],
    )
    def test_read_reports_unavailable_rather_than_missing_regression(
        self, client, url
    ) -> None:
        """Regression: a source read 404s with storage down instead of 503.

        Bug reported: with ``app_state.storage`` unset, a read on a source that
        exists answered 404 "Source not found.", while every write on that same
        source answered 503 "Storage unavailable".
        Root cause: ``get_source_schema`` and ``get_source_config_endpoint``
        reached ``require_plugin`` with no guard in front of it.
        ``resolve_source_plugin`` reads the plugin name off storage, falling
        back to config, so with both ``None`` it resolved nothing and the
        handler raised 404 — blaming the caller for the server being down.
        Fix: ``require_plugin`` takes ``RequiredStorage`` itself, so no
        caller can reach the lookup before the outage has been reported.
        Authentication now reads the session out of storage and reports the
        same outage first; the caller-visible claim is what this holds, and
        ``require_plugin``'s own guard is read off the signature below.
        """
        _clear_dependencies()
        app_state.storage = None

        response = client.get(url)

        assert response.status_code == 503
        assert response.json()["detail"] == _STORAGE_UNAVAILABLE

    @pytest.mark.parametrize(
        "url",
        ["/api/sync/sources/my_books/schema", "/api/sync/sources/my_books/config"],
    )
    def test_read_reports_unavailable_with_config_down_regression(
        self, client, mock_components, url
    ) -> None:
        """Regression: config down left source reads answering off storage alone.

        Bug reported: the sweep above guarded storage and left ``get_config()``
        passing through, so with ``app_state.config`` unset a source read
        answered whatever the DB half alone could resolve — 404 "Source not
        found." for a YAML-only source, 200 off a stale-by-half view for a
        migrated one — while ``GET /api/sync/sources`` answered 503 for that
        same server state.
        Root cause: ``require_plugin`` guarded storage only, and
        ``resolve_source_plugin`` treats a missing config as "no YAML entry"
        rather than as an outage.
        Fix: ``require_plugin`` takes ``RequiredConfig`` too.
        The DB row is what makes this fail on the bug rather than on an id
        nothing could resolve: with it the lookup succeeds, so the 503 is the
        guard firing and nothing else.
        """
        mock_components["storage"].sources.get.return_value = {
            "source_id": "my_books",
            "plugin": "goodreads_rss",
            "enabled": 1,
        }
        app_state.config = None

        response = client.get(url)

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE

    def test_write_on_the_same_source_answers_503(self, client) -> None:
        """The other half of the disagreement: one server state, one resource.

        Paired with the config-down read above, which is the outage a request
        can still arrive during.
        """
        _clear_dependencies()

        response = client.post("/api/sync/sources/my_books/migrate")

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE


class TestDependencyGuardPrecedence:
    """The guard answers before anything else the request could be faulted for.

    A 400 or a 404 raised ahead of the guard reads as the caller's mistake, and
    the caller cannot tell it from one — which is how the same server state came
    to have several different answers in the first place.
    """

    def test_guard_precedes_the_handlers_own_rejections(self, client) -> None:
        """An unknown type and an over-max count still answer 503, not 400."""
        _clear_dependencies()

        response = client.get("/api/recommendations?type=bogus&count=999999")

        assert response.status_code == 503
        assert response.json()["detail"] == _ENGINE_UNAVAILABLE

    def test_the_guard_outranks_request_validation_too(self, client) -> None:
        """Even a request FastAPI would reject is answered with the outage.

        ``count=0`` fails the ``ge=1`` bound, and while the guards were called
        from the handler body that 422 came back: parsing finished before the
        handler ran, so the guard never got a say. A guard that IS the
        parameter is resolved with the rest of the dependency tree, which
        FastAPI does before validating the endpoint's own params — so the 503
        now outranks the 422 as well. This is the one answer the conversion
        changed, and it changed toward this class's rule rather than away from
        it: the caller is not faulted for a request the server could not have
        served whatever it said.
        """
        _clear_dependencies()

        response = client.get("/api/recommendations?type=book&count=0")

        assert response.status_code == 503
        assert response.json()["detail"] == _ENGINE_UNAVAILABLE

    def test_guard_precedes_lookup_of_an_unresolvable_source_id(self, client) -> None:
        """A non-ASCII id no source could carry reports the outage, not a miss."""
        _clear_dependencies()

        response = client.post("/api/sync/sources/%F0%9F%92%A9/migrate")

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE


class TestUnguardedReadsAreOptional:
    """``requires`` proves which components produce a 503; nothing there proves
    the unlisted ones were left out deliberately. Harden one of these reads
    into a guard and the 200 becomes a 503 with every case above still green.
    """

    def test_recommendations_serve_without_the_config(
        self, client, mock_components
    ) -> None:
        """The count bound falls back to the registered default without it.

        Storage is no longer cleared alongside it: authentication reads the
        session out of storage, so no request arrives with it down.
        """
        mock_components["engine"].generate_recommendations.return_value = []
        app_state.config = None

        response = client.get("/api/recommendations?type=book")

        assert response.status_code == 200
        assert response.json() == []

    def test_complete_serves_without_config(self, client, mock_components) -> None:
        """``get_feature_flags(None)`` falls back to the registered defaults."""
        mock_components["storage"].complete_content_item.return_value = 7
        app_state.config = None

        response = client.post(
            "/api/complete", json={"content_type": "book", "title": "Dune"}
        )

        assert response.status_code == 200
        assert response.json()["id"] == 7


class TestSourceCreateReadsBothHalvesRegression:
    """The last unguarded read on the sync-sources surface.

    Bug reported: ``POST /api/sync/sources`` passed ``get_config()`` straight
    into ``create_source``, which refuses an id YAML already defines. With
    config down that check saw no ``inputs`` section and read it as "no
    collision possible", so the create the server would have answered 409 to
    succeeded instead and planted a database source over the YAML one — the
    same one-state-two-answers shape the other source routes were swept for,
    except that here the wrong answer is a write.
    Root cause: the endpoint guarded storage only, so config being unreadable
    was indistinguishable from there being nothing to read.
    Fix: the endpoint takes ``RequiredConfig``, and is classified
    ``('storage', 'config')`` alongside every other route that reads both.
    """

    def test_create_refuses_rather_than_shadowing_a_yaml_source(
        self, client, mock_components
    ) -> None:
        """With config down the create is refused, and nothing is written."""
        storage = mock_components["storage"]
        storage.sources.get.return_value = None
        app_state.config = None

        response = client.post(
            "/api/sync/sources", json={"id": "my_books", "plugin": "goodreads_rss"}
        )

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE
        storage.sources.upsert.assert_not_called()

    def test_delete_refuses_rather_than_sweeping_off_half_a_source_list(
        self, client, mock_components
    ) -> None:
        """Config down, a YAML source on the plugin reads as no source at all."""
        storage = mock_components["storage"]
        storage.sources.get.return_value = {
            "source_id": "my_books",
            "plugin": "goodreads_rss",
            "enabled": 1,
            "config": {},
            "migrated_at": "2026-01-01T00:00:00",
        }
        app_state.config = None

        response = client.delete("/api/sync/sources/my_books")

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE
        storage.sources.delete.assert_not_called()
        storage.credentials.delete_for_source.assert_not_called()


# The plugin-resolving routes that also carry a body, so the order between the
# lookup and body validation is observable on them.
_PLUGIN_ROUTES_WITH_A_BODY = [
    "/api/sync/sources/no_such_source/config",
    "/api/sync/sources/no_such_source/secret/api_key",
    "/api/sync/sources/no_such_source/enabled",
    "/api/sync/sources/no_such_source/schedule",
]


class TestPluginLookupVersusRequestValidation:
    """The URL is answered before the body, on the routes carrying both.

    ``require_plugin`` is a dependency now rather than the handler's first
    statement, so FastAPI resolves it with the rest of the dependency tree —
    ahead of validating the endpoint's own params — and its 404 outranks the
    422 an invalid body used to get. That is the accepted answer
    rather than a regression, and it is the same argument as the 503 that now
    outranks a 422: the URL names a resource this server cannot serve, so
    reporting the body error would be faulting the caller for a request that
    could never have succeeded whatever it said. Fix the id, then the body.
    """

    @pytest.mark.parametrize("url", _PLUGIN_ROUTES_WITH_A_BODY)
    def test_an_unresolvable_source_outranks_an_invalid_body(
        self, client, mock_components, url
    ) -> None:
        """Bad body plus unknown id answers 404 for the id, not 422 for the body."""
        mock_components["storage"].sources.get.return_value = None

        response = client.put(url, json={})

        assert response.status_code == 404
        assert response.json()["detail"] == "Source not found."


# Bounded so a handler nothing releases fails the test instead of hanging the
# suite; nothing waits this long on the passing path.
_STALL_TIMEOUT_SECONDS = 5.0

# How long a caller that must not get through is given to prove it. Only spent
# on the passing path, and only by the tests asserting that something blocks.
_BLOCKED_GRACE_SECONDS = 0.5


class TestSlowRequestsDoNotStallTheServerRegression:
    """A request in flight must not hold the loop against every other one.

    Bug reported: while a recommendation generation was running, the Data
    page's two-second sync poll froze, so sync progress appeared stuck.
    Root cause: ``get_recommendations`` was ``async def`` with no ``await`` in
    its body, so FastAPI ran the whole scoring pass directly on the event loop.
    Fix: the handler is plain ``def``, which Starlette runs in a threadpool
    worker, leaving the loop free to serve everything else.
    """

    def test_status_answers_while_a_recommendation_is_in_flight(
        self, mock_components
    ) -> None:
        """A concurrent ``/api/status`` returns before the slow request does."""
        engine_reached = threading.Event()
        release_engine = threading.Event()
        engine_returned = threading.Event()

        def blocking_scoring_pass(*_args, **_kwargs):
            engine_reached.set()
            release_engine.wait(timeout=_STALL_TIMEOUT_SECONDS)
            engine_returned.set()
            return []

        mock_components["engine"].generate_recommendations.side_effect = (
            blocking_scoring_pass
        )
        mock_components["storage"].get_user_preference_config.return_value = None
        # The context-manager form shares ONE portal, so both requests land on
        # one event loop. A bare TestClient builds a portal per request, each
        # with a loop of its own, where this stall cannot be observed at all.
        # Clearing config_path keeps that lifespan off the config watcher.
        app_state.config_path = None

        with (
            authenticated_client(mock_components["app"]) as client,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            slow = pool.submit(client.get, "/api/recommendations?type=book")
            assert engine_reached.wait(timeout=_STALL_TIMEOUT_SECONDS)

            status = client.get("/api/status")

            # The discriminator: back on the event loop, this request could
            # only be served once the engine had returned.
            assert not engine_returned.is_set()
            release_engine.set()
            assert slow.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200

        assert status.status_code == 200


def _awatch_reporting_one_change_on(
    trigger: threading.Event,
) -> Callable[[Path], AsyncIterator[set[tuple[str, str]]]]:
    """A ``watchfiles.awatch`` stand-in reporting one change when *trigger* is set.

    Waits off the loop so the fake is never itself the thing stalling it, which
    is what the test using this is trying to observe.
    """

    async def awatch(path: Path) -> AsyncIterator[set[tuple[str, str]]]:
        await asyncio.to_thread(trigger.wait, _STALL_TIMEOUT_SECONDS)
        yield {("modified", str(path))}
        await asyncio.Event().wait()

    return awatch


class TestTheConfigWatcherDoesNotStallTheServerRegression:
    """The fourth config writer is a task on the serving loop, and must not be.

    Defect: ``ConfigWatcher._watch`` called ``reload_config()`` straight, so the
    event loop itself waited on the config lock — held by ``PUT /api/settings``
    across a whole registry sweep, one SQLite connection per entry — and then on
    the reload's own file read, migrations and Fernet decrypts. A config file
    touched during a settings save therefore stopped the server answering
    anything at all: the head-of-line stall this change exists to remove,
    reintroduced at the one caller that is not a handler.
    Fix: ``_watch`` hands the reload to a worker thread.
    """

    def test_status_answers_while_the_watchers_reload_waits_for_the_lock(
        self, mock_components
    ) -> None:
        """A concurrent ``/api/status`` returns before the blocked reload does."""
        lock_held = threading.Event()
        release_lock = threading.Event()
        file_changed = threading.Event()
        reload_entered = threading.Event()
        reload_finished = threading.Event()

        def hold_the_config_lock() -> None:
            with locked_running_config():
                lock_held.set()
                release_lock.wait(timeout=_STALL_TIMEOUT_SECONDS)

        def announce_then_reload() -> bool:
            reload_entered.set()
            reloaded = reload_config()
            reload_finished.set()
            return reloaded

        with (
            patch(
                "watchfiles.awatch",
                side_effect=_awatch_reporting_one_change_on(file_changed),
            ),
            patch("src.web.state.reload_config", announce_then_reload),
            # The context-manager form is what starts the lifespan, and so the
            # watcher, on the same loop the requests are served from.
            authenticated_client(mock_components["app"]) as client,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            holder = pool.submit(hold_the_config_lock)
            assert lock_held.wait(timeout=_STALL_TIMEOUT_SECONDS)
            file_changed.set()
            assert reload_entered.wait(timeout=_STALL_TIMEOUT_SECONDS)

            status = client.get("/api/status")

            # The discriminator: on the loop, the watcher's reload had to reach
            # the lock and finish before any request could be served at all.
            assert not reload_finished.is_set()
            release_lock.set()
            holder.result(timeout=_STALL_TIMEOUT_SECONDS)
            assert reload_finished.wait(timeout=_STALL_TIMEOUT_SECONDS)

        assert status.status_code == 200


@pytest.fixture()
def settings_app(tmp_path: Path):
    """A booted app over a real temp-DB store, plus that store.

    The settings suite's own fixture in miniature; a module-level copy because
    a fixture defined inside a class is not visible from another one.
    """
    reset_sync_manager()
    storage = StorageManager(sqlite_path=tmp_path / "settings.db")
    config = {
        "storage": {"database_path": str(tmp_path / "settings.db")},
        "recommendations": {"default_count": 5, "max_count": 20},
    }
    with booted_web_app(storage, config) as app:
        yield authenticated_client(app), storage
    reset_sync_manager()


class TestConfigReloadRacingASettingsSaveRegression:
    """Every writer of the running config has to be kept apart from the others.

    Defect: the sweep left ``PUT /api/settings`` and ``DELETE
    /api/settings/{key}`` ``async`` because the single event loop is the only
    thing serialising a write to the running config — and then converted
    ``POST /api/config/reload``, which writes the running config too and
    wholesale, to plain ``def``. In a threadpool worker it can rebind
    ``app_state.config`` while a save is mid-request, and the save then
    publishes its live-apply into the dict nobody reads any more: the database
    keeps the new value, the running config keeps the old one, and nothing
    reports an error. That is the exact disagreement the two exceptions exist
    to prevent, arriving through the third writer.
    """

    def test_a_save_survives_a_reload_that_lands_mid_request(
        self, settings_app
    ) -> None:
        """The value the save persisted is the value the server is running."""
        client, storage = settings_app
        save_holds_its_config = threading.Event()
        reload_finished = threading.Event()

        def hand_over_the_config_then_pause() -> dict[str, Any] | None:
            # Only the save reaches the guards — ``/config/reload`` declares no
            # dependencies — so this pauses one request and not the other.
            config = app_state.config
            save_holds_its_config.set()
            reload_finished.wait(timeout=_STALL_TIMEOUT_SECONDS)
            return config

        with (
            patch("src.web.guards.get_config", hand_over_the_config_then_pause),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            save = pool.submit(
                client.put,
                "/api/settings",
                json={"updates": {_SETTINGS_INT_KEY: 11}},
            )
            assert save_holds_its_config.wait(timeout=_STALL_TIMEOUT_SECONDS)

            assert client.post("/api/config/reload").status_code == 200

            reload_finished.set()
            assert save.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200

        assert storage.settings.get(_SETTINGS_INT_KEY) == 11
        assert (
            get_leaf(app_state.config, tuple(_SETTINGS_INT_KEY.split("."))) == 11
        ), "the database and the running config disagree about a saved setting"

    def test_a_reload_cannot_land_while_a_save_holds_the_lock(
        self, settings_app
    ) -> None:
        """The lock itself, rather than what surviving a reload looks like.

        The two tests above pass with ``with _config_lock:`` deleted from both
        of its sites: the reload simply completes first, and the save then
        resolves the new binding inside ``writable_config`` and publishes into
        that, so the database and the running config still agree. What they pin
        is the re-resolution, which is worth pinning and is not this.

        This one parks the save inside its locked block and then asks the
        reload to run. ``load_config`` is the reload's first statement inside
        the lock, so calling it at all is the reload having got past a lock
        that should have stopped it — and with the lock removed the whole
        request completes here rather than after the save is released.
        """
        client, storage = settings_app
        save_inside_the_lock = threading.Event()
        release_save = threading.Event()

        def park_inside_the_locked_block(
            config: dict[str, Any], settings_storage: StorageManager
        ) -> dict[str, Any]:
            save_inside_the_lock.set()
            assert release_save.wait(timeout=_STALL_TIMEOUT_SECONDS)
            return build_settings_view(config, settings_storage)

        with (
            patch("src.web.api.build_settings_view", park_inside_the_locked_block),
            patch("src.web.state.load_config", wraps=load_config) as reload_read_yaml,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            save = pool.submit(
                client.put,
                "/api/settings",
                json={"updates": {_SETTINGS_INT_KEY: 11}},
            )
            assert save_inside_the_lock.wait(timeout=_STALL_TIMEOUT_SECONDS)

            reload = pool.submit(client.post, "/api/config/reload")
            with pytest.raises(TimeoutError):
                reload.result(timeout=_BLOCKED_GRACE_SECONDS)
            assert reload_read_yaml.call_count == 0

            release_save.set()
            assert save.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200
            assert reload.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200
            assert reload_read_yaml.call_count == 1

        assert storage.settings.get(_SETTINGS_INT_KEY) == 11


class TestOverlappingPreferenceWritesRegression:
    """Two preference writes at once must both survive.

    Bug: the handler read, merged and wrote as three calls, so the later write
    stored a blob built before the earlier landed, losing all of
    ``users.settings``.
    Fix: storage merges under ``_save_lock``.
    """

    def test_the_first_write_survives_a_second_that_overlaps_it(
        self, settings_app
    ) -> None:
        """Forced interleaving: the first request is parked holding its read."""
        client, storage = settings_app
        parked = threading.Event()
        release = threading.Event()
        real_read = StorageManager.get_user_preference_config

        def park_the_first_read(
            self: StorageManager, user_id: int
        ) -> UserPreferenceConfig:
            preference_config = real_read(self, user_id)
            if not parked.is_set():
                parked.set()
                assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)
            return preference_config

        with (
            patch.object(
                StorageManager, "get_user_preference_config", park_the_first_read
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            theme = pool.submit(
                client.put, "/api/users/1/preferences", json={"theme": "midnight"}
            )
            assert parked.wait(timeout=_STALL_TIMEOUT_SECONDS)

            weights = pool.submit(
                client.put,
                "/api/users/1/preferences",
                json={"scorer_weights": {"genre_match": 3.0}},
            )
            # Merged in the handler, the second request reads the pre-theme
            # blob and finishes here rather than waiting for the save lock.
            with pytest.raises(TimeoutError):
                weights.result(timeout=_BLOCKED_GRACE_SECONDS)

            release.set()
            assert theme.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200
            assert weights.result(timeout=_STALL_TIMEOUT_SECONDS).status_code == 200

        stored = storage.get_user_preference_config(1)
        assert stored.theme == "midnight"
        assert stored.scorer_weights == {"genre_match": 3.0}


# Each lazily-built process singleton behind the sync and enrichment endpoints:
# the accessor, its reset hook, the name its module builds, and the lock the
# build is supposed to happen under.
_LAZY_SINGLETONS = [
    pytest.param(
        get_sync_manager,
        reset_sync_manager,
        "src.web.sync_manager.SyncManager",
        SyncManager,
        _sync_manager_lock,
        id="sync_manager",
    ),
    pytest.param(
        get_enrichment_manager,
        reset_enrichment_manager,
        "src.web.enrichment_manager.WebEnrichmentManager",
        WebEnrichmentManager,
        _enrichment_manager_lock,
        id="enrichment_manager",
    ),
]


class TestLazySingletonsAreBuiltOnceRegression:
    """A cold process must not hand two callers two managers.

    Bug reported: sync progress freezes — a job started through ``POST
    /api/update`` never appears in ``GET /api/sync/status``.
    Root cause: ``get_sync_manager`` is a check-then-set with no lock, and the
    threadpool conversion made both of its callers plain ``def``. Two requests
    arriving together on a cold process both see ``None`` and each keep a
    manager of their own, so the job lives in one and the status endpoint reads
    the other. ``get_enrichment_manager`` is the same three lines.
    Fix: the lazy build happens under a module-level lock.
    """

    @pytest.mark.parametrize(
        ("acquire", "reset", "target", "manager_class", "module_lock"), _LAZY_SINGLETONS
    )
    def test_two_cold_callers_share_one_manager(
        self, acquire, reset, target, manager_class, module_lock
    ) -> None:
        """Forced interleaving, not a race: the build cannot finish unreleased.

        The first caller is parked inside the constructor until the test lets
        it out, so the second reaches the accessor while the first is still
        building — the window the missing lock left open. Unlocked, the second
        passes the ``is None`` check and builds a second manager, and ``built``
        ends up holding two objects that are not each other.

        The constructor asserts the lock is held rather than the test asserting
        the second caller has not finished: "not done yet" is also what a
        thread that has merely been descheduled looks like, so it would hold on
        the unlocked code too.
        """
        building = threading.Event()
        release = threading.Event()
        built: list[Any] = []

        class _StallingManager(manager_class):
            def __init__(self) -> None:
                assert module_lock.locked(), "the lazy build is not serialised"
                building.set()
                assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)
                super().__init__()
                built.append(self)

        reset()
        try:
            with (
                patch(target, _StallingManager),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(acquire)
                assert building.wait(timeout=_STALL_TIMEOUT_SECONDS)

                second = pool.submit(acquire)
                # Unlocked, the second caller passes the ``is None`` check and
                # builds a manager of its own while the first is still parked,
                # so it finishes here rather than waiting for the release.
                with pytest.raises(TimeoutError):
                    second.result(timeout=_BLOCKED_GRACE_SECONDS)

                release.set()
                manager = first.result(timeout=_STALL_TIMEOUT_SECONDS)
                assert second.result(timeout=_STALL_TIMEOUT_SECONDS) is manager
            assert built == [manager]
        finally:
            reset()


def _api_log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Only the api.py records: booting the app logs from four other modules."""
    return [
        record.getMessage() for record in caplog.records if record.name == "src.web.api"
    ]


class TestExoticBreaksCannotForgeAnApiLogLine:
    """``\\u2028`` is the case a reviewer misses and ``str.splitlines`` does not,
    so each sink is driven with every break the shared constant names.
    """

    @pytest.mark.parametrize("breaker", LINE_BREAKS)
    def test_a_recommendation_failure_stays_on_one_line(
        self, breaker: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.side_effect = ValueError(
            f"no candidate for Real Title{breaker}ERROR forged"
        )
        storage = MagicMock(spec=StorageManager)
        storage.get_user_preference_config.return_value = None

        with (
            booted_web_app(storage, {}) as app,
            caplog.at_level(logging.ERROR, logger="src.web.api"),
        ):
            app_state.engine = engine
            authenticated_client(app).get(
                "/api/recommendations?type=video_game&count=5"
            )

        messages = _api_log_messages(caplog)
        assert len(messages) == 1
        assert len(messages[0].splitlines()) == 1
        assert breaker not in messages[0]
        assert "ValueError" in messages[0]


class TestACatchAllHandlerStillNamesItsExceptionClassRegression:
    """Reported: two spellings of exception logging, and one drops the class.

    Bug: ``sanitize_for_log(str(exc))`` in a catch-all ``except Exception``
    logs a trailing colon and nothing else for a bare ``TimeoutError()``.
    Fix: both go through ``exception_for_log``.
    """

    def test_the_recommendations_sink_names_it(
        self,
        client: TestClient,
        mock_components: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_components["engine"].generate_recommendations.side_effect = TimeoutError()

        with caplog.at_level(logging.ERROR, logger="src.web.api"):
            response = client.get("/api/recommendations?type=book&count=1")

        assert response.status_code == 500
        # The whole rendering, so the sink is named too: a class name found
        # anywhere in the joined records could have come from another one.
        assert "Error generating recommendations: TimeoutError: " in _api_log_messages(
            caplog
        )

    def test_the_completion_sink_names_it(
        self,
        client: TestClient,
        mock_components: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        mock_components["storage"].complete_content_item.side_effect = TimeoutError()

        with caplog.at_level(logging.ERROR, logger="src.web.api"):
            response = client.post(
                "/api/complete", json={"content_type": "book", "title": "Dune"}
            )

        assert response.status_code == 500
        assert (
            "Error marking content as completed: TimeoutError: "
            in _api_log_messages(caplog)
        )


class TestANonUtf8ThemeNameStillWritesItsWarningRegression:
    """Reported by QA: the warning for a bad theme directory disappeared.

    Bug: ``os.listdir`` surrogate-escapes a name that is not valid UTF-8, the
    encoder raised on it, and the only content of the warning deleted the
    warning.
    Fix: ``sanitize_for_log`` escapes surrogates.
    """

    def test_the_warning_reaches_the_log_file(self, tmp_path: Path) -> None:
        theme_dir = tmp_path / os.fsdecode(b"solar\xff")
        theme_dir.mkdir()
        (theme_dir / "theme.json").write_text("{not json", encoding="utf-8")
        log_file = tmp_path / "app.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
        api_logger = logging.getLogger("src.web.api")
        api_logger.addHandler(handler)
        try:
            assert src.web.api.discover_themes(tmp_path) == []
        finally:
            api_logger.removeHandler(handler)
            handler.close()

        written = log_file.read_text(encoding="utf-8")
        assert "Skipping invalid theme directory: solar\\udcff" in written
        assert len(written.splitlines()) == 1


class TestNoLogEscapeReachesAResponseBodyRegression:
    """Reported: a title came back holding a literal ``\\n`` where one was.

    Bug: both title-echoing endpoints were routed through
    ``sanitize_for_log``, which shapes a value for a log file, not a client.
    Fix: the body carries the stored title.
    """

    def test_ignore_echoes_the_stored_title(self, client, mock_components) -> None:
        mock_components["storage"].get_content_item = Mock(
            return_value=ContentItem(
                id="ext_1",
                db_id=42,
                title="Dune\nWARNING forged",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        mock_components["storage"].set_item_ignored = Mock(return_value=True)

        response = client.patch(
            "/api/items/42/ignore?user_id=1", json={"ignored": True}
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "db_id": 42,
            "title": "Dune\nWARNING forged",
            "ignored": True,
            "message": "Item 'Dune\nWARNING forged' ignored",
        }

    def test_complete_echoes_the_requested_title(self, client, mock_components) -> None:
        mock_components["storage"].complete_content_item.return_value = 7

        response = client.post(
            "/api/complete",
            json={"content_type": "book", "title": "Dune\nWARNING forged"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "message": "Marked 'Dune\nWARNING forged' as completed",
            "id": 7,
        }

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udcff", "\udfff"])
    def test_the_echoed_title_can_never_hold_a_lone_surrogate(
        self, surrogate: str
    ) -> None:
        """The door refuses one, so nothing downstream has to read past it.

        ``json.loads`` accepts an unpaired ``\\ud800`` escape, and the model
        bound is the only thing keeping one out of a stored title.
        """
        with pytest.raises(ValidationError):
            src.web.api.CompletionRequest(content_type="book", title=f"Dune{surrogate}")

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udcff", "\udfff"])
    def test_the_refusal_itself_renders(self, surrogate: str, mock_components) -> None:
        """Regression: the refusal above answered 500.

        Bug: the 422 quotes the rejected input back, and ``json.loads``
        accepts an unpaired ``\\ud800`` escape, so the refusal could not
        render. Sent as raw ASCII bytes: ``json=`` declines to encode one.
        """
        tolerant = authenticated_client(
            mock_components["app"], raise_server_exceptions=False
        )
        body = json.dumps(
            {"content_type": "book", "title": f"Dune{surrogate}"}, ensure_ascii=True
        ).encode("ascii")

        response = tolerant.post(
            "/api/complete", content=body, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["input"] == f"Dune{surrogate}"
        mock_components["storage"].complete_content_item.assert_not_called()

    def test_an_ordinary_refusal_still_quotes_its_input_verbatim(self, client) -> None:
        """The escape above is the response class's, not a reshaped 422."""
        over_long = "D" * 501

        response = client.post(
            "/api/complete", json={"content_type": "book", "title": over_long}
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"][0]["input"] == over_long

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udcff", "\udfff"])
    def test_a_stored_title_holding_a_lone_surrogate_still_answers(
        self, surrogate: str, mock_components
    ) -> None:
        """Regression: no request model stands between storage and this body.

        Bug: ``JSONResponse`` encodes strictly, so the ignore succeeded and
        the caller got a 500.
        Fix: the app's one response class writes the escape instead.
        """
        mock_components["storage"].get_content_item = Mock(
            return_value=ContentItem(
                id="ext_1",
                db_id=42,
                title=f"Dune{surrogate}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        mock_components["storage"].set_item_ignored = Mock(return_value=True)
        tolerant = authenticated_client(
            mock_components["app"], raise_server_exceptions=False
        )

        response = tolerant.patch(
            "/api/items/42/ignore?user_id=1", json={"ignored": True}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == f"Dune{surrogate}"
        # The body interpolates the title twice; the first fix missed one.
        assert body["message"] == f"Item 'Dune{surrogate}' ignored"

    @pytest.mark.parametrize("astral", ["\U0001f600", "\U0010ffff", "￿", "Café"])
    def test_the_ignore_body_keeps_a_title_that_encodes(
        self, astral: str, mock_components
    ) -> None:
        """The escape is for what UTF-8 refuses, never for what it accepts.

        Rendering runs on every title now, so a legible one must survive it
        byte for byte.
        """
        mock_components["storage"].get_content_item = Mock(
            return_value=ContentItem(
                id="ext_1",
                db_id=42,
                title=f"Dune{astral}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        mock_components["storage"].set_item_ignored = Mock(return_value=True)

        response = authenticated_client(mock_components["app"]).patch(
            "/api/items/42/ignore?user_id=1", json={"ignored": False}
        )

        assert response.status_code == 200, response.text
        assert response.json()["title"] == f"Dune{astral}"
        assert response.json()["message"] == f"Item 'Dune{astral}' unignored"

    @pytest.mark.parametrize("astral", ["\U0001f600", "\U0010ffff", "￿"])
    def test_a_body_keeps_a_character_above_the_bmp(
        self, astral: str, client, mock_components
    ) -> None:
        """The surrogate range is a code unit, never a real emoji's codepoint."""
        mock_components["storage"].complete_content_item.return_value = 7

        response = client.post(
            "/api/complete",
            json={"content_type": "book", "title": f"Dune{astral}"},
        )

        assert response.json()["message"] == f"Marked 'Dune{astral}' as completed"


_LONE_SURROGATES = ["\ud800", "\udcff", "\udfff"]


def _item_holding(surrogate: str) -> ContentItem:
    """One item carrying *surrogate* in a column and in the metadata blob.

    The blob is how a real row reaches this code with one: sqlite3 encodes a
    TEXT bind strictly, while ``json.dumps`` escapes to ASCII first.
    """
    return ContentItem(
        id="ext_1",
        db_id=42,
        title=f"Dune{surrogate}",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata={"description": f"Arrakis{surrogate}"},
    )


def _client_reading(mock_components, item: ContentItem) -> TestClient:
    """A 500-reporting client whose store answers every item read with *item*."""
    storage = mock_components["storage"]
    storage.get_content_items = Mock(return_value=[item])
    storage.get_content_item = Mock(return_value=item)
    storage.update_item_from_ui = Mock(return_value=True)
    return authenticated_client(mock_components["app"], raise_server_exceptions=False)


@pytest.mark.parametrize("surrogate", _LONE_SURROGATES)
class TestAStoredLoneSurrogate500edEveryEndpointEchoingItRegression:
    """Bug: a JSON blob column stores an unpaired ``\\ud800`` escape as ASCII,
    and every body carrying it back was encoded strictly and answered 500.
    Fix: one response class for the app, encoding with ``backslashreplace``.
    """

    def test_listing_items_answers(self, surrogate, mock_components) -> None:
        client = _client_reading(mock_components, _item_holding(surrogate))

        response = client.get("/api/items")

        assert response.status_code == 200, response.text
        listed = response.json()[0]
        assert (listed["title"], listed["description"]) == (
            f"Dune{surrogate}",
            f"Arrakis{surrogate}",
        )

    def test_fetching_one_item_answers(self, surrogate, mock_components) -> None:
        client = _client_reading(mock_components, _item_holding(surrogate))

        response = client.get("/api/items/42")

        assert response.status_code == 200, response.text
        fetched = response.json()
        assert (fetched["title"], fetched["description"]) == (
            f"Dune{surrogate}",
            f"Arrakis{surrogate}",
        )

    def test_editing_an_item_answers(self, surrogate, mock_components) -> None:
        client = _client_reading(mock_components, _item_holding(surrogate))

        response = client.patch("/api/items/42", json={"status": "unread"})

        assert response.status_code == 200, response.text
        edited = response.json()
        assert (edited["title"], edited["description"]) == (
            f"Dune{surrogate}",
            f"Arrakis{surrogate}",
        )

    def test_exporting_the_library_as_json_answers(
        self, surrogate, mock_components
    ) -> None:
        """The export serialises its own body, so it needs the raw encode."""
        client = _client_reading(mock_components, _item_holding(surrogate))

        response = client.get("/api/items/export?type=book&format=json")

        assert response.status_code == 200, response.text
        assert json.loads(response.text)[0]["title"] == f"Dune{surrogate}"

    def test_exporting_the_library_as_csv_answers(
        self, surrogate, mock_components
    ) -> None:
        """A CSV cell carries no escape of its own, so the code unit arrives
        as the six characters ``backslashreplace`` wrote.
        """
        client = _client_reading(mock_components, _item_holding(surrogate))

        response = client.get("/api/items/export?type=book&format=csv")

        assert response.status_code == 200, response.text
        assert f"Dune\\u{ord(surrogate):04x}" in response.text


class TestAStoredCustomRulePermanently500edThePreferencesPageRegression:
    """Bug: a rule stored as ASCII and read back with ``ensure_ascii=False``
    500ed the page — every later read, for good, with no door left to correct
    the row by.
    Fix: the app's one response class encodes the escape instead.
    """

    RULE = "avoid \ud800"

    def test_the_put_door_refuses_one_rather_than_storing_it(
        self, settings_app
    ) -> None:
        """The rule's length bound makes pydantic read a string it cannot
        represent, so the row below is the case left — the one with no door to
        correct it by. Sent raw: ``json=`` declines to encode one.
        """
        client, storage = settings_app
        tolerant = authenticated_client(client.app, raise_server_exceptions=False)

        written = tolerant.put(
            "/api/users/1/preferences",
            content='{"custom_rules": ["avoid \\ud800"]}',
            headers={"Content-Type": "application/json"},
        )

        assert written.status_code == 422, written.text
        assert storage.get_user_preference_config(1).custom_rules == []

    def test_a_rule_already_in_the_database_reads_back_twice(
        self, settings_app
    ) -> None:
        """The permanent case: the row is written, and no read may refuse it."""
        client, storage = settings_app
        with storage.sqlite_db.connection() as conn:
            update_user_settings(
                conn, 1, {"preference_config": {"custom_rules": [self.RULE]}}
            )
        tolerant = authenticated_client(client.app, raise_server_exceptions=False)

        first = tolerant.get("/api/users/1/preferences")
        second = tolerant.get("/api/users/1/preferences")

        assert (first.status_code, second.status_code) == (200, 200)
        assert first.json()["custom_rules"] == [self.RULE]


class TestAnHTTPExceptionDetailBypassesTheAppResponseClassRegression:
    """Bug: FastAPI renders an ``HTTPException`` through its own handler, on a
    stock ``JSONResponse`` the app's class never sees, so a detail holding a
    lone surrogate answers 500 rather than the refusal.
    Fix: that handler names the app's encode too.
    """

    def test_an_unknown_settings_key_holding_one_is_refused_not_500ed(
        self, settings_app
    ) -> None:
        """``updates`` is a bare ``dict[str, Any]``, so no bound refuses the key
        at the door and it reaches the 422 detail verbatim. Sent raw because
        ``json=`` declines to encode one.
        """
        client, _storage = settings_app
        tolerant = authenticated_client(client.app, raise_server_exceptions=False)

        refused = tolerant.put(
            "/api/settings",
            content='{"updates": {"nope\\ud800": 1}}',
            headers={"Content-Type": "application/json"},
        )

        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["key"] == "nope\ud800"

    @pytest.mark.parametrize("status", [100, 204, 304])
    def test_a_status_that_may_not_carry_a_body_still_gets_none(
        self, status: int
    ) -> None:
        """Standing in for FastAPI's handler means keeping the rest of its
        contract: every status it withholds a body from, and the headers that
        go with it — a challenge dropped here is a 401 nobody can answer.
        """
        rendered = asyncio.run(
            _raised_refusal_json_can_carry(
                Mock(spec=Request),
                StarletteHTTPException(
                    status_code=status,
                    detail="unsendable",
                    headers={"WWW-Authenticate": "Bearer"},
                ),
            )
        )

        assert (rendered.status_code, rendered.body) == (status, b"")
        assert rendered.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("surrogate", _LONE_SURROGATES)
    @pytest.mark.parametrize("status", [400, 404, 409, 422, 500])
    def test_every_status_carries_the_detail_and_its_headers(
        self, status: int, surrogate: str
    ) -> None:
        """The defect was the renderer, not the 422 — one endpoint echoes a
        caller's key there today, and any status raising a detail built from
        request text hit the same strict encode.
        """
        rendered = asyncio.run(
            _raised_refusal_json_can_carry(
                Mock(spec=Request),
                StarletteHTTPException(
                    status_code=status,
                    detail=f"nope{surrogate}",
                    headers={"WWW-Authenticate": "Bearer"},
                ),
            )
        )

        assert rendered.status_code == status
        assert json.loads(rendered.body) == {"detail": f"nope{surrogate}"}
        assert rendered.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("surrogate", _LONE_SURROGATES)
    def test_the_handler_it_replaced_still_refuses_that_detail(
        self, surrogate: str
    ) -> None:
        """The counterfactual the app tests cannot show: FastAPI's own handler
        is what every one of them reached before, and it still raises on the
        same input — so nothing above passes without the registration.
        """
        with pytest.raises(UnicodeEncodeError):
            asyncio.run(
                http_exception_handler(
                    Mock(spec=Request),
                    StarletteHTTPException(status_code=422, detail=f"nope{surrogate}"),
                )
            )


def test_edit_item_corrects_the_release_year_and_creator(client, mock_components):
    corrected = ContentItem(
        id="game_1",
        db_id=7,
        title="Doom",
        author="id Software",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        metadata={"release_year": 1993},
    )
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)
    mock_components["storage"].get_content_item = Mock(return_value=corrected)

    # The year arrives as the text the dialog's free-text box holds, and is
    # stored as the number the CLI would have sent.
    response = client.patch(
        "/api/items/7?user_id=1",
        json={"status": "unread", "release_year": "1993", "creator": "id Software"},
    )

    assert response.status_code == 200
    assert response.json()["release_year"] == 1993
    assert response.json()["author"] == "id Software"
    call_kwargs = mock_components["storage"].update_item_from_ui.call_args[1]
    assert call_kwargs["release_year"] == 1993
    assert call_kwargs["creator"] == "id Software"


def test_edit_item_rejects_a_correction_outside_the_shared_bounds(
    client, mock_components
):
    """Each refusal is a sentence naming the bound, not a nested 422 list.

    The edit dialog renders ``detail`` and nothing else, so a schema constraint
    left it with "422 Unprocessable Entity" to show for a mistyped year.
    """
    mock_components["storage"].update_item_from_ui = Mock(return_value=True)

    for correction, says in (
        ({"release_year": MIN_RELEASE_YEAR - 1}, str(MIN_RELEASE_YEAR)),
        ({"release_year": MAX_RELEASE_YEAR + 1}, str(MAX_RELEASE_YEAR)),
        # The dialog's year box is free text, so what it holds arrives verbatim.
        ({"release_year": "2016 (remaster)"}, str(MAX_RELEASE_YEAR)),
        # A year copied off a page with a footnote marker: every character is a
        # digit to ``str.isdigit``, but ``int`` takes only the decimal ones.
        ({"release_year": "2016¹"}, str(MAX_RELEASE_YEAR)),
        ({"creator": "x" * (MAX_CREATOR_LENGTH + 1)}, str(MAX_CREATOR_LENGTH)),
        ({"creator": "   "}, "empty"),
        ({"review": "x" * (MAX_REVIEW_LENGTH + 1)}, str(MAX_REVIEW_LENGTH)),
    ):
        response = client.patch(
            "/api/items/42?user_id=1", json={"status": "unread", **correction}
        )
        assert response.status_code == 400, correction
        assert says in response.json()["detail"], correction

    mock_components["storage"].update_item_from_ui.assert_not_called()


def test_edit_item_reports_a_type_that_states_no_release_year(client, mock_components):
    mock_components["storage"].update_item_from_ui = Mock(
        side_effect=UncorrectableFieldError("A book has no release year to correct.")
    )

    response = client.patch("/api/items/7?user_id=1", json={"release_year": 1965})

    assert response.status_code == 400
    assert "release year" in response.json()["detail"]
