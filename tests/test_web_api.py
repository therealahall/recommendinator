"""Tests for web API endpoints."""

import ast
import asyncio
import csv
import gc
import inspect
import io
import json
import logging
import os
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime
from math import inf, nan
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import anyio.from_thread
import pytest
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.datastructures import DefaultPlaceholder
from fastapi.dependencies.models import Dependant
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import (
    RequestValidationError,
    WebSocketRequestValidationError,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import WebSocketRoute

import src.sources.service
import src.web.api
import src.web.chat_api
from src.auth.epic import EpicAuthError
from src.auth.gog import GogAuthError
from src.auth.trakt import DevicePollResult, DevicePollStatus, TraktAuthError
from src.config.service import load_config
from src.conversation.engine import ConversationEngine
from src.ingestion.paths import get_allowed_source_roots
from src.ingestion.sync import SyncErrorCallback, SyncResult
from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import ConversationChunk
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.content_length import LengthPreference
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from src.recommendations.scorers import SCORER_NAME_MAP
from src.settings.metadata import default_of
from src.settings.service import build_settings_view
from src.sources.service import SOURCE_MISCONFIGURED_DETAIL
from src.storage.manager import UNSET, StorageManager
from src.storage.schema import update_user_settings
from src.storage.settings_migration import migrate_config_settings
from src.utils.dotted_path import get_leaf
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.utils.text import LINE_BREAKS
from src.web.api import APP_VERSION, _item_to_response
from src.web.app import (
    _raised_refusal_json_can_carry,
    _validation_refusal_json_can_carry,
)
from src.web.auth import SESSION_COOKIE, UNAUTHORIZED_DETAIL, require_session
from src.web.enrichment_manager import (
    WebEnrichmentManager,
    _enrichment_manager_lock,
    get_enrichment_manager,
    reset_enrichment_manager,
)
from src.web.guards import (
    RequiredStorage,
    require_config,
    require_conversation_engine,
    require_engine,
    require_memory_manager,
    require_storage,
    writable_config,
)
from src.web.responses import SurrogateSafeJSONResponse, SurrogateSafeResponse
from src.web.state import (
    ConfigWatcher,
    _config_lock,
    app_state,
    get_config,
    get_conversation_engine,
    get_engine,
    get_memory_manager,
    get_storage,
    locked_running_config,
    reload_config,
)
from src.web.stream_limit import (
    MAX_CONCURRENT_STREAMS,
    TOO_MANY_STREAMS_DETAIL,
    _HeldSlot,
    _slots,
    bounded_sse,
)
from src.web.sync_manager import (
    SyncManager,
    _sync_manager_lock,
    get_sync_manager,
    reset_sync_manager,
)
from tests.ast_sweeps import renders_a_value_as_text
from tests.factories import (
    authenticated_client,
    back_mock_preference_store,
    booted_web_app,
    issue_session,
)


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
            "goodreads_csv": {
                "plugin": "goodreads_csv",
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

    mock_storage_manager = Mock(spec=StorageManager)
    mock_storage_manager.get_credentials_for_source.return_value = {}
    mock_storage_manager.list_source_configs.return_value = []
    mock_storage_manager.get_source_config.return_value = None

    mock_embedding_gen = Mock(spec=EmbeddingGenerator)
    llm_components = (
        Mock(spec=OllamaClient),
        mock_embedding_gen,
        Mock(spec=RecommendationGenerator),
    )

    mock_engine_instance = Mock(spec=RecommendationEngine)
    mock_engine_instance.storage = mock_storage_manager

    with (
        patch("src.web.app.migrate_source_labels") as mock_migrate_labels,
        patch("src.web.app.migrate_source_config_plugins") as mock_migrate_plugins,
        patch("src.web.app.migrate_source_attribution") as mock_migrate_attribution,
        booted_web_app(
            mock_storage_manager,
            mock_config,
            llm_components,
            engine=mock_engine_instance,
        ) as app,
    ):
        app_state.embedding_gen = mock_embedding_gen

        yield {
            "app": app,
            "storage": mock_storage_manager,
            "embedding_gen": mock_embedding_gen,
            "engine": mock_engine_instance,
            "migrate_source_labels": mock_migrate_labels,
            "migrate_source_config_plugins": mock_migrate_plugins,
            "migrate_source_attribution": mock_migrate_attribution,
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


def test_create_app_runs_every_source_migration(mock_components, mock_config):
    """create_app runs all three source migrations with the real storage.

    Proves they are wired into web startup, not merely unit-tested: a rename
    must relabel items and configs on boot, and items stored under a plugin
    name must find their source.
    """
    storage = mock_components["storage"]
    mock_components["migrate_source_labels"].assert_called_once_with(storage)
    mock_components["migrate_source_config_plugins"].assert_called_once_with(storage)
    mock_components["migrate_source_attribution"].assert_called_once_with(
        mock_config, storage
    )


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
        storage_manager.set_setting("recommendations.default_count", 9)
        with booted_web_app(storage_manager, mock_config):
            # Real hook overlaid the DB leaf onto the in-memory config.
            assert app_state.config["recommendations"]["default_count"] == 9
            # Boot seeded nothing: only the pre-existing leaf remains in the DB.
            assert storage_manager.list_settings() == {
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
        storage_manager.set_setting("web.debug", True)
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

    def test_a_configured_origin_reaches_only_the_ungated_surface(
        self, mock_config, tmp_path
    ):
        """Regression: this asserted a credentialed preflight, which cannot work.

        The session cookie is ``SameSite=Strict``, so a browser never attaches
        it cross-origin. ``allowed_origins`` buys the SPA shell and the static
        assets; everything behind the session gate answers 401 to that client.
        """
        reset_sync_manager()
        origin = "https://app.example.com"
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {"allowed_origins": [origin]}}
        with booted_web_app(storage_manager, config) as app:
            client = TestClient(app)
            preflight = client.options(
                "/api/auth/login",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            shell = client.get("/", headers={"Origin": origin})
            gated = client.get("/api/status", headers={"Origin": origin})
        reset_sync_manager()

        assert preflight.status_code == 200
        allowed = preflight.headers["access-control-allow-headers"].lower()
        assert "content-type" in allowed
        assert "access-control-allow-credentials" not in preflight.headers
        assert shell.status_code == 200
        assert shell.headers["access-control-allow-origin"] == origin
        assert gated.status_code == 401

    @pytest.mark.parametrize(
        "origins",
        [
            pytest.param(["https://app.example.com"], id="one-origin"),
            pytest.param(["*"], id="wildcard"),
        ],
    )
    def test_no_origin_list_carries_credentials(self, mock_config, tmp_path, origins):
        """Nothing a browser sends cross-origin can authenticate under Strict.

        Turning this on would promise a signed-in cross-origin client that the
        cookie makes impossible, and against ``["*"]`` browsers refuse it flat.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {"allowed_origins": origins}}
        with booted_web_app(storage_manager, config) as app:
            assert _cors_origins(app) == origins
            assert _cors_kwargs(app).get("allow_credentials", False) is False
        reset_sync_manager()

    def test_db_set_origins_reach_the_middleware(self, mock_config, tmp_path):
        """A DB-stored value applies on the next boot, as restart_required promises.

        The overlay runs before the CORS read, but nothing pinned that ordering
        — and this is the only registry leaf whose effect is a security control.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        storage_manager.set_setting("web.allowed_origins", ["https://stored.example"])
        with booted_web_app(storage_manager, mock_config) as app:
            assert _cors_origins(app) == ["https://stored.example"]
        reset_sync_manager()

    @pytest.mark.parametrize("bad_origins", [None, "https://app.example.com", [1, 2]])
    def test_unusable_allowed_origins_is_reported_not_swallowed(
        self, mock_config, tmp_path, bad_origins, caplog
    ):
        """A narrowed CORS policy must say why, like the bind path already does.

        ``resolve_bootstrap_web`` warns for every unusable ``web.*`` leaf, and
        the reasoning applies identically here: an operator who typed
        ``allowed_origins: https://app.example.com`` (a scalar, not a list) gets
        the default policy instead of theirs, and without a log there is nothing
        to debug the resulting browser CORS failures from.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {"allowed_origins": bad_origins}}
        with (
            caplog.at_level(logging.WARNING, logger="src.web.app"),
            booted_web_app(storage_manager, config),
        ):
            pass

        assert any("web.allowed_origins" in m for m in caplog.messages)
        reset_sync_manager()

    def test_well_formed_allowed_origins_logs_nothing(
        self, mock_config, tmp_path, caplog
    ):
        """The common case stays quiet, or the warning trains itself away."""
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {"allowed_origins": ["https://ok.example"]}}
        with (
            caplog.at_level(logging.WARNING, logger="src.web.app"),
            booted_web_app(storage_manager, config) as app,
        ):
            assert _cors_origins(app) == ["https://ok.example"]

        assert not any("web.allowed_origins" in m for m in caplog.messages)
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

    def test_logging_is_configured_after_the_overlay_onto_the_servers_console(
        self, mock_config, tmp_path
    ):
        """Overlay first, then the console the server logs onto.

        Spies on the real settings hook so it still runs (no stub). stdout is
        what ``docker logs`` shows, and a server has no data channel to keep
        the traceback off.
        """
        reset_sync_manager()
        order: list[str] = []
        console_arguments: dict[str, Any] = {}
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")

        def _record_settings(config, storage):
            order.append("settings")
            migrate_config_settings(config, storage)

        def _record_logging(config, **kwargs):
            order.append("logging")
            console_arguments.update(kwargs)

        with (
            patch(
                "src.web.app.migrate_config_settings",
                side_effect=_record_settings,
            ),
            # Overrides the root conftest's blanket no-op patch, which is what
            # keeps every other boot here off the production log file.
            patch("src.utils.logging.configure_logging", side_effect=_record_logging),
            booted_web_app(storage_manager, mock_config),
        ):
            pass

        assert order == ["settings", "logging"]
        assert console_arguments["console_stream"] is sys.stdout
        assert console_arguments["console_tracebacks"] is True
        # No floor of its own: a server's console is its log viewer, so it
        # takes what ``logging.level`` names.
        assert console_arguments["console_floor"] == logging.NOTSET
        # The real hook ran via the spy but wrote nothing to the DB.
        assert storage_manager.list_settings() == {}
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
                storage_manager.has_global_secret("enrichment.providers.tmdb.api_key")
                is True
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

    def test_a_leaked_watcher_does_not_reach_the_booted_app(
        self, mock_config, tmp_path, monkeypatch
    ) -> None:
        """The boot sees a real watcher, and the caller gets its mock back."""
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        leaked = MagicMock(spec=ConfigWatcher)
        monkeypatch.setattr(app_state, "config_watcher", leaked)

        with booted_web_app(storage_manager, mock_config):
            assert isinstance(app_state.config_watcher, ConfigWatcher)

        assert app_state.config_watcher is leaked

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
    # mock_config has exactly goodreads_csv enabled
    assert len(sources) == 1
    goodreads = next((s for s in sources if s["id"] == "goodreads_csv"), None)
    assert goodreads is not None
    assert goodreads["display_name"] == "Goodreads CSV"
    assert goodreads["plugin_display_name"] == "Goodreads (CSV Export)"


def test_sync_sources_lists_all_with_enabled_flag(client):
    """All configured sources are listed; ``enabled`` flag exposed per source.

    The UI renders disabled sources in a muted state instead of hiding them
    entirely, so the listing endpoint must surface them. ``resolve_inputs``
    is the gate that filters to enabled-only for sync execution.
    """
    app_state.config = {
        "inputs": {
            "goodreads_csv": {
                "plugin": "goodreads_csv",
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

    assert by_id["goodreads_csv"]["enabled"] is True
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
    mock_components["embedding_gen"].generate_content_embedding.return_value = [
        0.1
    ] * 768
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
    app_state.embedding_gen = None

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
    app_state.embedding_gen = None

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
    app_state.embedding_gen = None

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
    preference analysis keeps scoring on the stale rating. Same silent-discard
    class as the chat re-rating defect, on the completion endpoint.
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
    # No embedding generator: this test is about the SQLite write, and the
    # endpoint skips embedding generation when there is none.
    app_state.embedding_gen = None

    response = client.post(
        "/api/complete",
        json={"content_type": "book", "title": "Dune", "rating": 2},
    )

    assert response.status_code == 200, response.text
    stored = storage.get_content_item(db_id)
    assert stored is not None
    assert stored.rating == 2


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
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.fetch",
            return_value=iter([mock_item]),
        ),
        patch(
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
            return_value=[],
        ),
    ):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "goodreads_csv"})

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


def test_update_endpoint_steam_missing_id(client, mock_components, caplog):
    """Test update endpoint with missing Steam ID."""
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "",
        "vanity_url": "",
        "enabled": True,
    }

    with caplog.at_level(logging.WARNING, logger="src.web.api"):
        response = client.post("/api/update", json={"source": "steam"})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Source is not properly configured — check these: 'steam_id', 'vanity_url'."
    )
    assert "steam_id" in caplog.text or "vanity_url" in caplog.text


def test_update_endpoint_steam_api_error(client, mock_components):
    """Test update endpoint handles Steam API error during validation.

    A started sync answers 200 and reports its errors through the status
    endpoint. The sync manager is stubbed because the real one spawns a
    thread that outlives the test calling the live Steam API.
    """
    app_state.config["inputs"]["steam"] = {
        "plugin": "steam",
        "api_key": "test_api_key",
        "steam_id": "76561198000000000",
        "enabled": True,
    }
    sync_manager = Mock(spec=SyncManager)
    sync_manager.start_sync.return_value = (True, "Started sync for Steam")

    with patch("src.web.api.get_sync_manager", return_value=sync_manager):
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
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.fetch",
            return_value=iter([mock_book]),
        ),
        patch(
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
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
        assert "goodreads_csv" in data["sources"]
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
        app_state.config["inputs"]["books"] = {
            "plugin": "goodreads_csv",
            "path": "/srv/private/library.csv",
            "enabled": True,
        }

        with patch(
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
            return_value=["CSV file not found: /srv/private/library.csv"],
        ):
            response = client.post("/api/update", json={"source": "books"})

        assert response.status_code == 400
        assert response.json()["detail"] == SOURCE_MISCONFIGURED_DETAIL
        assert "/srv/private" not in response.text

    def test_a_containment_refusal_names_the_field_and_nothing_else(self, client):
        """Unmocked: the real refusal quotes the path and the config key.

        Only the schema's own field name survives onto the wire, so neither
        the operator's path nor the allowlist setting is disclosed.
        """
        app_state.config["inputs"]["books"] = {
            "plugin": "goodreads_csv",
            "path": "/etc/shadow",
            "enabled": True,
        }

        response = client.post("/api/update", json={"source": "books"})

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Source is not properly configured — check its 'path' setting."
        )
        assert "/etc/shadow" not in response.text
        assert "allowed_source_roots" not in response.text

    def test_a_real_missing_file_reveals_neither_the_path_nor_the_field(
        self, client, tmp_path
    ):
        """Unmocked twin of the fabricated "not found" above.

        An allowed-but-absent file is the case a caller would probe, and its
        answer carries no path.
        """
        app_state.config["inputs"]["books"] = {
            "plugin": "goodreads_csv",
            "path": str(tmp_path / "library.csv"),
            "enabled": True,
        }

        response = client.post("/api/update", json={"source": "books"})

        assert response.status_code == 400
        assert response.json()["detail"] == SOURCE_MISCONFIGURED_DETAIL
        assert "library.csv" not in response.text

    def test_a_newline_in_the_source_id_cannot_forge_a_log_line(self, client, caplog):
        """CR/LF is escaped before the id reaches the log (CWE-117)."""
        with caplog.at_level(logging.INFO, logger="src.web.api"):
            response = client.post(
                "/api/update", json={"source": "ok\nWARNING forged line"}
            )

        assert response.status_code == 400
        assert "\\nWARNING forged line" in caplog.text

    def test_a_newline_in_the_plugins_reason_cannot_forge_a_log_line(
        self, client, mock_components, caplog
    ):
        """The plugin's message is escaped too, not just the id.

        Plugin messages quote configured values verbatim, and a value
        authored in ``config.yaml`` never passes the write door that
        refuses newlines.
        """
        app_state.config["inputs"]["steam"] = {
            "plugin": "steam",
            "api_key": "test_api_key",
            "steam_id": "76561198000000000",
            "enabled": True,
        }

        with (
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=["'api_key' is invalid\nWARNING forged line"],
            ),
            caplog.at_level(logging.WARNING, logger="src.web.api"),
        ):
            response = client.post("/api/update", json={"source": "steam"})

        assert response.status_code == 400
        assert "\\nWARNING forged line" in caplog.text
        assert "\nWARNING forged line" not in caplog.text


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
        "plugin": "goodreads_csv",
        "path": "inputs/books.csv",
        "content_type": content_type,
        "enabled": True,
    }
    sync_manager = Mock(spec=SyncManager)
    sync_manager.start_sync.return_value = (True, "Started sync for Typed")
    enrichment_manager = Mock(spec=WebEnrichmentManager)
    enrichment_manager.start_enrichment.return_value = (True, "started")

    with (
        patch("src.web.api.get_sync_manager", return_value=sync_manager),
        patch("src.web.api.get_enrichment_manager", return_value=enrichment_manager),
        patch(
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
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
        with caplog.at_level(logging.WARNING, logger="src.web.api"):
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
        with caplog.at_level(logging.WARNING, logger="src.web.api"):
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


def test_put_user_preferences_partial(client, mock_components):
    """PUT /api/users/1/preferences merges partial update."""
    back_mock_preference_store(mock_components["storage"])

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


def test_put_user_preferences_accepts_max_variety_penalty(client, mock_components):
    """variety_penalty at the 5.0 maximum is accepted and saved."""
    merge = back_mock_preference_store(mock_components["storage"])

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 5.0},
    )
    assert response.status_code == 200
    assert response.json()["variety_penalty"] == 5.0
    merge.assert_called_once()


def test_put_user_preferences_accepts_zero_variety_penalty(client, mock_components):
    """variety_penalty at the 0.0 minimum is accepted and saved (penalty off)."""
    merge = back_mock_preference_store(mock_components["storage"])

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": 0.0},
    )
    assert response.status_code == 200
    assert response.json()["variety_penalty"] == 0.0
    merge.assert_called_once()


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


def test_put_user_preferences_rejects_negative_variety_penalty(client, mock_components):
    """variety_penalty below 0.0 is rejected with a 422 and never saved."""
    merge = back_mock_preference_store(mock_components["storage"])

    response = client.put(
        "/api/users/1/preferences",
        json={"variety_penalty": -0.1},
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
_LENGTH_PREFERENCE_NAMES = [member.value for member in LengthPreference]


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

    def test_a_full_preferences_page_still_saves(self, client, mock_components):
        """The bound is above what the UI sends: every scorer plus rules."""
        merge = back_mock_preference_store(mock_components["storage"])

        response = client.put(
            "/api/users/1/preferences",
            json={
                "scorer_weights": dict.fromkeys(SCORER_NAME_MAP, 2.0),
                "custom_rules": ["avoid horror", "prefer sci-fi"],
                "content_length_preferences": {"book": "short", "movie": "any"},
                "theme": "nord",
            },
        )

        assert response.status_code == 200
        assert response.json()["scorer_weights"] == dict.fromkeys(SCORER_NAME_MAP, 2.0)
        merge.assert_called_once()

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

    def test_the_refusal_names_what_would_have_been_accepted(
        self, client, mock_components
    ):
        """A 422 saying only "unknown" leaves the operator guessing."""
        back_mock_preference_store(mock_components["storage"])

        response = client.put(
            "/api/users/1/preferences", json={"scorer_weights": {"recency": 1.0}}
        )

        assert "genre_match" in json.dumps(response.json())

    @pytest.mark.parametrize("preference", _LENGTH_PREFERENCE_NAMES)
    def test_every_legal_name_is_still_accepted(
        self, client, mock_components, preference
    ):
        """Closing the set the wrong way refuses what the CLI can set."""
        back_mock_preference_store(mock_components["storage"])

        response = client.put(
            "/api/users/1/preferences",
            json={
                "scorer_weights": dict.fromkeys(SCORER_NAME_MAP, 2.0),
                "content_length_preferences": dict.fromkeys(
                    _CONTENT_TYPE_NAMES, preference
                ),
            },
        )

        assert response.status_code == 200


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

    @pytest.mark.parametrize("user_id", [0, -1])
    def test_the_read_side_rejects_a_non_positive_user_id_too(self, client, user_id):
        """Both handlers on the route carry the same bound."""
        assert client.get(f"/api/users/{user_id}/preferences").status_code == 422


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
    # The password stamp is fetched per row, and an unstubbed Mock is not
    # subscriptable.
    mock_components["storage"].describe_account = Mock(return_value=None)

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


def test_recommendations_variety_penalty_defaults_to_zero(client, mock_components):
    """variety_penalty is 0.0 on the wire when the producer sets none."""
    mock_item = ContentItem(
        id="1",
        title="Plain Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    mock_recommendations = [
        Recommendation(
            item=mock_item,
            score=0.85,
            reasoning="Recommended",
            score_breakdown={"genre_match": 0.9},
        )
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
        rating=UNSET,
        review=UNSET,
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
        rating=UNSET,
        review=UNSET,
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
    assert empty.status_code == 422

    whitespace = client.patch(
        "/api/items/42?user_id=1",
        json={"status": "completed", "review": "   "},
    )
    assert whitespace.status_code == 422

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
        rating=UNSET,
        review=UNSET,
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
        review=UNSET,
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

    def test_non_latin_search_term_reaches_storage_unmangled(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """A non-Latin search term survives the round trip to storage and back.

        Storage is mocked, so the matching itself is not exercised here —
        ``tests/test_sorting.py`` covers that. What this pins is the web half:
        the percent-encoded term arrives at storage byte-for-byte and a
        matched title in a non-Latin script comes back through the JSON
        response unchanged.
        """
        mock_items = [
            ContentItem(
                id="1",
                title="進撃の巨人",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                source="tmdb",
            )
        ]
        mock_components["storage"].get_content_items = Mock(
            spec=StorageManager.get_content_items, return_value=mock_items
        )

        response = client.get("/api/items", params={"search": "進撃の巨人"})
        assert response.status_code == 200

        call_kwargs = mock_components["storage"].get_content_items.call_args[1]
        assert call_kwargs["search"] == "進撃の巨人"
        assert response.json()[0]["title"] == "進撃の巨人"

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
        _rec_record(book_item)
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
                "Sync already in progress for Goodreads CSV",
            )
            mock_get_sync_manager.return_value = mock_manager

            with patch(
                "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
                return_value=[],
            ):
                response = client.post("/api/update", json={"source": "goodreads_csv"})

            assert response.status_code == 409
            detail = response.json()["detail"]
            assert "Sync already in progress" in detail
            assert "Goodreads CSV" in detail
            assert mock_manager.start_sync.call_args.args[0] == "Goodreads CSV"

    def test_update_allows_different_sources_concurrently(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """A second source is accepted while a different source is running.

        Plants a real RUNNING job for Steam in the global SyncManager
        before triggering a Goodreads CSV sync. The endpoint must reject
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
            "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
            return_value=[],
        ):
            # Drop the captured execute_multi_source_sync into a no-op so
            # the second sync's daemon doesn't try to actually run.
            with patch(
                "src.web.api.execute_multi_source_sync",
                return_value=[
                    SyncJob(source="Goodreads CSV", status=SyncStatus.RUNNING)
                ],
            ):
                response = client.post("/api/update", json={"source": "goodreads_csv"})

        assert response.status_code == 200, response.text
        assert "Sync started" in response.json()["message"]
        # Manager now tracks both jobs; the Steam one is still running
        # and the Goodreads CSV one was added on top.
        assert manager.is_running("Steam") is True
        assert "Goodreads CSV" in {
            job["source"] for job in manager.get_status()["jobs"]
        }


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
                "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
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
                "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
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
                "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post(
                "/api/update", json={"source": "all", "max_workers": 8}
            )
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200, response.text
        assert captured_kwargs.get("max_workers") == 8

    def test_the_config_reaches_the_executor(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Without it every sync warns that a live YAML token is stranded.

        The stranded-credential check reads ``inputs`` for the sources the
        database does not hold, so the executor has to be handed the config
        this endpoint already resolved from.
        """
        captured_kwargs: dict = {}
        completion = threading.Event()
        with (
            patch(
                "src.web.api.execute_multi_source_sync",
                side_effect=self._make_capture(captured_kwargs, completion),
            ),
            patch(
                "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post("/api/update", json={"source": "all"})
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200
        assert captured_kwargs.get("config") is app_state.config

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
            *, error_callback: SyncErrorCallback, **_kwargs: object
        ) -> list[SyncResult]:
            try:
                error_callback("Sonarr", self.REMEDY)
                return [SyncResult(source_name="Goodreads Csv", items_synced=3)]
            finally:
                completion.set()

        with (
            patch("src.web.api.execute_multi_source_sync", side_effect=fake_execute),
            patch(
                "src.ingestion.sources.goodreads_csv.GoodreadsCsvPlugin.validate_config",
                return_value=[],
            ),
        ):
            response = client.post("/api/update", json={"source": "all"})
            assert completion.wait(timeout=5.0), "background sync did not run"

        assert response.status_code == 200
        # Completed, not failed: the run saved items, which is the shape that
        # used to leave the message nowhere in the response at all.
        job = client.get("/api/sync/status").json()["jobs"][0]
        assert job["status"] == "completed"
        assert job["errors"] == [{"source": "Sonarr", "message": self.REMEDY}]


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
    ) -> Recommendation:
        """Create a mock recommendation matching engine output."""
        item = ContentItem(
            id=item_id,
            db_id=int(item_id),
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        return _rec_record(item)

    def test_phase1_recommendations_event(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE stream emits a phase 1 'recommendations' event with items.

        Phase 1 carries no blurb because the endpoint asks for none, not
        because it blanks the field afterwards, so the engine here answers
        ``use_llm=True`` with a blurb the way the real one does. An empty slot
        is then evidence about the request the endpoint made.
        """
        rec = self._make_recommendation()
        mock_components["engine"].generate_recommendations.side_effect = (
            lambda use_llm, **kwargs: [
                replace(rec, llm_reasoning="blurb from the engine") if use_llm else rec
            ]
        )
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
        assert (
            mock_components["engine"].generate_recommendations.call_args.kwargs[
                "use_llm"
            ]
            is False
        )

    def test_phase1_tv_season_includes_db_id(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """SSE phase 1 serializes a TV season rec with its parent show db_id.

        The streaming path shares ``Recommendation.to_payload`` with the sync
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
        rec = _rec_record(season_item)
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


def _free_stream_slots() -> int:
    """How much of the process-wide stream budget is available right now."""
    taken = 0
    while _slots.acquire(blocking=False):
        taken += 1
    for _ in range(taken):
        _slots.release()
    return taken


@pytest.fixture()
def whole_stream_budget() -> Iterator[None]:
    """Attribute a leaked slot to the test that leaked it.

    ``_slots`` is process-global and nothing else resets it, so a leak
    anywhere surfaces as an unexplained 503 in the cap tests below.
    """
    assert _free_stream_slots() == MAX_CONCURRENT_STREAMS
    yield
    assert _free_stream_slots() == MAX_CONCURRENT_STREAMS


@pytest.mark.usefixtures("whole_stream_budget")
class TestStreamConcurrencyCap:
    """Each in-flight stream holds one of anyio's 40 threadpool tokens per
    generator step, so uncapped, streams left open stop every endpoint
    answering. The two SSE routes share one bounded budget and answer 503
    past it.
    """

    STREAM_URL = "/api/recommendations/stream?type=book&count=1"

    def test_the_cap_answers_503_while_the_rest_of_the_api_still_answers(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """One saturated budget, and ``GET /api/status`` unaffected by it."""
        holding = threading.Semaphore(0)
        release = threading.Event()

        def blocked_scoring_pass(**_kwargs):
            holding.release()
            release.wait(timeout=_STALL_TIMEOUT_SECONDS)
            return []

        mock_components["engine"].generate_recommendations.side_effect = (
            blocked_scoring_pass
        )
        mock_components["storage"].get_user_preference_config.return_value = None

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_STREAMS) as pool:
            in_flight = [
                pool.submit(client.get, self.STREAM_URL)
                for _ in range(MAX_CONCURRENT_STREAMS)
            ]
            try:
                for _ in range(MAX_CONCURRENT_STREAMS):
                    assert holding.acquire(timeout=_STALL_TIMEOUT_SECONDS)

                refused = client.get(self.STREAM_URL)
                refused_chat = client.post("/api/chat", json={"message": "hi"})
                unrelated = client.get("/api/status")
            finally:
                release.set()

            assert refused.status_code == 503
            assert refused.json()["detail"] == TOO_MANY_STREAMS_DETAIL
            assert refused_chat.status_code == 503
            assert refused_chat.json()["detail"] == TOO_MANY_STREAMS_DETAIL
            assert unrelated.status_code == 200
            assert [stream.result().status_code for stream in in_flight] == [
                200
            ] * MAX_CONCURRENT_STREAMS

        assert client.get(self.STREAM_URL).status_code == 200


@pytest.mark.usefixtures("whole_stream_budget")
class TestStreamBudgetIsGivenBack:
    """The budget is process-wide and never refilled, so a slot leaked once
    per request turns the cap itself into the outage it prevents. Each case
    runs one request past the cap: a leak refuses the last one.
    """

    STREAM_URL = "/api/recommendations/stream?type=book&count=1"

    def test_giving_one_slot_back_twice_does_not_widen_the_budget(self) -> None:
        """Two paths release a slot and either may be the only one.

        The second lands in a ``weakref.finalize`` callback, where the
        over-release is unraisable and the widened budget trips no cap
        assertion — so it is taken at the guard.
        """
        assert _slots.acquire(blocking=False)
        slot = _HeldSlot()

        slot.give_back()
        slot.give_back()

        assert _free_stream_slots() == MAX_CONCURRENT_STREAMS

    def test_a_stream_that_ends_in_an_error_event_returns_its_slot(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """The failure path is the common one while the LLM is down."""
        mock_components["engine"].generate_recommendations.side_effect = RuntimeError(
            "engine down"
        )
        mock_components["storage"].get_user_preference_config.return_value = None

        for _ in range(MAX_CONCURRENT_STREAMS + 1):
            response = client.get(self.STREAM_URL)
            assert response.status_code == 200
            assert _parse_sse_events(response.text)[-1]["type"] == "error"

    def test_a_chat_stream_returns_its_slot_to_the_shared_budget(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """One budget for two endpoints: a chat leak refuses recommendations."""
        conversation = Mock(spec=ConversationEngine)
        conversation.process_message.side_effect = lambda **_kwargs: iter(
            [ConversationChunk(chunk_type="done")]
        )
        app_state.conversation_engine = conversation
        mock_components["engine"].generate_recommendations.return_value = []
        mock_components["storage"].get_user_preference_config.return_value = None

        for _ in range(MAX_CONCURRENT_STREAMS + 1):
            assert client.post("/api/chat", json={"message": "hi"}).status_code == 200

        assert client.get(self.STREAM_URL).status_code == 200

    def test_a_stream_abandoned_before_its_first_chunk_returns_its_slot(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Closing an unstarted generator runs no frame code, so its ``finally``
        never fires. Taken at the helper: no TestClient request can open the
        window between the handler returning and Starlette's first pull.
        """
        for _ in range(MAX_CONCURRENT_STREAMS + 1):
            abandoned = bounded_sse(iter(["data: never read\n\n"]))
            abandoned.close()
            del abandoned
            gc.collect()

        mock_components["engine"].generate_recommendations.return_value = []
        mock_components["storage"].get_user_preference_config.return_value = None
        assert client.get(self.STREAM_URL).status_code == 200

    def test_closing_an_unstarted_generator_runs_no_finally(self) -> None:
        """The premise of the case above: without it, nothing is being fixed."""
        ran: list[str] = []

        def body() -> Iterator[str]:
            try:
                yield "chunk"
            finally:
                ran.append("finally")

        unstarted = body()
        unstarted.close()

        assert ran == []

    def test_a_request_refused_before_the_stream_starts_takes_no_slot(
        self, client: TestClient, anonymous_client: TestClient, mock_components: dict
    ) -> None:
        """Otherwise an unauthenticated caller empties the budget for free."""
        for _ in range(MAX_CONCURRENT_STREAMS + 1):
            assert anonymous_client.get(self.STREAM_URL).status_code == 401
            assert (
                client.get(
                    "/api/recommendations/stream?type=invalid&count=1"
                ).status_code
                == 400
            )

        mock_components["engine"].generate_recommendations.return_value = []
        mock_components["storage"].get_user_preference_config.return_value = None
        assert client.get(self.STREAM_URL).status_code == 200


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
        """No config returns 503."""
        app_state.config = None
        response = client.get("/api/gog/status")
        assert response.status_code == 503
        assert response.json()["detail"] == "Config unavailable"


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

    def test_save_token_failure_returns_400(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """DB save failure returns generic 400."""
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

    def test_no_storage_returns_503(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Missing storage returns 503."""
        app_state.config["inputs"]["epic_games"] = {
            "plugin": "epic_games",
            "enabled": True,
        }
        app_state.storage = None

        response = client.post("/api/epic/exchange", json={"code_or_json": "some_code"})

        assert response.status_code == 503
        assert response.json()["detail"] == "Storage unavailable"

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

    def test_no_config_returns_503(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """Missing config returns 503."""
        app_state.config = None
        response = client.post("/api/epic/exchange", json={"code_or_json": "some_code"})
        assert response.status_code == 503
        assert response.json()["detail"] == "Config unavailable"


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
        """No config returns 503."""
        app_state.config = None
        response = client.get("/api/epic/status")
        assert response.status_code == 503
        assert response.json()["detail"] == "Config unavailable"


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

    def test_reset_enrichment_no_storage(self, client, mock_components):
        """Reset when storage not available returns 503."""
        app_state.storage = None
        response = client.post(
            "/api/enrichment/reset",
            json={"reset_type": "all"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Storage unavailable"


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
        storage.delete_credential.return_value = True

        response = client.delete(f"/api/{provider}/token?source_id={source_id}")

        assert response.status_code == 404, response.text
        storage.delete_credential.assert_not_called()

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
    """Tests for GET /api/trakt/status.

    Only the 503 lives here. Both flags are proved against a real credential
    store in ``tests/web/test_oauth_source_binding.py``; a mocked pair with
    ``enabled == connected`` passed a handler returning one flag for both.
    """

    def test_no_storage_returns_503(self, client, mock_components) -> None:
        """Storage down is 503, not a fabricated ``enabled: false``.

        ``resolve_trakt_client_credentials`` raises without storage, and the
        handler answered that with 200 ``{"enabled": false}`` — the same body
        as a machine that has no Trakt credentials, for a state that is not
        that.
        """
        app_state.storage = None

        response = client.get("/api/trakt/status")

        assert response.status_code == 503
        assert response.json()["detail"] == "Storage unavailable"


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

    def test_no_storage_returns_503(self, client, mock_components) -> None:
        """Start returns 503 'Storage unavailable' when storage is None."""
        app_state.storage = None

        response = client.post("/api/trakt/start-device-flow")

        assert response.status_code == 503
        assert response.json()["detail"] == "Storage unavailable"


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

    def test_no_storage_returns_503(self, client, mock_components) -> None:
        """Poll returns 503 'Storage unavailable' when storage is None."""
        app_state.storage = None

        response = client.post(
            "/api/trakt/poll-device-approval", json={"device_code": "dev1234567"}
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Storage unavailable"


class TestStreamRecommendationsSignalRegression:
    """Bug reported: streaming blurbs cited ignored/unrated items as taste refs.

    Bug reported: ``/recommendations/stream`` fetched the LLM blurb "taste
    reference" list via ``get_completed_items(min_rating=None)`` with no
    ignored/unrated filter, so a streamed "since you enjoyed X" blurb could
    cite an ignored or completed-but-unrated item.
    Root cause: ``generate_sse`` called ``get_completed_items`` directly
    instead of the shared signal accessor.
    Fix: it now calls ``get_signal_items``, so the blurb generator only ever
    receives the taste-signal set.
    """

    def test_blurb_generation_receives_signal_items_regression(
        self, client, mock_components
    ) -> None:
        """The blurb generator is fed the signal set, not the full completed set."""
        engine = mock_components["engine"]
        storage = mock_components["storage"]

        candidate = ContentItem(
            id="cand",
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        engine.generate_recommendations.return_value = [
            Recommendation(item=candidate, score=0.9, reasoning="because sci-fi")
        ]

        signal_item = ContentItem(
            id="sig",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        ignored_item = ContentItem(
            id="ign",
            title="Ignored Favorite",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            ignored=True,
        )
        storage.get_signal_items.return_value = [signal_item]
        storage.get_completed_items.return_value = [signal_item, ignored_item]
        storage.get_user_preference_config.return_value = None
        engine.generate_blurb_for_item.return_value = "a blurb"

        response = client.get("/api/recommendations/stream?type=book&count=1")

        assert response.status_code == 200
        assert engine.generate_blurb_for_item.called
        # generate_blurb_for_item(content_type, item, consumed_items, refs)
        consumed_arg = engine.generate_blurb_for_item.call_args.args[2]
        consumed_titles = {item.title for item in consumed_arg}
        assert consumed_titles == {"Dune"}
        assert "Ignored Favorite" not in consumed_titles


# Sensitive and non-sensitive leaves reused across the settings endpoint tests.
_SETTINGS_SECRET_KEY = "enrichment.providers.tmdb.api_key"
_SETTINGS_INT_KEY = "recommendations.default_count"
_SETTINGS_OLLAMA_URL_KEY = "ollama.base_url"


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
        assert body["sections"][0]["section"] == "features"

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
        assert storage.get_setting(_SETTINGS_INT_KEY) == 7
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
        assert storage.list_settings() == {}
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
        assert storage.get_setting(_SETTINGS_INT_KEY) == 7

    def test_put_restart_required_persists_but_flagged(self, settings_env) -> None:
        client, storage, config = settings_env

        response = client.put(
            "/api/settings", json={"updates": {"logging.level": "DEBUG"}}
        )

        assert response.status_code == 200
        assert storage.get_setting("logging.level") == "DEBUG"
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
        assert storage.get_setting(_SETTINGS_INT_KEY) is None
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
        assert storage.list_settings() == {}
        assert get_allowed_source_roots() == before

    def test_a_non_local_ollama_base_url_is_refused_over_http(
        self, settings_env
    ) -> None:
        """The locality rule is tested at the service; this is the door.

        A host with no ASCII dot reads as one local label until IDNA splits
        it, which is how this one reached the service.
        """
        client, storage, config = settings_env
        before = config["ollama"]["base_url"]

        response = client.put(
            "/api/settings",
            json={"updates": {_SETTINGS_OLLAMA_URL_KEY: "http://ollama。example。com"}},
        )

        assert response.status_code == 422
        assert response.json()["detail"]["key"] == _SETTINGS_OLLAMA_URL_KEY
        assert storage.get_setting(_SETTINGS_OLLAMA_URL_KEY) is None
        assert config["ollama"]["base_url"] == before

    def test_a_local_ollama_base_url_is_stored_as_it_will_be_dialled(
        self, settings_env
    ) -> None:
        """A trailing slash is a path, and the stored value is what httpx gets."""
        client, storage, config = settings_env

        response = client.put(
            "/api/settings",
            json={"updates": {_SETTINGS_OLLAMA_URL_KEY: " http://ollama:11434/ "}},
        )

        assert response.status_code == 200
        assert storage.get_setting(_SETTINGS_OLLAMA_URL_KEY) == "http://ollama:11434"
        assert config["ollama"]["base_url"] == "http://ollama:11434"

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
        assert storage.has_global_secret(_SETTINGS_SECRET_KEY) is True
        # The secret is never persisted in the plaintext settings table.
        assert storage.list_settings() == {}
        # And it surfaces only as has_secret, never as a value.
        secret = self._find(client.get("/api/settings").json(), _SETTINGS_SECRET_KEY)
        assert secret["has_secret"] is True
        assert "value" not in secret

        delete = client.delete(f"/api/settings/secret/{_SETTINGS_SECRET_KEY}")

        assert delete.status_code == 204
        assert storage.has_global_secret(_SETTINGS_SECRET_KEY) is False

    def test_secret_put_rejects_non_sensitive_key(self, settings_env) -> None:
        client, storage, _config = settings_env

        response = client.put(
            "/api/settings/secret",
            json={"key": _SETTINGS_INT_KEY, "value": "nope"},
        )

        assert response.status_code == 400
        assert storage.list_settings() == {}

    def test_secret_delete_rejects_non_sensitive_key(self, settings_env) -> None:
        client, _storage, _config = settings_env

        response = client.delete(f"/api/settings/secret/{_SETTINGS_INT_KEY}")

        assert response.status_code == 400

    def test_get_returns_503_when_config_unavailable(self, settings_env) -> None:
        client, _storage, _config = settings_env
        app_state.config = None

        assert client.get("/api/settings").status_code == 503

    def test_get_returns_503_when_storage_unavailable(self, settings_env) -> None:
        client, _storage, _config = settings_env
        app_state.storage = None

        assert client.get("/api/settings").status_code == 503

    def test_put_returns_503_when_config_unavailable(self, settings_env) -> None:
        client, _storage, _config = settings_env
        app_state.config = None

        response = client.put("/api/settings", json={"updates": {_SETTINGS_INT_KEY: 7}})
        assert response.status_code == 503

    def test_put_returns_503_when_storage_unavailable(self, settings_env) -> None:
        client, _storage, _config = settings_env
        app_state.storage = None

        response = client.put("/api/settings", json={"updates": {_SETTINGS_INT_KEY: 7}})
        assert response.status_code == 503

    def test_delete_returns_503_when_config_unavailable(self, settings_env) -> None:
        client, _storage, _config = settings_env
        app_state.config = None

        assert client.delete(f"/api/settings/{_SETTINGS_INT_KEY}").status_code == 503

    def test_delete_returns_503_when_storage_unavailable(self, settings_env) -> None:
        client, _storage, _config = settings_env
        app_state.storage = None

        assert client.delete(f"/api/settings/{_SETTINGS_INT_KEY}").status_code == 503

    def test_secret_put_returns_503_when_storage_unavailable(
        self, settings_env
    ) -> None:
        client, _storage, _config = settings_env
        app_state.storage = None

        response = client.put(
            "/api/settings/secret",
            json={"key": _SETTINGS_SECRET_KEY, "value": "x"},
        )
        assert response.status_code == 503

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
        assert storage.get_setting(_SETTINGS_INT_KEY) == 11
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

        assert storage.get_setting(_SETTINGS_INT_KEY) is None
        assert get_leaf(
            app_state.config, tuple(_SETTINGS_INT_KEY.split("."))
        ) == default_of(
            _SETTINGS_INT_KEY
        ), "the database and the running config disagree about a reset setting"

    def test_secret_delete_returns_503_when_storage_unavailable(
        self, settings_env
    ) -> None:
        client, _storage, _config = settings_env
        app_state.storage = None

        response = client.delete(f"/api/settings/secret/{_SETTINGS_SECRET_KEY}")
        assert response.status_code == 503


_STORAGE_UNAVAILABLE = "Storage unavailable"
_CONFIG_UNAVAILABLE = "Config unavailable"
_ENGINE_UNAVAILABLE = "Recommendation engine unavailable"
_MEMORY_UNAVAILABLE = "Memory manager unavailable"
_CHAT_UNAVAILABLE = "Chat is not available. LLM is not configured."

# The 503 message each guarded component produces, keyed by its AppState field.
_UNAVAILABLE_DETAIL = {
    "storage": _STORAGE_UNAVAILABLE,
    "config": _CONFIG_UNAVAILABLE,
    "engine": _ENGINE_UNAVAILABLE,
    "memory_manager": _MEMORY_UNAVAILABLE,
    "conversation_engine": _CHAT_UNAVAILABLE,
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
    _Endpoint(
        "GET",
        "/api/recommendations/stream",
        ("engine",),
        url="/api/recommendations/stream?type=book",
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
        body={"id": "my_books", "plugin": "goodreads_csv"},
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
    _Endpoint("POST", "/api/chat", ("conversation_engine",), body={"message": "hi"}),
    _Endpoint("POST", "/api/chat/reset", ("conversation_engine",)),
    _Endpoint("GET", "/api/chat/history", ("memory_manager",)),
    _Endpoint("GET", "/api/memories", ("memory_manager",)),
    _Endpoint(
        "POST", "/api/memories", ("memory_manager",), body={"memory_text": "sci-fi"}
    ),
    _Endpoint(
        "PUT",
        "/api/memories/{memory_id}",
        ("memory_manager",),
        url="/api/memories/1",
        body={"memory_text": "sci-fi"},
    ),
    _Endpoint(
        "DELETE",
        "/api/memories/{memory_id}",
        ("storage",),
        url="/api/memories/1",
    ),
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

# The endpoints an outage other than storage's can be measured on.
_NON_STORAGE_ENDPOINTS = [
    endpoint
    for endpoint in _GUARDED_ENDPOINTS + _OPEN_ENDPOINTS
    if set(endpoint.requires) - {"storage"}
]


def _clear_dependencies() -> None:
    """Drop every component but storage, ahead of a request.

    Storage stays because authentication reads the session out of it: gone, a
    503 answers before any handler's own guard runs.
    """
    app_state.config = None
    app_state.engine = None
    app_state.memory_manager = None
    app_state.conversation_engine = None


class TestDependencyGuards:
    """Every uninitialised dependency answers 503, one message per dependency.

    An absent component is unavailability, not a server fault, and one server
    state has to read the same way everywhere: a 500 from ``/api/items`` and a
    503 from ``/api/memories`` described the identical outage two ways.
    """

    @pytest.mark.parametrize("endpoint", _NON_STORAGE_ENDPOINTS, ids=_endpoint_id)
    def test_guarded_endpoint_returns_503(self, client, endpoint) -> None:
        """With the rest down, each endpoint names one of its dependencies.

        Storage stays up because authentication reads it; the routes needing
        only storage are the per-component case below.
        """
        _clear_dependencies()

        response = client.request(endpoint.method, endpoint.target, json=endpoint.body)

        assert response.status_code == 503
        assert response.json()["detail"] in endpoint.details

    def test_that_sweep_covers_most_of_the_guarded_routes(self) -> None:
        """The filter above is a subset, not an accidental emptying."""
        assert len(_NON_STORAGE_ENDPOINTS) > 10
        assert len(_NON_STORAGE_ENDPOINTS) < len(_GUARDED_ENDPOINTS)

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

        # The source migrations that reload runs want a database, and the
        # storage authentication needs here is a mock.
        with (
            patch("src.web.state.migrate_source_labels"),
            patch("src.web.state.migrate_source_config_plugins"),
            patch("src.web.state.migrate_source_attribution"),
        ):
            response = client.request(
                endpoint.method, endpoint.target, json=endpoint.body
            )

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
        # Storage is what authenticated the request, so it reports up: no
        # caller can reach this route with it down.
        assert body["components"]["storage"] is True

    def test_every_api_route_is_classified(self, client, mock_components) -> None:
        """Both lists together must name every route the app serves.

        A new handler is unlisted until someone classifies it, and the tests
        above then hold it to the list it landed in: 503 naming each component
        it needs, or an answer that is not a fault at all.
        """
        assert _served_api_routes(mock_components["app"]) == _CLASSIFIED_API_ROUTES

    def test_an_unclassified_route_is_what_that_comparison_catches(
        self, mock_components
    ) -> None:
        """The same comparison, against an app carrying one route nobody listed.

        Set equality passing is not by itself evidence that anything is being
        checked; this is what says the test above would go red rather than
        quietly widen.
        """
        app = mock_components["app"]

        @app.get("/api/unclassified")
        def _unclassified() -> dict[str, str]:
            return {}

        assert _served_api_routes(app) - _CLASSIFIED_API_ROUTES == {
            ("GET", "/api/unclassified")
        }


# The ``AppState`` field each guard defends, keyed by the guard itself.
_GUARD_COMPONENT = {
    require_storage: "storage",
    require_config: "config",
    require_engine: "engine",
    require_memory_manager: "memory_manager",
    require_conversation_engine: "conversation_engine",
}


def _declared_guards(route: APIRoute) -> set[str]:
    """Every guarded component in *route*'s dependency tree, off the signatures.

    Walks sub-dependencies too, so a guard a route inherits through
    ``require_plugin`` counts exactly as one it declares itself.
    """
    found: set[str] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        component = _GUARD_COMPONENT.get(dependency.call)
        if component is not None:
            found.add(component)
        pending.extend(dependency.dependencies)
    return found


def _route_for(app: FastAPI, endpoint: _Endpoint) -> APIRoute:
    for route in app.routes:
        if (
            isinstance(route, APIRoute)
            and route.path == endpoint.route
            and endpoint.method in route.methods
        ):
            return route
    raise AssertionError(f"{_endpoint_id(endpoint)} is not served")


class TestClassificationIsDerivedFromTheSignatures:
    """``requires`` must restate what the handler's parameters already say.

    The behavioural matrix proves each declared component answers 503 when it
    is down. It cannot prove the converse — that a guard nobody classified is
    absent — and on a handler that acquires one component through two routes it
    cannot see the second acquisition go away. Reading the guards back off the
    dependency tree closes both: delete a ``Required*`` parameter and the
    derived set stops matching the list, whichever way the deletion leans.
    """

    @pytest.mark.parametrize(
        "endpoint",
        _GUARDED_ENDPOINTS + _DEPENDENCY_FREE_ENDPOINTS + _OPEN_ENDPOINTS,
        ids=_endpoint_id,
    )
    def test_route_guards_exactly_the_components_it_is_classified_with(
        self, mock_components, endpoint
    ) -> None:
        """Signature and classification agree, on every route the app serves."""
        route = _route_for(mock_components["app"], endpoint)

        assert _declared_guards(route) == set(endpoint.requires)

    def test_the_deriver_reads_the_signature_and_not_the_list(self) -> None:
        """A guard that is not in the parameters is not in the derived set.

        Without this the test above could be passing on a deriver that reports
        whatever it is asked about. Two handlers differing only in that
        parameter derive differently, so removing one really is what the
        parametrised case sees.
        """
        probe = FastAPI()

        @probe.get("/guarded")
        def _guarded(storage: RequiredStorage) -> dict[str, str]:
            return {}

        @probe.get("/unguarded")
        def _unguarded() -> dict[str, str]:
            return {}

        derived = {
            route.path: _declared_guards(route)
            for route in probe.routes
            if isinstance(route, APIRoute)
        }

        assert derived == {"/guarded": {"storage"}, "/unguarded": set()}


# Every route the app serves, in one list, so the auth cases below cannot
# sample: ``test_every_api_route_is_classified`` pins this against the app.
_ALL_ENDPOINTS = _GUARDED_ENDPOINTS + _DEPENDENCY_FREE_ENDPOINTS

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

    @pytest.mark.parametrize("endpoint", _ALL_ENDPOINTS, ids=_endpoint_id)
    def test_a_request_with_no_cookie_is_401(self, anonymous_client, endpoint) -> None:
        """Every component is up, so a 401 is authentication and nothing else."""
        response = anonymous_client.request(
            endpoint.method, endpoint.target, json=endpoint.body
        )

        assert response.status_code == 401
        assert response.json()["detail"] == UNAUTHORIZED_DETAIL

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("/api/items", id="api"),
            pytest.param("/api/memories", id="chat"),
        ],
    )
    def test_a_request_with_an_unknown_cookie_is_401(
        self, mock_components, target
    ) -> None:
        """A dead session is refused like an absent one.

        One route per router: the branch is inside ``require_session``, and the
        no-cookie sweep proves every route carries it.
        """
        client = TestClient(
            mock_components["app"], cookies={SESSION_COOKIE: _WRONG_SESSION}
        )

        response = client.get(target)

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

    def test_the_deriver_reads_the_dependency_tree(self) -> None:
        """Without this, the assertion above could hold on a deriver that lies."""
        probe = FastAPI()

        @probe.get("/api/open")
        def _open() -> dict[str, str]:
            return {}

        @probe.get("/api/closed", dependencies=[Depends(require_session)])
        def _closed() -> dict[str, str]:
            return {}

        assert _exempt_api_routes(probe) == {("GET", "/api/open")}


def _user_id_fields(dependant: Dependant) -> Iterator[Any]:
    """Yield the ``FieldInfo`` of every ``user_id`` one request can carry.

    Body fields included: ``ChatRequest`` takes its id there. One level deep,
    because no route carries an id in a sub-dependency or nested model — one
    that did would be invisible.
    """
    for param in dependant.path_params + dependant.query_params:
        if param.name == "user_id":
            yield param.field_info
    for body_param in dependant.body_params:
        # Not ``ModelField.type_``: that is FastAPI's own compatibility shim and
        # a newer release drops it, so the sweep went red on a dependency bump
        # rather than on anything about this app.
        model = body_param.field_info.annotation
        if isinstance(model, type) and issubclass(model, BaseModel):
            for name, field in model.model_fields.items():
                if name == "user_id":
                    yield field


def _unbounded_user_id_params(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``/api`` route taking a ``user_id`` that admits 0 or below."""
    found = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        for field_info in _user_id_fields(route.dependant):
            metadata = getattr(field_info, "metadata", [])
            if not any(getattr(item, "ge", None) == 1 for item in metadata):
                found.add((next(iter(route.methods)), route.path))
    return found


def _user_id_carrying_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``/api`` route the sweep above has anything at all to judge."""
    return {
        (next(iter(route.methods)), route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api")
        if next(_user_id_fields(route.dependant), None) is not None
    }


class TestEveryUserIdParamIsBounded:
    """``UserIdPath``'s comment claims every sibling carries ``ge=1``, and the
    chat router's did not. A non-positive id matches no row, so it reads as an
    empty library rather than a bad request.
    """

    def test_no_route_accepts_a_non_positive_user_id(self, mock_components) -> None:
        assert _unbounded_user_id_params(mock_components["app"]) == set()

    def test_the_sweep_still_finds_ids_to_judge(self, mock_components) -> None:
        """The assertion above also holds over no ``user_id`` params at all.

        ``_user_id_fields`` reads one level deep and keys on the name, so a
        rename or a nested model empties it with nothing going red.
        """
        carrying = _user_id_carrying_routes(mock_components["app"])

        assert ("GET", "/api/users/{user_id}/preferences") in carrying
        assert len(carrying) > 1

    def test_the_deriver_finds_an_unbounded_one(self) -> None:
        """Without this the sweep could hold on a deriver that never matches."""
        probe = FastAPI()

        class _LooseBody(BaseModel):
            user_id: int = Field(default=1)

        @probe.get("/api/loose")
        def _loose(user_id: int = Query(default=1)) -> dict[str, str]:
            return {}

        @probe.post("/api/loose-body")
        def _loose_body(body: _LooseBody) -> dict[str, str]:
            return {}

        @probe.get("/api/tight")
        def _tight(user_id: int = Query(default=1, ge=1)) -> dict[str, str]:
            return {}

        assert _unbounded_user_id_params(probe) == {
            ("GET", "/api/loose"),
            ("POST", "/api/loose-body"),
        }


class TestAServerWithNoStorageAcceptsNothing:
    """``create_app`` populates storage or raises, so nothing reaches this
    branch today. Dropping it makes a half-initialised server 500 on every
    request instead of answering the outage.
    """

    @pytest.fixture()
    def unavailable(self, mock_components, monkeypatch) -> TestClient:
        monkeypatch.setattr(app_state, "storage", None)
        return mock_components["app"]

    def test_a_request_carrying_no_cookie_is_401(self, unavailable) -> None:
        """Nobody is signed in, and no component was read to find that out."""
        response = TestClient(unavailable).get("/api/status")

        assert response.status_code == 401
        assert response.json()["detail"] == UNAUTHORIZED_DETAIL

    def test_a_cookie_that_cannot_be_checked_is_a_503(self, unavailable) -> None:
        """Sessions live in the database, so an unverifiable cookie is an
        outage rather than a refusal the caller could fix."""
        response = TestClient(
            unavailable, cookies={SESSION_COOKIE: _WRONG_SESSION}
        ).get("/api/status")

        assert response.status_code == 503
        assert response.json()["detail"] == _STORAGE_UNAVAILABLE


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
        app_state.storage.claim_account("owner", None, "correct horse battery")
        client = TestClient(real_boot)

        response = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "correct horse battery"},
        )

        assert response.status_code == 200
        assert client.cookies[SESSION_COOKIE] not in caplog.text

    def test_the_settings_view_never_lists_a_credential(self, real_boot) -> None:
        """``GET /api/settings`` renders registry leaves, and none is one."""
        client = authenticated_client(real_boot)
        token = client.cookies[SESSION_COOKIE]

        response = client.get("/api/settings")

        assert response.status_code == 200
        assert token not in response.text

    def test_the_session_cookie_is_closed_to_javascript(self, real_boot) -> None:
        """An XSS that can read the cookie is an XSS that keeps the account."""
        response = authenticated_client(real_boot).post(
            "/api/auth/logout",
        )

        assert response.status_code == 204
        assert "httponly" in response.headers["set-cookie"].lower()

    def test_a_refusal_does_not_echo_what_was_presented(self, anonymous_client) -> None:
        """A 401 quoting the guess would put it in the caller's own logs."""
        anonymous_client.cookies.set(SESSION_COOKIE, _WRONG_SESSION)

        response = anonymous_client.get("/api/status")

        assert response.status_code == 401
        assert _WRONG_SESSION not in response.text


class TestBootWithoutAnAccount:
    """Refusing to start is what left the owner locked out of their instance.

    The window is real, so it is said loudly; it closes on first setup, and
    only the first visitor can complete that.
    """

    def test_an_unclaimed_instance_warns_and_serves(
        self, mock_config, tmp_path, caplog
    ) -> None:
        """The warning is the whole of the operator's instruction."""
        reset_sync_manager()
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            with booted_web_app(storage, mock_config) as app:
                assert TestClient(app).get("/").status_code == 200

        assert "No account on this instance yet" in caplog.text
        reset_sync_manager()

    def test_a_claimed_instance_says_nothing(
        self, mock_config, tmp_path, caplog
    ) -> None:
        """Otherwise it fires on every boot forever and is ignored."""
        reset_sync_manager()
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.claim_account("owner", "Owner", "correct horse battery")

        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            with booted_web_app(storage, mock_config):
                pass

        assert "No account on this instance yet" not in caplog.text
        reset_sync_manager()


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

    def test_the_published_schema_advertises_no_body_on_them_either(
        self, mock_components
    ) -> None:
        """The same claim read off the document clients are generated from."""
        schema = mock_components["app"].openapi()

        assert {
            f"{method.upper()} {path}"
            for path, operations in schema["paths"].items()
            if path.startswith("/api")
            for method, operation in operations.items()
            if method.upper() in _BODYLESS_METHODS and "requestBody" in operation
        } == set()


class TestWritableConfigGuardsItsOwnBlock:
    """The 503 inside ``writable_config``, which no request can reach.

    Both routes using it also declare ``Depends(require_config)``, so config
    being down is answered before the body runs and this branch never fires
    from the outside. It is not redundant: the dependency resolves and is
    released first, and this is what answers if the config goes ``None``
    between then and the write. Driven directly because nothing else can, over
    a booted app so ``app_state`` is restored after the field is cleared.
    """

    def test_a_config_that_went_away_mid_request_is_a_503(
        self, mock_components
    ) -> None:
        """Status and message match every other reader's answer for that state."""
        app_state.config = None

        with pytest.raises(HTTPException) as raised, writable_config():
            pytest.fail("the block ran with no config to write into")

        assert raised.value.status_code == 503
        assert raised.value.detail == _CONFIG_UNAVAILABLE
        # A lock left held by the raise would show up as the next settings
        # write hanging, in whatever test happens to run after this one.
        assert not _config_lock.locked()


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
        mock_components["storage"].get_source_config.return_value = {
            "source_id": "my_books",
            "plugin": "goodreads_csv",
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

    def test_memory_manager_and_storage_are_separate_dependencies(
        self, client, mock_components
    ) -> None:
        """The memory endpoints split across two components, so both are named.

        ``DELETE /memories/{id}`` goes through storage because ``MemoryManager``
        has no delete; with storage up it must keep working while the manager is
        down, and the endpoints that do need the manager must say so.
        """
        app_state.memory_manager = None
        mock_components["storage"].delete_core_memory.return_value = True

        unavailable = client.get("/api/chat/history")
        served = client.delete("/api/memories/1")

        assert unavailable.status_code == 503
        assert unavailable.json()["detail"] == _MEMORY_UNAVAILABLE
        assert served.status_code == 200


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

    def test_stream_serves_without_the_config(self, client, mock_components) -> None:
        """The streaming sibling falls back the same way, so it is pinned too.

        Sampling one of the pair would leave the other free to grow a guard,
        or lose its fallback, with this class still green.
        """
        mock_components["engine"].generate_recommendations.return_value = []
        app_state.config = None

        response = client.get("/api/recommendations/stream?type=book")

        assert response.status_code == 200
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events == [
            {"type": "recommendations", "items": []},
            {"type": "done"},
        ]

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
        storage.get_source_config.return_value = None
        app_state.config = None

        response = client.post(
            "/api/sync/sources", json={"id": "my_books", "plugin": "goodreads_csv"}
        )

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE
        storage.upsert_source_config.assert_not_called()

    def test_delete_refuses_rather_than_sweeping_off_half_a_source_list(
        self, client, mock_components
    ) -> None:
        """Config down, a YAML source on the plugin reads as no source at all."""
        storage = mock_components["storage"]
        storage.get_source_config.return_value = {
            "source_id": "my_books",
            "plugin": "goodreads_csv",
            "enabled": 1,
            "config": {},
            "migrated_at": "2026-01-01T00:00:00",
        }
        app_state.config = None

        response = client.delete("/api/sync/sources/my_books")

        assert response.status_code == 503
        assert response.json()["detail"] == _CONFIG_UNAVAILABLE
        storage.delete_source_config.assert_not_called()
        storage.delete_credentials_for_source.assert_not_called()


# The three plugin-resolving routes that also carry a body, so the order
# between the lookup and body validation is observable on them.
_PLUGIN_ROUTES_WITH_A_BODY = [
    "/api/sync/sources/no_such_source/config",
    "/api/sync/sources/no_such_source/secret/api_key",
    "/api/sync/sources/no_such_source/enabled",
]


class TestPluginLookupVersusRequestValidation:
    """The URL is answered before the body, on the three routes carrying both.

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
        mock_components["storage"].get_source_config.return_value = None

        response = client.put(url, json={})

        assert response.status_code == 404
        assert response.json()["detail"] == "Source not found."

    @pytest.mark.parametrize("url", _PLUGIN_ROUTES_WITH_A_BODY)
    def test_a_valid_body_on_an_unresolvable_source_still_404s(
        self, client, mock_components, url
    ) -> None:
        """The lookup keeps its own answer once the body parses — unchanged."""
        mock_components["storage"].get_source_config.return_value = None
        bodies = {
            "config": {"values": {}},
            "api_key": {"value": "secret"},
            "enabled": {"enabled": True},
        }

        response = client.put(url, json=bodies[url.rsplit("/", 1)[-1]])

        assert response.status_code == 404


class TestGuardsResolveOncePerRequest:
    """One request, one trip through the guard for each component it needs.

    Guard-mediated acquisition only. A handler is free to read the unguarded
    ``src.web.state`` accessors as well — ``TestUnguardedReadsAreOptional``
    covers the four that do — and those reads are imported into
    ``src.web.api``'s own namespace, so nothing here sees or counts them.
    """

    def test_a_handler_and_its_dependency_share_one_lookup(
        self, client, mock_components
    ) -> None:
        """``/sync/sources/{id}/config`` needs storage twice over and asks once.

        The handler needs storage itself and reaches the plugin through
        ``require_plugin``, which needs storage too. Called from the handler
        body those were two acquisitions of the same component inside one
        request; declared as dependencies, FastAPI caches the first for the
        life of the request.
        """
        mock_components["storage"].get_source_config.return_value = {
            "source_id": "my_books",
            "plugin": "goodreads_csv",
            "enabled": 1,
            "config": {},
            "migrated_at": "2026-01-01T00:00:00",
        }

        with patch("src.web.guards.get_storage", wraps=get_storage) as acquisitions:
            response = client.get("/api/sync/sources/my_books/config")

        assert response.status_code == 200
        assert acquisitions.call_count == 1

    @pytest.mark.parametrize("endpoint", _GUARDED_ENDPOINTS, ids=_endpoint_id)
    def test_no_endpoint_resolves_a_guard_more_than_once(
        self, mock_components, endpoint
    ) -> None:
        """The cache holds on every route, not just the one measured above.

        The six routes reaching ``require_plugin`` are the known duplicates,
        but a handler that grew a second acquisition of anything would be a
        second read of state that can change between them. Counting zero for
        the unlisted components is the same claim from the other side: a guard
        nobody classified would show up here as a read.

        ``raise_server_exceptions=False`` because the guards all resolve before
        the body runs, so what a stub-thin mock does once it gets there decides
        the status code and nothing about the count. The sync manager is
        stubbed for the same reason from the other direction: every component
        is wired here, so ``POST /api/update`` would otherwise reach
        ``start_sync`` and spawn a real background sync over whatever files the
        fixture config names.
        """
        mock_components["storage"].get_source_config.return_value = {
            "source_id": "my_books",
            "plugin": "goodreads_csv",
            "enabled": 1,
            "config": {},
            "migrated_at": "2026-01-01T00:00:00",
        }
        client = authenticated_client(
            mock_components["app"], raise_server_exceptions=False
        )

        with (
            patch("src.web.api.get_sync_manager"),
            patch("src.web.guards.get_storage", wraps=get_storage) as storage_reads,
            patch("src.web.guards.get_config", wraps=get_config) as config_reads,
            patch("src.web.guards.get_engine", wraps=get_engine) as engine_reads,
            patch(
                "src.web.guards.get_memory_manager", wraps=get_memory_manager
            ) as memory_reads,
            patch(
                "src.web.guards.get_conversation_engine",
                wraps=get_conversation_engine,
            ) as chat_reads,
        ):
            client.request(endpoint.method, endpoint.target, json=endpoint.body)

        assert {
            "storage": storage_reads.call_count,
            "config": config_reads.call_count,
            "engine": engine_reads.call_count,
            "memory_manager": memory_reads.call_count,
            "conversation_engine": chat_reads.call_count,
        } == {
            component: 1 if component in endpoint.requires else 0
            for component in _UNAVAILABLE_DETAIL
        }


class TestHandlersRunOffTheEventLoop:
    """Every ``/api`` handler is plain ``def``, with no exceptions.

    FastAPI runs an ``async def`` endpoint on the event loop and a plain ``def``
    one in Starlette's threadpool. Every handler here does blocking work —
    SQLite, the scoring pipeline, outbound OAuth calls — and none of them
    awaits anything, so declaring one ``async`` stalls every other request on
    the server for its whole duration. The settings writers were the last two
    holding the loop, and they hold ``writable_config`` instead now.
    """

    def test_no_api_handler_is_a_coroutine(self, mock_components) -> None:
        """A new ``async def`` handler is caught here rather than in production."""
        coroutines = {
            route.endpoint.__name__
            for route in mock_components["app"].routes
            if isinstance(route, APIRoute)
            and route.path.startswith("/api")
            and inspect.iscoroutinefunction(route.endpoint)
        }

        assert coroutines == set()


# Bounded so a handler nothing releases fails the test instead of hanging the
# suite; nothing waits this long on the passing path.
_STALL_TIMEOUT_SECONDS = 5.0

# How long a caller that must not get through is given to prove it. Only spent
# on the passing path, and only by the tests asserting that something blocks.
_BLOCKED_GRACE_SECONDS = 0.5


class TestSlowRequestsDoNotStallTheServerRegression:
    """A request in flight must not hold the loop against every other one.

    Bug reported: while a recommendation generation was running, the Data
    page's two-second sync poll and the chat SSE stream both froze, so sync
    progress appeared stuck.
    Root cause: ``get_recommendations`` was ``async def`` with no ``await`` in
    its body, so FastAPI ran the whole scoring pass — plus, by default, a
    fan-out of synchronous Ollama calls — directly on the event loop.
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


def _asgi_request(
    app,
    method: str,
    target: str,
    on_body: Callable[[bytes], None],
    request_body: bytes = b"",
) -> int:
    """Drive *app* over raw ASGI, calling *on_body* per response body chunk.

    ``TestClient`` accumulates the whole response into a ``BytesIO`` before it
    hands anything back, so even ``client.stream`` cannot see whether an
    endpoint trickled or buffered. The ASGI boundary is the last place that is
    observable, which is why this drives the app rather than the client.
    """
    path, _, query = target.partition("?")
    session = issue_session(app_state.storage)
    headers = [
        (b"host", b"testserver"),
        (b"cookie", f"{SESSION_COOKIE}={session}".encode()),
    ]
    if request_body:
        headers += [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(request_body)).encode()),
        ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "http",
        "query_string": query.encode(),
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def drive() -> int:
        status = 0
        # The middleware chain asks again once it has the body, and answering
        # "disconnected" straight away makes it abandon the response.
        finished = anyio.Event()
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                await finished.wait()
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    on_body(message["body"])
                if not message.get("more_body", False):
                    finished.set()

        await app(scope, receive, send)
        return status

    with anyio.from_thread.start_blocking_portal() as portal:
        return portal.call(drive)


class TestStreamingStaysIncremental:
    """A ``def`` handler returning a ``StreamingResponse`` must still trickle.

    The handler moved to a threadpool worker, and the SSE generator it returns
    is a plain iterator Starlette drives in one of its own. Materialise that
    generator at either end and the two-phase protocol collapses: every event
    arrives at once, after the slowest LLM call, which is the stall this whole
    change is about rather than a cosmetic difference.
    """

    def test_phase_one_is_sent_before_a_blurb_has_come_back(
        self, mock_components
    ) -> None:
        """The recommendations event leaves the app while a blurb is blocked."""
        release_blurb = threading.Event()
        blurb_returned = threading.Event()
        blurb_pending_at_first_chunk: list[bool] = []
        chunks: list[bytes] = []

        def slow_blurb(*_args, **_kwargs):
            release_blurb.wait(timeout=_STALL_TIMEOUT_SECONDS)
            blurb_returned.set()
            return "a blurb"

        def record(body: bytes) -> None:
            if not chunks:
                blurb_pending_at_first_chunk.append(not blurb_returned.is_set())
                release_blurb.set()
            chunks.append(body)

        engine = mock_components["engine"]
        engine.generate_recommendations.return_value = [
            Recommendation(
                item=ContentItem(
                    id="1",
                    title="Dune",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
                score=0.9,
                reasoning="because",
            )
        ]
        engine.generate_blurb_for_item.side_effect = slow_blurb
        engine.storage.get_signal_items.return_value = []
        mock_components["storage"].get_user_preference_config.return_value = None

        status = _asgi_request(
            mock_components["app"],
            "GET",
            "/api/recommendations/stream?type=book&count=1",
            record,
        )

        assert status == 200
        assert blurb_pending_at_first_chunk == [True]
        # One ASGI body message per ``yield``. Buffered, there would be one for
        # the lot, which is the other half of what "still streams" means.
        assert [
            json.loads(chunk.removeprefix(b"data: "))["type"] for chunk in chunks
        ] == [
            "recommendations",
            "blurb",
            "done",
        ]

    def test_chat_sends_its_first_event_before_the_next_one_exists(
        self, mock_components
    ) -> None:
        """The other stream the stall report named, on the same conversion.

        ``chat`` became plain ``def`` returning the same synchronous-generator
        ``StreamingResponse``, and the reported freeze was of the chat stream
        as much as the sync poll — so proving only ``/recommendations/stream``
        trickles leaves the reported symptom itself unpinned.
        """
        release_second = threading.Event()
        second_returned = threading.Event()
        second_pending_at_first_chunk: list[bool] = []
        chunks: list[bytes] = []

        def two_chunks(**_kwargs) -> Iterator[ConversationChunk]:
            yield ConversationChunk(chunk_type="text", content="first")
            release_second.wait(timeout=_STALL_TIMEOUT_SECONDS)
            second_returned.set()
            yield ConversationChunk(chunk_type="done")

        def record(body: bytes) -> None:
            if not chunks:
                second_pending_at_first_chunk.append(not second_returned.is_set())
                release_second.set()
            chunks.append(body)

        engine = Mock(spec=ConversationEngine)
        engine.process_message.side_effect = two_chunks
        app_state.conversation_engine = engine

        status = _asgi_request(
            mock_components["app"],
            "POST",
            "/api/chat",
            record,
            request_body=json.dumps({"message": "hi"}).encode(),
        )

        assert status == 200
        assert second_pending_at_first_chunk == [True]
        assert [
            json.loads(chunk.removeprefix(b"data: "))["type"] for chunk in chunks
        ] == ["text", "done"]


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

        assert storage.get_setting(_SETTINGS_INT_KEY) == 11
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

        assert storage.get_setting(_SETTINGS_INT_KEY) == 11


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


_API_TREE = ast.parse(Path(src.web.api.__file__).read_text(encoding="utf-8"))

_LOG_METHODS = {"debug", "info", "warning", "error", "critical"}

# The two calls that render a value fit to be interpolated into a log line.
_LOG_SANITIZERS = {"sanitize_for_log", "exception_for_log"}

# The arguments that are not text, each with the reason it cannot forge a line.
# Anything else must go through a sanitizer.
_NON_TEXT_LOG_ARGUMENTS = {
    "idx": "enumerate counter, rendered with %d",
    "user_id": "int query parameter",
}


def _log_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``logger.<level>(...)`` call in a parsed module."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    ]


def _unsanitized_log_arguments(tree: ast.AST) -> set[str]:
    return {
        f"{ast.unparse(argument)} (line {argument.lineno})"
        for call in _log_calls(tree)
        for argument in call.args[1:]
        if ast.unparse(argument) not in _NON_TEXT_LOG_ARGUMENTS
        and not (
            isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.func.id in _LOG_SANITIZERS
        )
    }


def _sanitizers_reached_by_the_sweep(tree: ast.AST) -> set[str]:
    """The sanitizer names the sweep finds interpolated into a log line.

    Empty is the shape every ``== set()`` assertion below also has, so this is
    what tells a swept module from an unswept one.
    """
    return {
        argument.func.id
        for call in _log_calls(tree)
        for argument in call.args[1:]
        if isinstance(argument, ast.Call)
        and isinstance(argument.func, ast.Name)
        and argument.func.id in _LOG_SANITIZERS
    }


def _stringified_exception_log_arguments(tree: ast.AST) -> set[str]:
    """Sanitized log arguments stringified before they got there.

    Every one so far sat in a catch-all ``except Exception``, where the class
    is the diagnostic: a bare ``TimeoutError()`` renders as nothing at all.
    ``exception_for_log`` is the spelling that keeps the name.
    """
    return {
        f"{ast.unparse(argument)} (line {argument.lineno})"
        for call in _log_calls(tree)
        for argument in call.args[1:]
        if isinstance(argument, ast.Call)
        and isinstance(argument.func, ast.Name)
        and argument.func.id == "sanitize_for_log"
        and argument.args
        and renders_a_value_as_text(argument.args[0])
    }


def _non_literal_log_messages(tree: ast.AST) -> set[str]:
    return {
        f"{ast.unparse(call)} (line {call.lineno})"
        for call in _log_calls(tree)
        if not call.args or not isinstance(call.args[0], ast.Constant)
    }


def _traceback_log_calls(tree: ast.AST) -> set[str]:
    return {
        f"{call.func.attr} (line {call.lineno})"
        for call in _log_calls(tree)
        if isinstance(call.func, ast.Attribute)
        and (
            call.func.attr not in _LOG_METHODS
            or any(keyword.arg == "exc_info" for keyword in call.keywords)
        )
    }


def _hand_rolled_break_escapes(tree: ast.AST) -> set[str]:
    return {
        f"{ast.unparse(node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and set(str(node.args[0].value)) & set(LINE_BREAKS + "\0")
    }


def _sanitizer_calls_outside_a_log_call(tree: ast.AST) -> set[str]:
    """Sanitizer calls that are not an argument to a ``logger`` call.

    Escapes cut for a single-line file reach an API consumer as the literal
    backslashes they are. Only a sanitizer's own body composes one elsewhere.
    """
    logged = {id(argument) for call in _log_calls(tree) for argument in call.args[1:]}
    composed = {
        id(node)
        for definition in ast.walk(tree)
        if isinstance(definition, ast.FunctionDef)
        and definition.name in _LOG_SANITIZERS
        for node in ast.walk(definition)
    }
    return {
        f"{ast.unparse(node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _LOG_SANITIZERS
        and id(node) not in logged | composed
    }


def _log_sinks_the_sweep_cannot_see(tree: ast.AST) -> set[str]:
    """Writes that reach a log or console without going through ``logger``.

    The four sweeps above key on the name ``logger``; anything that emits
    under another name is invisible to them, so it is banned outright.
    """
    return {
        f"{ast.unparse(node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "print")
            or (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logging"
                and node.func.attr in _LOG_METHODS | {"exception", "log"}
            )
        )
    }


def _is_logger_expression(value: ast.expr) -> bool:
    """A ``getLogger`` result, the module logger, or a method bound off it.

    ``from logging import getLogger`` spells the call as a bare name, and
    ``warn = logger.warning`` needs no call at all.
    """
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Attribute):
            return value.func.attr == "getLogger"
        return isinstance(value.func, ast.Name) and value.func.id == "getLogger"
    root = value.value if isinstance(value, ast.Attribute) else value
    return isinstance(root, ast.Name) and root.id == "logger"


def _logger_binding_names(tree: ast.AST) -> set[str]:
    """The names a logger, or anything reached through one, is bound to."""
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _is_logger_expression(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


class TestNoApiLogCallCanBeForgedRegression:
    """Reported three times: a log sink here escapes nothing.

    Bug: each round fixed the sinks it was shown and left their siblings.
    Fix: sweep the syntax tree, so a new raw sink fails here rather than in
    review.
    """

    def test_every_interpolated_value_is_sanitized(self) -> None:
        """A log argument is a sanitizer call or a listed non-text value."""
        assert _unsanitized_log_arguments(_API_TREE) == set()

    def test_no_argument_stringifies_an_exception_by_hand(self) -> None:
        """The spelling that drops the class name off a catch-all handler."""
        assert _stringified_exception_log_arguments(_API_TREE) == set()

    def test_every_log_message_is_a_literal(self) -> None:
        """An f-string message would carry its values past the check above."""
        assert _non_literal_log_messages(_API_TREE) == set()

    def test_no_log_call_attaches_a_traceback(self) -> None:
        """A traceback writes absolute source paths whatever the message says."""
        assert _traceback_log_calls(_API_TREE) == set()

    def test_no_line_break_is_escaped_by_hand(self) -> None:
        """A local copy of the escape rule is how the two definitions drifted."""
        assert _hand_rolled_break_escapes(_API_TREE) == set()

    def test_no_sink_emits_under_another_name(self) -> None:
        """``logging.error`` and ``print`` write the same file, unswept."""
        assert _log_sinks_the_sweep_cannot_see(_API_TREE) == set()

    def test_the_only_logger_binding_is_the_one_swept(self) -> None:
        """A second logger under another name would be swept by nothing."""
        assert _logger_binding_names(_API_TREE) == {"logger"}

    def test_the_sweep_still_reaches_the_calls_it_exists_for(self) -> None:
        """Six of the assertions above hold over an empty population.

        A binding outlives its call sites unflagged, so the test above is not
        this anchor: logging moved into a helper empties the sweep.
        """
        assert _sanitizers_reached_by_the_sweep(_API_TREE) == _LOG_SANITIZERS


class TestTheApiLogSweepFailsOnANewRawSink:
    """The sweep above passes; these prove it is not passing vacuously.

    Each feeds the offending source to the predicate api.py's test calls, and
    asserts the whole report: ``!= set()`` holds for one reporting the wrong
    node.
    """

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("logger.info('title=%s', item.title)", "item.title"),
            ("logger.warning('%s', str(error))", "str(error)"),
            ("logger.error('%s %s', sanitize_for_log(a), b)", "b"),
            ("logger.info('%s', f'{title}')", "f'{title}'"),
            ("logger.info('%s', 'a' + title)", "'a' + title"),
            ("logger.info('%s', *values)", "*values"),
        ],
    )
    def test_an_unsanitized_argument_is_reported(
        self, source: str, reported: str
    ) -> None:
        assert _unsanitized_log_arguments(ast.parse(source)) == {f"{reported} (line 1)"}

    @pytest.mark.parametrize(
        "argument",
        [
            "sanitize_for_log(str(error))",
            "sanitize_for_log(f'{error}')",
            "sanitize_for_log('%s' % error)",
            "sanitize_for_log('{}'.format(error))",
        ],
    )
    def test_a_stringified_exception_is_reported(self, argument: str) -> None:
        """All four interpolations, each dropping the class name identically."""
        tree = ast.parse(f"logger.warning('%d: %s', idx, {argument})")

        assert _stringified_exception_log_arguments(tree) == {f"{argument} (line 1)"}

    def test_the_report_names_the_line_the_argument_is_on(self) -> None:
        """Every case above sits on line 1, which a hardcoded 1 also satisfies."""
        tree = ast.parse(
            "x = 1\ny = 2\nlogger.warning('%s', sanitize_for_log(f'{error}'))"
        )

        assert _stringified_exception_log_arguments(tree) == {
            "sanitize_for_log(f'{error}') (line 3)"
        }

    def test_both_stringified_arguments_of_one_call_are_reported(self) -> None:
        """A first-match predicate would leave the second spelling in place."""
        tree = ast.parse(
            "logger.warning('%s %s', sanitize_for_log(str(a)), sanitize_for_log(f'{b}'))"
        )

        assert _stringified_exception_log_arguments(tree) == {
            "sanitize_for_log(str(a)) (line 1)",
            "sanitize_for_log(f'{b}') (line 1)",
        }

    def test_the_body_of_a_sanitizer_is_not_reported(self) -> None:
        """``exception_for_log``'s own body is a sanctioned f-string.

        Broadening the predicate to f-strings put the one legitimate site in
        range; it stays out only because it is not a ``logger`` argument.
        """
        tree = ast.parse(
            "def exception_for_log(error):\n"
            "    return sanitize_for_log(f'{type(error).__name__}: {error}')"
        )

        assert _stringified_exception_log_arguments(tree) == set()
        assert _sanitizer_calls_outside_a_log_call(tree) == set()

    @pytest.mark.parametrize(
        "source",
        [
            "logger.info(f'title={title}')",
            "logger.info('title=%s' % title)",
            "logger.info()",
        ],
    )
    def test_a_message_that_is_not_a_literal_is_reported(self, source: str) -> None:
        assert _non_literal_log_messages(ast.parse(source)) == {f"{source} (line 1)"}

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("logger.error('boom', exc_info=True)", "error"),
            ("logger.error('boom', exc_info=error)", "error"),
            ("logger.exception('boom')", "exception"),
        ],
    )
    def test_a_traceback_is_reported(self, source: str, reported: str) -> None:
        assert _traceback_log_calls(ast.parse(source)) == {f"{reported} (line 1)"}

    @pytest.mark.parametrize("breaker", [*LINE_BREAKS, "\0"])
    def test_a_hand_rolled_escape_is_reported(self, breaker: str) -> None:
        escape = f"title.replace({breaker!r}, ' ')"

        assert _hand_rolled_break_escapes(ast.parse(f"safe = {escape}")) == {
            f"{escape} (line 1)"
        }

    @pytest.mark.parametrize(
        "source",
        [
            "logging.error('title=%s', title)",
            "logging.exception('boom')",
            "print(title)",
        ],
    )
    def test_a_sink_under_another_name_is_reported(self, source: str) -> None:
        assert _log_sinks_the_sweep_cannot_see(ast.parse(source)) == {
            f"{source} (line 1)"
        }

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            (
                "safe_title = sanitize_for_log(item.title)",
                "sanitize_for_log(item.title)",
            ),
            (
                "body = {'title': sanitize_for_log(item.title)}",
                "sanitize_for_log(item.title)",
            ),
            ("detail = exception_for_log(error)", "exception_for_log(error)"),
        ],
    )
    def test_a_sanitizer_outside_a_log_call_is_reported(
        self, source: str, reported: str
    ) -> None:
        assert _sanitizer_calls_outside_a_log_call(ast.parse(source)) == {
            f"{reported} (line 1)"
        }

    def test_a_sanitizer_composing_another_is_not_reported(self) -> None:
        """``exception_for_log`` is built from ``sanitize_for_log``."""
        source = (
            "def exception_for_log(error):\n    return sanitize_for_log(str(error))"
        )

        assert _sanitizer_calls_outside_a_log_call(ast.parse(source)) == set()

    @pytest.mark.parametrize(
        ("source", "bound"),
        [
            ("audit = logging.getLogger('audit')", "audit"),
            ("audit = getLogger('audit')", "audit"),
            ("_LOG = logger", "_LOG"),
            ("warn = logger.warning", "warn"),
        ],
    )
    def test_a_second_logger_binding_is_reported(self, source: str, bound: str) -> None:
        """The alias is named, and the module's own binding is still found.

        A bare ``!= {'logger'}`` on the offending line alone passes on a
        predicate that recognises nothing, because the empty set is not
        ``{'logger'}`` either.
        """
        tree = ast.parse(f"logger = logging.getLogger(__name__)\n{source}")

        assert _logger_binding_names(tree) == {"logger", bound}

    def test_the_clean_shape_is_not_reported(self) -> None:
        """The predicates accept what api.py actually writes."""
        tree = ast.parse(
            "logger.warning('e=%s: %s', sanitize_for_log(a), exception_for_log(b))"
        )

        assert _unsanitized_log_arguments(tree) == set()
        assert _stringified_exception_log_arguments(tree) == set()
        assert _non_literal_log_messages(tree) == set()
        assert _traceback_log_calls(tree) == set()
        assert _sanitizer_calls_outside_a_log_call(tree) == set()
        assert _sanitizers_reached_by_the_sweep(tree) == _LOG_SANITIZERS

    def test_a_module_that_logs_nothing_reaches_no_sanitizer(self) -> None:
        """The state the anchor asserts against: api.py with its sinks moved."""
        assert (
            _sanitizers_reached_by_the_sweep(ast.parse("x = helper(payload)")) == set()
        )

    def test_a_sink_under_a_bound_name_reaches_no_sanitizer(self) -> None:
        """``self.logger`` is the rebinding ``_logger_binding_names`` misses."""
        tree = ast.parse("self.logger.info('%s', sanitize_for_log(title))")

        assert _sanitizers_reached_by_the_sweep(tree) == set()

    def test_the_anchor_is_the_only_assertion_an_emptied_module_moves(self) -> None:
        """api.py with every sink behind a helper, put to all eight predicates.

        The anchor's docstring claims the other seven survive that; asserting
        the claim is what stops the anchor being dropped as redundant.
        """
        tree = ast.parse("logger = logging.getLogger(__name__)\nlog_it(payload)")

        assert _unsanitized_log_arguments(tree) == set()
        assert _stringified_exception_log_arguments(tree) == set()
        assert _non_literal_log_messages(tree) == set()
        assert _traceback_log_calls(tree) == set()
        assert _hand_rolled_break_escapes(tree) == set()
        assert _log_sinks_the_sweep_cannot_see(tree) == set()
        assert _logger_binding_names(tree) == {"logger"}
        assert _sanitizers_reached_by_the_sweep(tree) != _LOG_SANITIZERS

    def test_the_anchor_moves_when_one_sanitizer_stops_being_reached(self) -> None:
        """Half the sinks moved is a half-empty sweep, and a silent one.

        Equality with the whole set, rather than a non-empty check: the
        exception sinks can go while the plain ones stay.
        """
        tree = ast.parse("logger.error('e=%s', sanitize_for_log(title))")

        assert _unsanitized_log_arguments(tree) == set()
        assert _LOG_SANITIZERS - _sanitizers_reached_by_the_sweep(tree) == {
            "exception_for_log"
        }


def _api_log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Only the api.py records: booting the app logs from four other modules."""
    return [
        record.getMessage() for record in caplog.records if record.name == "src.web.api"
    ]


class TestExoticBreaksCannotForgeAnApiLogLine:
    """The syntax sweep proves the shape; these run the sinks.

    ``\\u2028`` is the case a reviewer misses and ``str.splitlines`` does not,
    so each sink is driven with every break the shared constant names.
    """

    @pytest.mark.parametrize("breaker", LINE_BREAKS)
    def test_a_stream_failure_stays_on_one_line(
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
                "/api/recommendations/stream?type=video_game&count=5"
            )

        messages = _api_log_messages(caplog)
        assert len(messages) == 1
        assert len(messages[0].splitlines()) == 1
        assert breaker not in messages[0]
        assert "ValueError" in messages[0]

    @pytest.mark.parametrize("breaker", LINE_BREAKS)
    def test_a_theme_directory_name_stays_on_one_line(
        self, breaker: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        theme_dir = tmp_path / f"solar{breaker}WARNING forged"
        theme_dir.mkdir()
        (theme_dir / "theme.json").write_text("{not json", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="src.web.api"):
            assert src.web.api.discover_themes(tmp_path) == []

        messages = _api_log_messages(caplog)
        assert len(messages) == 1
        assert len(messages[0].splitlines()) == 1
        assert breaker not in messages[0]


class TestATerminalControlCannotRewriteAnApiLogLine:
    """The console handler writes to what ``docker logs`` renders.

    ``ESC[2K\\r`` erases the line an operator just read, which is the CWE-117
    outcome without a line break anywhere in the value.
    """

    @pytest.mark.parametrize("control", ["\x1b", "\x08", "\x7f", "\t"])
    def test_a_control_character_never_reaches_the_message(
        self, control: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.side_effect = ValueError(
            f"no candidate for Real Title{control}[2KERROR forged"
        )
        storage = MagicMock(spec=StorageManager)
        storage.get_user_preference_config.return_value = None

        with (
            booted_web_app(storage, {}) as app,
            caplog.at_level(logging.ERROR, logger="src.web.api"),
        ):
            app_state.engine = engine
            authenticated_client(app).get(
                "/api/recommendations/stream?type=video_game&count=5"
            )

        messages = _api_log_messages(caplog)
        assert len(messages) == 1
        assert control not in messages[0]
        assert f"\\u{ord(control):04x}" in messages[0]


class TestACatchAllHandlerStillNamesItsExceptionClassRegression:
    """Reported: two spellings of exception logging, and one drops the class.

    Bug: ``sanitize_for_log(str(exc))`` in a catch-all ``except Exception``
    logs a trailing colon and nothing else for a bare ``TimeoutError()``.
    Fix: every one of the three goes through ``exception_for_log``.
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

    def test_the_blurb_sink_names_it(
        self,
        client: TestClient,
        mock_components: dict,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        item = ContentItem(
            id="1",
            db_id=1,
            title="Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        mock_components["engine"].generate_recommendations.return_value = [
            _rec_record(item)
        ]
        mock_components["engine"].generate_blurb_for_item.side_effect = TimeoutError()
        mock_components["storage"].get_user_preference_config.return_value = None

        with (
            caplog.at_level(logging.WARNING, logger="src.web.api"),
            client.stream(
                "GET", "/api/recommendations/stream?type=book&count=1"
            ) as response,
        ):
            response.read()

        # The inner sink, not the outer one wrapping the whole generator.
        assert (
            "Streaming blurb failed for index 0: TimeoutError: "
            in _api_log_messages(caplog)
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

    def test_no_sanitizer_call_sits_outside_a_log_call(self) -> None:
        """The sibling endpoints too: an escaped value only ever gets logged."""
        assert _sanitizer_calls_outside_a_log_call(_API_TREE) == set()

    def test_the_source_service_escapes_only_into_a_log_call(self) -> None:
        """``_log_refusal`` is its one sanitizer call site: those escapes are cut
        for a log file and reach a client as literal backslashes. A lower bound,
        so rendering a fault through ``exception_for_log`` stays legal.
        """
        tree = ast.parse(Path(src.sources.service.__file__).read_text(encoding="utf-8"))

        assert {"sanitize_for_log"} <= _sanitizers_reached_by_the_sweep(tree)
        assert _sanitizer_calls_outside_a_log_call(tree) == set()

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


_CHAT_TREE = ast.parse(Path(src.web.chat_api.__file__).read_text(encoding="utf-8"))


def _resolved_response_class(route: APIRoute) -> type:
    """Unwrap the placeholder a route keeps when nothing overrode the default."""
    declared = route.response_class
    return declared.value if isinstance(declared, DefaultPlaceholder) else declared


def _dumps_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps"
    ]


def _chunk_dumps_calls(tree: ast.AST) -> list[ast.Call]:
    """The ``dumps`` calls building an SSE chunk, which Starlette then encodes
    strictly. A ``dumps`` elsewhere in the module answers at a different
    boundary, so counting it would report coverage the streams do not have.
    """
    builders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "generate_sse"
    ]
    return [call for builder in builders for call in _dumps_calls(builder)]


def _dumps_naming_ensure_ascii(calls: list[ast.Call]) -> set[str]:
    return {
        f"line {call.lineno}"
        for call in calls
        if any(keyword.arg == "ensure_ascii" for keyword in call.keywords)
    }


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

    def test_the_recommendation_stream_answers(
        self, surrogate, mock_components
    ) -> None:
        """SSE encodes its own chunks, so the response class never sees them.

        ``json.dumps`` defaults to ``ensure_ascii=True`` there, writing the
        escape before the encode — why this path never had the defect.
        """
        mock_components["engine"].generate_recommendations.return_value = [
            _rec_record(_item_holding(surrogate))
        ]
        mock_components["engine"].generate_blurb_for_item.return_value = None
        mock_components["storage"].get_user_preference_config.return_value = None
        mock_components["storage"].get_signal_items.return_value = []
        client = authenticated_client(
            mock_components["app"], raise_server_exceptions=False
        )

        with client.stream(
            "GET", "/api/recommendations/stream?type=book&count=1"
        ) as response:
            assert response.status_code == 200
            body = response.read().decode()

        events = _parse_sse_events(body)
        streamed = [e for e in events if e["type"] == "recommendations"][0]["items"]
        assert streamed[0]["title"] == f"Dune{surrogate}"


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


class TestTheEncodeIsOneBoundary:
    """Eleven call sites was the shape the per-endpoint repair would have had."""

    def test_every_route_renders_through_the_app_response_class(
        self, mock_components
    ) -> None:
        """``/`` is the one exemption: its body is a constant or a strict
        UTF-8 file decode, so no string UTF-8 refuses can reach it.
        """
        rendering = {
            route.path: _resolved_response_class(route)
            for route in mock_components["app"].routes
            if isinstance(route, APIRoute)
        }

        assert {
            path: cls
            for path, cls in rendering.items()
            if cls is not SurrogateSafeJSONResponse
        } == {"/": HTMLResponse}

    def test_every_handler_an_http_request_can_reach_is_the_app_encode(
        self, mock_components
    ) -> None:
        """FastAPI supplies a handler per exception class, and each of its own
        renders on a stock ``JSONResponse`` the default class never sees.
        """
        registered = mock_components["app"].exception_handlers

        assert {
            raised: handler
            for raised, handler in registered.items()
            if raised is not WebSocketRequestValidationError
        } == {
            StarletteHTTPException: _raised_refusal_json_can_carry,
            RequestValidationError: _validation_refusal_json_can_carry,
        }

    def test_the_handler_left_to_fastapi_answers_a_protocol_never_served(
        self, mock_components
    ) -> None:
        """The exemption above, anchored: nothing routed can raise it."""
        app = mock_components["app"]
        routed = {type(route) for route in app.routes}

        assert WebSocketRequestValidationError in app.exception_handlers
        assert routed and not any(
            issubclass(kind, WebSocketRoute) for kind in routed
        ), routed

    def test_the_routes_that_sweep_skips_are_the_ones_named_as_exempt(
        self, mock_config, tmp_path
    ) -> None:
        """Debug opens four routes FastAPI owns. ``/openapi.json`` renders on a
        stock ``JSONResponse`` — safe only because the schema is built from
        source text, and unreviewable if a fifth arrives unnoticed.
        """
        reset_sync_manager()
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {**mock_config["web"], "debug": True}}

        with booted_web_app(storage, config) as app:
            unswept = {
                route.path for route in app.routes if not isinstance(route, APIRoute)
            }
        reset_sync_manager()

        assert unswept == {
            "/static",
            "/openapi.json",
            "/docs",
            "/docs/oauth2-redirect",
            "/redoc",
        }

    def test_the_endpoints_the_defect_was_found_on_are_in_that_sweep(
        self, mock_components
    ) -> None:
        """A sweep over no routes would report a boundary nobody stands at."""
        paths = {
            route.path
            for route in mock_components["app"].routes
            if isinstance(route, APIRoute)
        }

        assert {
            "/api/items",
            "/api/items/{db_id}",
            "/api/items/{db_id}/ignore",
            "/api/users/{user_id}/preferences",
        } <= paths

    def test_no_stream_chunk_turns_ensure_ascii_off(self) -> None:
        """Both SSE endpoints encode their own chunks, and the default escape
        is the whole reason neither ever had the defect. ``export.py`` may say
        it: that body goes back through the raw response class.
        """
        reported = _dumps_naming_ensure_ascii(
            _chunk_dumps_calls(_API_TREE) + _chunk_dumps_calls(_CHAT_TREE)
        )

        assert reported == set()

    def test_that_sweep_reaches_the_chunks_it_exists_for(self) -> None:
        """Either builder moving elsewhere would empty the sweep silently."""
        assert _chunk_dumps_calls(_API_TREE) and _chunk_dumps_calls(_CHAT_TREE)


class TestOnlyTheAppResponseClassMakesTheBodyEncodable:
    """The two encodes side by side: every endpoint test answers through the
    booted app, so all of them would still pass if something below the
    response class were carrying the body.
    """

    @pytest.mark.parametrize("surrogate", _LONE_SURROGATES)
    def test_the_stock_json_encode_refuses_the_body_the_app_class_carries(
        self, surrogate: str
    ) -> None:
        content = {"title": f"Dune{surrogate}"}

        with pytest.raises(UnicodeEncodeError):
            JSONResponse(content)

        assert json.loads(SurrogateSafeJSONResponse(content).body) == content

    @pytest.mark.parametrize("surrogate", _LONE_SURROGATES)
    def test_the_stock_raw_encode_refuses_the_export_body_ours_carries(
        self, surrogate: str
    ) -> None:
        body = f'[{{"title": "Dune{surrogate}"}}]'

        with pytest.raises(UnicodeEncodeError):
            Response(content=body, media_type="application/json")

        rendered = SurrogateSafeResponse(content=body, media_type="application/json")
        assert json.loads(rendered.body)[0]["title"] == f"Dune{surrogate}"

    def test_two_adjacent_surrogates_come_back_as_the_pair_they_escape_to(
        self,
    ) -> None:
        """The one hole in reading back what is stored: JSON recombines an
        escaped surrogate pair, so this row arrives as U+10000. Documented
        rather than fixed — it answers 200, and no door can write one.
        """
        stored = "\ud800" + "\udc00"

        rendered = SurrogateSafeJSONResponse({"title": stored})

        assert json.loads(rendered.body) == {"title": "\U00010000"}

    def test_a_body_that_is_not_text_takes_the_stock_render(self) -> None:
        """The export hands over ``str``; every 204 hands over nothing."""
        assert SurrogateSafeResponse(content=b"\xff\xfe").body == b"\xff\xfe"
        assert SurrogateSafeResponse(status_code=204).body == b""

    def test_a_non_finite_number_is_still_refused_rather_than_rendered(self) -> None:
        """``allow_nan`` stays off, and no read path needs it loosened: a
        stored weight is dropped by ``UserPreferenceConfig`` and no response
        model carries a free float out of a blob.
        """
        with pytest.raises(ValueError):
            SurrogateSafeJSONResponse({"weight": nan})


@pytest.mark.parametrize("surrogate", _LONE_SURROGATES)
class TestTheChatStreamCarriesALoneSurrogateToo:
    """The second SSE path. Reported as never having had the defect because
    ``json.dumps`` escapes to ASCII before Starlette's strict chunk encode —
    a claim about the chunk builder, so it gets a behavioural test.
    """

    def test_a_streamed_chat_chunk_holding_one_reaches_the_client(
        self, surrogate: str, mock_components: dict
    ) -> None:
        conversation = Mock(spec=ConversationEngine)
        conversation.process_message.side_effect = lambda **_kwargs: iter(
            [
                ConversationChunk(chunk_type="text", content=f"Dune{surrogate}"),
                ConversationChunk(chunk_type="done"),
            ]
        )
        app_state.conversation_engine = conversation
        client = authenticated_client(
            mock_components["app"], raise_server_exceptions=False
        )

        response = client.post("/api/chat", json={"message": "hi"})

        assert response.status_code == 200
        streamed = _parse_sse_events(response.text)
        assert streamed[0]["content"] == f"Dune{surrogate}"


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
