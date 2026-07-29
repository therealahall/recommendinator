"""Tests for web API endpoints."""

import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.ingestion.import_service import (
    FILE_NOT_READABLE_MESSAGE,
    NO_ITEMS_WARNING,
    UNREADABLE_FILE_DETAIL,
    FileImportError,
)
from src.ingestion.sync import SyncResult
from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.settings.metadata import default_of
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.utils.series import MAX_SEASONS
from src.web.api import (
    APP_VERSION,
    IMPORT_ALREADY_RUNNING_DETAIL,
    IMPORT_JOB_LABEL_PREFIX,
    MAX_UPLOAD_BYTES,
    _item_to_response,
    _safe_temp_suffix,
)
from src.web.app import _LOG_BASE_DIR, _safe_log_path, create_app
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
from tests.factories import back_mock_settings_store
from tests.import_test_data import GOODREADS_CSV

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _reset_app_state() -> None:
    """Reset the global ``app_state`` singleton to AppState defaults.

    Tests that drive ``create_app`` share the module-level ``app_state``; this
    copies a fresh ``AppState()`` field-by-field so no state leaks between tests.
    """
    fresh = AppState()
    for f in fields(fresh):
        setattr(app_state, f.name, getattr(fresh, f.name))


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
            "sonarr": {
                "plugin": "sonarr",
                "url": "http://localhost:8989",
                "api_key": "x",
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
        patch("src.web.app.migrate_source_labels") as mock_migrate_labels,
        patch("src.web.app.migrate_source_config_plugins") as mock_migrate_plugins,
    ):
        # Setup mocks
        mock_storage_manager = Mock(spec=StorageManager)
        mock_storage_manager.get_credentials_for_source.return_value = {}
        mock_storage_manager.list_source_configs.return_value = []
        # Let the real migrate_config_settings boot hook run against an empty
        # settings store (no stub) — the DB overlay is a no-op and nothing
        # leaks across tests.
        back_mock_settings_store(mock_storage_manager)
        mock_storage.return_value = mock_storage_manager

        mock_client = Mock(spec=OllamaClient)
        mock_embedding_gen = Mock(spec=EmbeddingGenerator)
        mock_rec_gen = Mock(spec=RecommendationGenerator)
        mock_llm.return_value = (mock_client, mock_embedding_gen, mock_rec_gen)

        mock_engine_instance = Mock(spec=RecommendationEngine)
        mock_engine_instance.storage = mock_storage_manager
        mock_engine.return_value = mock_engine_instance

        # Reset app state to defaults
        _reset_app_state()

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
            "migrate_source_labels": mock_migrate_labels,
            "migrate_source_config_plugins": mock_migrate_plugins,
        }

        # Clean up sync manager after test
        reset_sync_manager()


@pytest.fixture
def client(mock_components):
    """Create test client."""
    return TestClient(mock_components["app"])


def test_create_app_runs_both_source_migrations(mock_components):
    """create_app runs both source migrations with the real storage instance.

    Proves the migrations are wired into web startup, not merely unit-tested:
    a rename must relabel stored items and DB source configs on first boot.
    """
    storage = mock_components["storage"]
    mock_components["migrate_source_labels"].assert_called_once_with(storage)
    mock_components["migrate_source_config_plugins"].assert_called_once_with(storage)


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


class TestSafeLogPath:
    """``logging.file`` containment — the control the registry pattern relies on.

    The registry pattern now rejects traversal at the Settings API, so this is
    no longer the only thing standing between an API caller and an arbitrary
    write. It stays load-bearing for the inputs the pattern never sees:
    ``config.yaml`` is unvalidated, rows persisted before the pattern gained its
    ``..`` lookahead still overlay at boot without re-validation, and a symlink
    under ``logs/`` satisfies any pattern.

    ``tests/web/test_logging_config.py`` covers this end to end through
    ``configure_logging``; these are the direct unit cases for the containment
    rule itself, including the fallback value and the warning.
    """

    def test_path_inside_logs_is_returned_resolved(self) -> None:
        assert _safe_log_path("logs/app.log") == (Path("logs") / "app.log").resolve()

    def test_nested_path_inside_logs_is_allowed(self) -> None:
        assert _safe_log_path("logs/sub/app.log") == (
            (Path("logs") / "sub" / "app.log").resolve()
        )

    @pytest.mark.parametrize(
        "escaping",
        [
            "logs/../../../tmp/pwned.log",
            "/etc/cron.d/evil.log",
            "logs/../secrets.log",
        ],
    )
    def test_path_escaping_logs_falls_back_to_the_default(self, escaping: str) -> None:
        """Anything resolving outside logs/ falls back — fail safe, never write.

        None of these can reach here from the Settings API any more: the pattern
        rejects both traversal and absolute paths. They can still arrive from a
        hand-edited config.yaml, which is unvalidated, or from a row persisted
        before the pattern gained its ``..`` lookahead.
        """
        assert _safe_log_path(escaping) == Path(default_of("logging.file")).resolve()

    def test_the_fallback_is_itself_contained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Closes the loop the test above leaves open.

        That assertion compares the result against the same expression the
        implementation uses, so if ``logging.file``'s registry default ever
        moved outside ``logs/``, the fallback would hand back an escaping path
        and the test would still pass. Containment is asserted directly here,
        without reference to the default.
        """
        monkeypatch.chdir(tmp_path)

        fallback = _safe_log_path("/etc/cron.d/evil.log")

        assert _LOG_BASE_DIR.resolve() in fallback.parents
        # And the fallback must satisfy the rule it is the fallback for, so
        # feeding it back through is a fixed point rather than a second retreat.
        assert _safe_log_path(str(fallback)) == fallback

    def test_the_fallback_survives_a_symlinked_default_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fail-safe branch must not become the escape it refuses.

        Regression: the refusal branch returned ``Path(default_of(
        "logging.file")).resolve()``, and ``resolve`` follows symlinks — so
        planting the default log file as a link out of ``logs/`` made every
        refused path resolve to the attacker's target instead. The fallback is
        now built from the ``logs/`` base, so it cannot escape by construction.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        default_name = Path(default_of("logging.file")).name
        (tmp_path / "logs" / default_name).symlink_to(outside / "pwned.log")

        fallback = _safe_log_path("logs/../../evil.log")

        assert _LOG_BASE_DIR.resolve() in fallback.parents
        assert fallback != (outside / "pwned.log").resolve()

    def test_the_logs_directory_itself_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``file: logs`` names the directory, which is never a valid log file.

        Regression: containment accepted ``resolved == base``, so the directory
        came back unchanged and the FileHandler opened on it raised
        IsADirectoryError inside ``create_app``'s try — turning a one-word
        config mistake into "Failed to initialize components".
        """
        monkeypatch.chdir(tmp_path)

        fallback = _safe_log_path("logs")

        assert fallback != _LOG_BASE_DIR.resolve()
        assert _LOG_BASE_DIR.resolve() in fallback.parents

    def test_escape_attempt_is_logged(self, caplog) -> None:
        """The rejection must be visible — a silent fallback hides a live attempt."""
        with caplog.at_level(logging.WARNING, logger="src.web.app"):
            _safe_log_path("logs/../../../tmp/pwned.log")

        assert any("outside the logs/ directory" in m for m in caplog.messages)

    def test_symlink_out_of_logs_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The third input the pattern cannot see, and the reason this is not dead code.

        ``logs/app.log`` satisfies the registry pattern completely — the name is
        clean, there is no ``..``, and it is relative. If ``logs/app.log`` is a
        symlink, the pattern still passes it and the containment check is the
        only thing standing between a network-set value and a FileHandler
        opening an arbitrary file for append.

        Containment therefore has to compare the RESOLVED path, which follows
        symlinks. A check written against the unresolved string would pass this.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (tmp_path / "logs" / "app.log").symlink_to(outside / "pwned.log")

        resolved = _safe_log_path("logs/app.log")

        assert resolved != (outside / "pwned.log").resolve()
        assert resolved == Path(default_of("logging.file")).resolve()

    def test_symlink_staying_inside_logs_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Containment is about where the path lands, not about symlinks per se.

        Pins the rule as "resolves under logs/" rather than "is not a symlink",
        so a future tightening that simply banned symlinks would fail here.
        """
        monkeypatch.chdir(tmp_path)
        logs = tmp_path / "logs"
        (logs / "real").mkdir(parents=True)
        (logs / "app.log").symlink_to(logs / "real" / "app.log")

        assert _safe_log_path("logs/app.log") == (logs / "real" / "app.log").resolve()


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
        with (
            patch("src.web.app.load_config", return_value=mock_config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            create_app()

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
        with (
            patch("src.web.app.load_config", return_value=mock_config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

        # mock_config carries no web.debug, so the bootstrap default (False)
        # applies and the docs stay closed despite the stored row.
        assert app.docs_url is None
        assert app.redoc_url is None
        # The schema too: docs_url=None alone would still serve the full route
        # inventory at /openapi.json.
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
        with (
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

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
        with (
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

        # Fails closed on both counts: no debug, and CORS pinned to the
        # restrictive default rather than whatever a malformed section produced.
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
        with (
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

        assert _cors_origins(app) == ["https://app.example.com"]
        # A concrete origin list may carry credentials.
        assert _cors_kwargs(app)["allow_credentials"] is True
        reset_sync_manager()

    def test_wildcard_origin_disables_credentials(self, mock_config, tmp_path):
        """``["*"]`` must turn credentials off — a browser-security invariant.

        Allowing credentials against a wildcard origin is exactly the
        combination browsers refuse and the one that would expose every
        authenticated response to any site. It had no coverage anywhere.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        config = {**mock_config, "web": {"allowed_origins": ["*"]}}
        with (
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

        assert _cors_origins(app) == ["*"]
        assert _cors_kwargs(app)["allow_credentials"] is False
        reset_sync_manager()

    def test_db_set_origins_reach_the_middleware(self, mock_config, tmp_path):
        """A DB-stored value applies on the next boot, as restart_required promises.

        The overlay runs before the CORS read, but nothing pinned that ordering
        — and this is the only registry leaf whose effect is a security control.
        """
        reset_sync_manager()
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        storage_manager.set_setting("web.allowed_origins", ["https://stored.example"])
        with (
            patch("src.web.app.load_config", return_value=mock_config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

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
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
            caplog.at_level(logging.WARNING, logger="src.web.app"),
        ):
            _reset_app_state()

            create_app()

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
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
            caplog.at_level(logging.WARNING, logger="src.web.app"),
        ):
            _reset_app_state()

            app = create_app()

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
        with (
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            app = create_app()

        assert _cors_origins(app) == default_of("web.allowed_origins")
        reset_sync_manager()

    def test_logging_configured_after_settings_overlay(self, mock_config, tmp_path):
        """configure_logging runs after migrate_config_settings (overlay first).

        Spies on the real hook so it still runs (no stub) while recording call
        order against configure_logging.
        """
        reset_sync_manager()
        order: list[str] = []
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")

        def _record_settings(config, storage):
            order.append("settings")
            migrate_config_settings(config, storage)

        with (
            patch("src.web.app.load_config", return_value=mock_config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch(
                "src.web.app.migrate_config_settings",
                side_effect=_record_settings,
            ),
            patch(
                "src.web.app.configure_logging",
                side_effect=lambda *a, **k: order.append("logging"),
            ),
        ):
            _reset_app_state()

            create_app()

        assert order == ["settings", "logging"]
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
        with (
            patch("src.web.app.load_config", return_value=config),
            patch(
                "src.web.app.create_storage_manager",
                return_value=storage_manager,
            ),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()

            create_app()

            # The secret was encrypted into storage on boot.
            assert (
                storage_manager.has_global_secret("enrichment.providers.tmdb.api_key")
                is True
            )
            # And stripped from the running config, so no plaintext lingers.
            providers = app_state.config["enrichment"]["providers"]
            assert providers.get("tmdb", {}).get("api_key") is None
        reset_sync_manager()


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
    # mock_config has exactly sonarr enabled
    assert len(sources) == 1
    sonarr = next((s for s in sources if s["id"] == "sonarr"), None)
    assert sonarr is not None
    assert sonarr["display_name"] == "Sonarr"
    assert sonarr["plugin_display_name"] == "Sonarr"


def test_sync_sources_lists_all_with_enabled_flag(client):
    """All configured sources are listed; ``enabled`` flag exposed per source.

    The UI renders disabled sources in a muted state instead of hiding them
    entirely, so the listing endpoint must surface them. ``resolve_inputs``
    is the gate that filters to enabled-only for sync execution.

    A leftover block naming a file-import plugin (here ``goodreads_csv``) is
    listed too, flagged ``is_file_import`` — it can never sync, and this
    listing is the only place the user can find the id to clear.
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

    # A leftover file-import block keeps the ``enabled`` flag it was stored
    # with — being unsyncable does not silently rewrite it — and is additionally
    # flagged as a file import so the UI can explain why it never runs.
    assert by_id["goodreads_csv"]["enabled"] is True
    assert by_id["goodreads_csv"]["is_file_import"] is True
    assert by_id["sonarr"]["enabled"] is True
    assert by_id["sonarr"]["is_file_import"] is False
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
            "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.fetch",
            return_value=iter([mock_item]),
        ),
        patch(
            "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
            return_value=[],
        ),
    ):
        mock_components["embedding_gen"].generate_content_embedding.return_value = [
            0.1
        ] * 768
        mock_components["storage"].save_content_item.return_value = 1

        response = client.post("/api/update", json={"source": "sonarr"})

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
            "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.fetch",
            return_value=iter([mock_book]),
        ),
        patch(
            "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
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
        assert "sonarr" in data["sources"]
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
                "Sync already in progress for Sonarr",
            )
            mock_get_sync_manager.return_value = mock_manager

            with patch(
                "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
                return_value=[],
            ):
                response = client.post("/api/update", json={"source": "sonarr"})

            assert response.status_code == 409
            detail = response.json()["detail"]
            assert "Sync already in progress" in detail
            assert "Sonarr" in detail
            assert mock_manager.start_sync.call_args.args[0] == "Sonarr"

    def test_update_allows_different_sources_concurrently(
        self, client: TestClient, mock_components: dict
    ) -> None:
        """A second source is accepted while a different source is running.

        Plants a real RUNNING job for Steam in the global SyncManager
        before triggering a Sonarr sync. The endpoint must reject
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
            "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
            return_value=[],
        ):
            # Drop the captured execute_multi_source_sync into a no-op so
            # the second sync's daemon doesn't try to actually run.
            with patch(
                "src.web.api.execute_multi_source_sync",
                return_value=[SyncJob(source="Sonarr", status=SyncStatus.RUNNING)],
            ):
                response = client.post("/api/update", json={"source": "sonarr"})

        assert response.status_code == 200, response.text
        assert "Sync started" in response.json()["message"]
        # Manager now tracks both jobs; the Steam one is still running
        # and the Sonarr one was added on top.
        assert manager.is_running("Steam") is True
        assert "Sonarr" in {job["source"] for job in manager.get_status()["jobs"]}


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
                "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
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
                "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
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
                "src.ingestion.sources.sonarr.sonarr.SonarrPlugin.validate_config",
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
            {
                "item": candidate,
                "score": 0.9,
                "similarity_score": 0.0,
                "preference_score": 0.0,
                "reasoning": "because sci-fi",
                "score_breakdown": {},
                "variety_penalty": 0.0,
                "contributing_items": [],
            }
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
        with (
            patch("src.web.app.load_config", return_value=config),
            patch("src.web.app.create_storage_manager", return_value=storage),
            patch("src.web.app.create_llm_components", return_value=(None, None, None)),
            patch("src.web.app.create_recommendation_engine"),
            patch("src.web.app.migrate_config_credentials"),
            patch("src.web.app.configure_logging"),
        ):
            _reset_app_state()
            app = create_app()
            app_state.config = config
            app_state.storage = storage
            yield TestClient(app), storage, config
        _reset_app_state()
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

    def test_secret_delete_returns_503_when_storage_unavailable(
        self, settings_env
    ) -> None:
        client, _storage, _config = settings_env
        app_state.storage = None

        response = client.delete(f"/api/settings/secret/{_SETTINGS_SECRET_KEY}")
        assert response.status_code == 503


def _fake_tempfile(mkstemp: Callable[..., tuple[int, str]] | None = None) -> Mock:
    """A stand-in for ``src.web.api``'s ``tempfile`` reference.

    Patched as ``patch("src.web.api.tempfile", _fake_tempfile(...))`` rather
    than ``patch("src.web.api.tempfile.mkstemp")``: the latter resolves through
    to the shared ``tempfile`` module and replaces the attribute there, so any
    other thread in the process creating a temp file during the block lands in
    this test's recording (or its ``assert_not_called``). ``api`` uses
    ``tempfile`` for nothing but ``mkstemp``, so rebinding the whole reference
    is both safe and local.

    Args:
        mkstemp: Implementation for ``mkstemp``. Omit to get a plain recorder
            that creates nothing — for tests asserting it never ran.
    """
    module = Mock(spec=tempfile)
    if mkstemp is not None:
        module.mkstemp.side_effect = mkstemp
    return module


def _recording_mkstemp(
    captured: dict[str, Path],
) -> Callable[..., tuple[int, str]]:
    """Wrap tempfile.mkstemp so a test can capture and later assert on the path.

    The real temp file is still created (the handler writes the upload into it);
    its path is recorded under ``captured["path"]`` so cleanup can be verified.
    """
    real_mkstemp = tempfile.mkstemp

    def recording(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        captured["path"] = Path(name)
        return fd, name

    return recording


def _chunked_multipart(
    source: str, filename: str, payload: bytes
) -> tuple[dict[str, str], list[bytes]]:
    """Build a multipart body as a list of chunks, with its content-type header.

    Sent through ``content=iter(chunks)`` so httpx uses ``Transfer-Encoding:
    chunked`` and declares no ``content-length`` — the only way to exercise the
    middleware's streaming counter against the real upload endpoint.
    """
    boundary = "recommendinatorTestBoundary"
    prologue = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="source"\r\n\r\n'
        f"{source}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode()
    epilogue = f"\r\n--{boundary}--\r\n".encode()
    headers = {"content-type": f"multipart/form-data; boundary={boundary}"}
    return headers, [prologue, payload, epilogue]


class TestImportSourcesEndpoint:
    """Tests for GET /api/import/sources."""

    def test_lists_only_file_import_plugins_with_schema(self, client):
        """The listing is exactly the five file-import plugins, no syncables.

        An exact set, not a subset: a syncable plugin that gained
        ``is_file_import`` by accident would stop being creatable and start
        accepting uploads, and a subset assertion would not notice.
        """
        response = client.get("/api/import/sources")
        assert response.status_code == 200
        data = response.json()
        names = {plugin["name"] for plugin in data}
        assert names == {
            "csv_import",
            "goodreads_csv",
            "json_import",
            "markdown_import",
            "storygraph_csv",
        }
        # Syncable sources (sonarr is configured in mock_config) are excluded.
        assert "sonarr" not in names
        # The Goodreads RSS feed is a network source, not a file import.
        assert "goodreads_rss" not in names

        csv_plugin = next(p for p in data if p["name"] == "csv_import")
        assert [f["name"] for f in csv_plugin["fields"]] == ["content_type"]

        goodreads_plugin = next(p for p in data if p["name"] == "goodreads_csv")
        assert goodreads_plugin["fields"] == []

        # The StoryGraph export is books-only, so it takes no options either.
        storygraph_plugin = next(p for p in data if p["name"] == "storygraph_csv")
        assert storygraph_plugin["fields"] == []

    def test_each_plugin_declares_the_extensions_it_reads(self, client):
        """The file picker's ``accept`` filter and help text come from here.

        Regression: the modal inferred the format by substring-matching the
        plugin name, so a future importer whose name says nothing about its
        format (``opml_import``) would have been offered a CSV file picker.
        """
        data = client.get("/api/import/sources").json()
        extensions = {p["name"]: p["accepted_extensions"] for p in data}

        assert extensions == {
            "csv_import": [".csv"],
            "goodreads_csv": [".csv"],
            "json_import": [".json", ".jsonl"],
            "markdown_import": [".md", ".markdown"],
            "storygraph_csv": [".csv"],
        }


class TestImportEndpoint:
    """Tests for POST /api/import."""

    def test_goodreads_happy_path(self, client, mock_components):
        """A Goodreads CSV upload imports every parsed book."""
        mock_components["storage"].save_content_item.return_value = 1
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 2
        assert body["total_items"] == 2
        # source is the plugin name the user passed, not the internal job label.
        assert body["source"] == "goodreads_csv"
        assert body["errors"] == []
        # An import that produced items has nothing to warn about.
        assert body["warning"] is None

    def test_csv_import_happy_path_with_content_type_option(
        self, client, mock_components
    ):
        """A generic CSV upload uses the content_type form field as an option."""
        mock_components["storage"].save_content_item.return_value = 1
        csv_content = "title,author,status,rating\nDune,Frank Herbert,read,5\n"
        response = client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book"},
            files={"file": ("books.csv", csv_content, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 1
        assert body["total_items"] == 1
        assert body["source"] == "csv_import"

    def test_unknown_plugin_rejected(self, client):
        """An unregistered source name is a 422 client error."""
        response = client.post(
            "/api/import",
            data={"source": "does_not_exist"},
            files={"file": ("x.csv", "ignored", "text/csv")},
        )
        assert response.status_code == 422

    def test_non_file_import_source_rejected(self, client):
        """A syncable (non-file-import) source is a 422 client error."""
        response = client.post(
            "/api/import",
            data={"source": "sonarr"},
            files={"file": ("x.csv", "ignored", "text/csv")},
        )
        assert response.status_code == 422

    def test_corrupt_file_returns_client_error(self, client):
        """A FileImportError maps to a 400 carrying only its client_detail."""
        with patch(
            "src.web.api.import_file",
            side_effect=FileImportError(
                "Failed to import file with 'csv_import': /tmp/upload-x.csv line 3",
                "Failed to import file with 'csv_import'.",
            ),
        ):
            response = client.post(
                "/api/import",
                data={"source": "csv_import", "content_type": "book"},
                files={"file": ("x.csv", "garbage", "text/csv")},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "Failed to import file with 'csv_import'."
        # The diagnostic half of the error stays server-side.
        assert "/tmp/upload-x.csv" not in response.text

    def test_import_failure_is_logged_once(self, client, caplog):
        """Regression: the same failure was written to the log twice.

        ``SyncManager.run_import`` logs every import exception with the job
        label, the exception type and the sanitized message; the API handler
        then logged the message again. The handler's line is gone — the more
        informative one is kept.
        """
        with (
            patch(
                "src.web.api.import_file",
                side_effect=FileImportError(
                    "Failed to import file with 'csv_import': /tmp/upload-x.csv",
                    "Failed to import file with 'csv_import'.",
                ),
            ),
            caplog.at_level(logging.WARNING),
        ):
            response = client.post(
                "/api/import",
                data={"source": "csv_import", "content_type": "book"},
                files={"file": ("x.csv", "garbage", "text/csv")},
            )

        assert response.status_code == 400
        failures = [
            record
            for record in caplog.records
            if "Failed to import file" in record.getMessage()
        ]
        assert len(failures) == 1
        assert failures[0].name == "src.web.sync_manager"
        assert "FileImportError" in failures[0].getMessage()

    def test_temp_file_cleaned_up_on_failure(self, client):
        """The streamed temp file is removed even when the import fails."""
        captured: dict[str, Path] = {}

        def fail(**kwargs: Any) -> None:
            captured["file_path"] = kwargs["file_path"]
            raise FileImportError("boom", "The file could not be imported.")

        with patch("src.web.api.import_file", side_effect=fail):
            response = client.post(
                "/api/import",
                data={"source": "csv_import", "content_type": "book"},
                files={"file": ("x.csv", "garbage", "text/csv")},
            )
        assert response.status_code == 400
        assert "file_path" in captured
        assert not captured["file_path"].exists()

    def test_progress_observable_via_status_endpoint(self, client, mock_components):
        """A completed import is reported as a job via GET /api/sync/status."""
        mock_components["storage"].save_content_item.return_value = 1
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 200

        status = client.get("/api/sync/status").json()
        jobs = {job["source"]: job for job in status["jobs"]}
        label = "Import: Goodreads (CSV Export)"
        assert label in jobs
        assert jobs[label]["status"] == "completed"
        assert jobs[label]["items_processed"] == 2

    def test_json_import_happy_path(self, client, mock_components):
        """A generic JSON upload imports every entry (criterion 1: JSON format)."""
        mock_components["storage"].save_content_item.return_value = 1
        json_content = json.dumps(
            [
                {"title": "Dune", "author": "Frank Herbert", "status": "completed"},
                {"title": "Neuromancer", "author": "William Gibson", "status": "read"},
            ]
        )
        response = client.post(
            "/api/import",
            data={"source": "json_import", "content_type": "book"},
            files={"file": ("books.json", json_content, "application/json")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 2
        assert body["total_items"] == 2
        assert body["source"] == "json_import"
        assert body["errors"] == []

    def test_markdown_import_happy_path(self, client, mock_components):
        """A markdown upload imports every entry (criterion 1: markdown format)."""
        mock_components["storage"].save_content_item.return_value = 1
        md_content = (
            "## Completed\n"
            "- **Dune** by Frank Herbert | Rating: 5\n"
            "- **Neuromancer** by William Gibson | Rating: 4\n"
        )
        response = client.post(
            "/api/import",
            data={"source": "markdown_import", "content_type": "book"},
            files={"file": ("books.md", md_content, "text/markdown")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 2
        assert body["total_items"] == 2
        assert body["source"] == "markdown_import"
        assert body["errors"] == []

    def test_temp_file_cleaned_up_on_success(self, client):
        """The streamed temp file is removed on the success path (criterion 7).

        The existing suite only proves cleanup on failure; this locks in that
        the ``finally`` block also runs after a successful import so uploads
        never accumulate on disk.
        """
        captured: dict[str, Path] = {}

        def succeed(**kwargs: Any) -> SyncResult:
            file_path = kwargs["file_path"]
            captured["file_path"] = file_path
            # The temp file must still exist while the import runs.
            assert file_path.exists()
            return SyncResult(source_name="csv_import", items_synced=1, total_items=1)

        with patch("src.web.api.import_file", side_effect=succeed):
            response = client.post(
                "/api/import",
                data={"source": "csv_import", "content_type": "book"},
                files={"file": ("x.csv", "title\nDune\n", "text/csv")},
            )
        assert response.status_code == 200, response.text
        assert "file_path" in captured
        assert not captured["file_path"].exists()

    def test_missing_required_option_returns_400(self, client, mock_components):
        """Omitting content_type for a generic format is a 400, not a 500.

        csv_import requires a content_type option; without it the plugin's
        validate_config fails, the service raises FileImportError, and the
        endpoint must surface a clean 400.
        """
        mock_components["storage"].save_content_item.return_value = 1
        response = client.post(
            "/api/import",
            data={"source": "csv_import"},
            files={"file": ("x.csv", "title\nDune\n", "text/csv")},
        )
        assert response.status_code == 400
        assert "content_type" in response.json()["detail"]

    def test_empty_file_imports_zero_items_with_a_warning(
        self, client, mock_components
    ):
        """An empty upload is a clean 200 with zero items and a warning.

        An empty file yields no rows; the import completes successfully with
        zero counts rather than failing or raising, but the response carries a
        warning so the user learns why nothing arrived.
        """
        mock_components["storage"].save_content_item.return_value = 1
        response = client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book"},
            files={"file": ("empty.csv", "", "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 0
        assert body["total_items"] == 0
        assert body["errors"] == []
        assert body["warning"] == NO_ITEMS_WARNING

    def test_wrong_format_file_returns_400(self, client, mock_components):
        """A JSON file sent to csv_import surfaces a clean 400, not a 500.

        The CSV parser sees no ``title`` column in the JSON content and raises
        a SourceError, which the service wraps into a FileImportError -> 400.
        """
        mock_components["storage"].save_content_item.return_value = 1
        json_content = json.dumps([{"name": "Dune"}, {"name": "Neuromancer"}], indent=2)
        response = client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book"},
            files={"file": ("books.json", json_content, "application/json")},
        )
        assert response.status_code == 400
        # Plugin-specific detail: the wrapper names the failing plugin so the
        # user knows which importer rejected the file, not just that it failed.
        assert "Failed to import file with 'csv_import'" in response.json()["detail"]

    def test_bom_prefixed_csv_imports(self, client, mock_components):
        """A UTF-8-BOM CSV (what Excel writes) imports like any other upload.

        Regression: the parser opened the file with plain ``utf-8``, so the BOM
        was glued onto the first header and the upload was rejected with "CSV
        missing required column: title" — sending the user hunting for a column
        that was right there. The readers now open with ``utf-8-sig``.
        """
        mock_components["storage"].save_content_item.return_value = 1
        csv_content = (
            "\N{ZERO WIDTH NO-BREAK SPACE}"
            "title,author,status,rating\nDune,Frank Herbert,read,5\n"
        )
        response = client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book"},
            files={"file": ("books.csv", csv_content, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 1
        assert body["errors"] == []

    def test_per_item_errors_surfaced_when_some_rows_fail(
        self, client, mock_components
    ):
        """A file that parses but whose rows partially fail to save reports them.

        The first item's save raises; the second succeeds. The endpoint must
        return 200 with the surviving count and a safe per-item error string
        (no raw exception text) for the failed row.
        """
        mock_components["storage"].save_content_item.side_effect = [
            RuntimeError("db write failed"),
            1,
        ]
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 1
        assert body["total_items"] == 2
        # Pins the pipeline's per-item error format ("Failed to process '<title>'"):
        # a safe, title-only string with no raw exception text.
        assert body["errors"] == ["Failed to process 'Dune'"]
        # The raw exception text must never leak to the client.
        assert "db write failed" not in json.dumps(body)

    def test_all_rows_failing_reports_errors_without_a_warning(
        self, client, mock_components
    ):
        """A file whose every row fails reports errors only, not also a warning.

        Zero items imported would otherwise look like the empty-file case. The
        per-item errors already explain the outcome, so warning as well would
        double-report it.
        """
        mock_components["storage"].save_content_item.side_effect = [
            RuntimeError("db write failed"),
            RuntimeError("db write failed"),
        ]
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 0
        assert body["total_items"] == 2
        assert body["errors"] == [
            "Failed to process 'Dune'",
            "Failed to process 'Neuromancer'",
        ]
        assert body["warning"] is None

    def test_all_rows_failing_is_a_200_response_and_a_failed_job(
        self, client, mock_components
    ):
        """The response and the job status answer different questions. Both are right.

        The 200 says the request succeeded: the file was accepted, parsed, and
        run — every row's outcome is reported in ``errors``. The job says the
        import produced nothing, which is what the Data tab's banner is for.
        Reconciling them by failing the request would hide the per-row detail
        behind an error body; reconciling them by completing the job would
        claim a successful import that saved nothing. This test exists so the
        divergence is a stated contract rather than an accident.
        """
        mock_components["storage"].save_content_item.side_effect = [
            RuntimeError("db write failed"),
            RuntimeError("db write failed"),
        ]
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["items_synced"] == 0

        job = next(
            job
            for job in client.get("/api/sync/status").json()["jobs"]
            if job["source"] == "Import: Goodreads (CSV Export)"
        )
        assert job["status"] == "failed"
        assert job["error_message"] == "Failed to process 'Dune'"
        assert job["error_count"] == 2

    def test_concurrent_import_returns_409(self, client, mock_components):
        """A second import for the same label while one runs is a 409.

        Plants a RUNNING job under the import's label in the global
        SyncManager, then posts an import that maps to the same label. The
        endpoint must reject it with 409 rather than starting a duplicate.
        """
        mock_components["storage"].save_content_item.return_value = 1
        label = "Import: Goodreads (CSV Export)"
        manager = get_sync_manager()
        with patch("src.web.sync_manager.threading.Thread"):
            manager.start_sync(source=label, sync_function=lambda _job: 0)
        assert manager.is_running(label) is True

        captured: dict[str, Path] = {}
        with patch(
            "src.web.api.tempfile", _fake_tempfile(_recording_mkstemp(captured))
        ):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )
        assert response.status_code == 409
        assert response.json()["detail"] == IMPORT_ALREADY_RUNNING_DETAIL
        # The label the exception carries names the plugin; it must not be what
        # goes on the wire, or its text becomes a wire contract by accident.
        assert label not in response.text
        # The streamed temp file must be removed on the 409 path too.
        assert "path" in captured
        assert not captured["path"].exists()

    def test_concurrency_guard_is_scoped_to_the_same_import_plugin(
        self, client, mock_components
    ):
        """The 409 guard is per-plugin, not a global import/sync lock.

        Pins the actual scope of the server-side guard so it is not mistaken
        for global serialisation: a running job under a *different* label (here
        a whole-library sync) does not block an import. The Import modal
        deliberately blocks more than this client-side — it disables the button
        whenever any job is running — so the two layers must not be conflated
        when reasoning about concurrency.
        """
        mock_components["storage"].save_content_item.return_value = 1
        manager = get_sync_manager()
        with patch("src.web.sync_manager.threading.Thread"):
            manager.start_sync(source="All Sources", sync_function=lambda _job: 0)
        assert manager.is_running("All Sources") is True
        assert manager.is_running() is True

        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["items_synced"] == 2

    def test_oversized_body_never_reaches_the_upload_handler(self, mock_components):
        """A body over the request cap is refused before the handler is entered.

        This is the layer that actually bounds disk use. By the time the
        handler runs, FastAPI has resolved ``file: UploadFile``, which means
        Starlette's multipart parser has already drained the whole request into
        a ``SpooledTemporaryFile`` — spilling to the system temp directory past
        1 MB, with no total-size limit. So the handler's own chunk loop can
        only bound the second copy. ``mkstemp`` never being called is the proof
        that nothing in the handler ran; ``tests/web/test_upload_limit.py``
        proves the wrapped application never reads the body at all.
        """
        fake_tempfile = _fake_tempfile()
        with (
            patch("src.web.app.MAX_REQUEST_BODY_BYTES", 128),
            patch("src.web.api.tempfile", fake_tempfile),
        ):
            app = create_app()
            response = TestClient(app).post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", "x" * 4096, "text/csv")},
            )

        assert response.status_code == 413
        assert "exceeds" in response.json()["detail"]
        fake_tempfile.mkstemp.assert_not_called()
        mock_components["storage"].save_content_item.assert_not_called()

    def test_oversized_chunked_upload_is_a_413_not_a_parse_error(
        self, mock_components, caplog
    ):
        """A body with no declared length is refused as a 413, not a 400.

        Regression: the middleware signalled the overrun by raising a private
        exception from the wrapped ``receive``. On this endpoint that raise
        happens inside ``await request.form()``, and FastAPI wraps that call in
        ``except Exception`` -> HTTP 400 "There was an error parsing the body"
        — so on the one route the cap exists for, an oversized chunked upload
        came back as a 400 and the modal said "We couldn't read that file".
        The cap still held; the answer was wrong. The existing coverage missed
        it because its probe endpoint called ``await request.body()``, which
        FastAPI does not wrap.

        The declared-``content-length`` path is unaffected (it never reaches
        the app at all) and is pinned by the test above.
        """
        headers, chunks = _chunked_multipart("goodreads_csv", "books.csv", b"x" * 4096)
        fake_tempfile = _fake_tempfile()
        with (
            patch("src.web.app.MAX_REQUEST_BODY_BYTES", 128),
            patch("src.web.api.tempfile", fake_tempfile),
            caplog.at_level(logging.WARNING, logger="src.web.upload_limit"),
        ):
            app = create_app()
            response = TestClient(app).post(
                "/api/import", headers=headers, content=iter(chunks)
            )

        # No declared length, so this is the streaming counter's branch — the
        # one whose warning never fired because the signal was swallowed first.
        assert "content-length" not in {
            name.lower() for name in response.request.headers
        }
        assert "mid-stream" in caplog.text
        assert response.status_code == 413, response.text
        assert "exceeds" in response.json()["detail"]
        fake_tempfile.mkstemp.assert_not_called()
        mock_components["storage"].save_content_item.assert_not_called()

    def test_oversized_upload_returns_413_and_cleans_up(self, client, mock_components):
        """The handler's own cap check is a backstop, and it cleans up after itself.

        Distinct from the middleware above: this body is small enough to pass
        the request cap, so the handler does run, copies the parsed upload out
        in chunks, and aborts on the file cap (patched low here rather than
        streaming tens of megabytes). What it pins is that the abort leaves no
        partial temp file behind — not that the body stayed off the disk, which
        is the middleware's job.
        """
        mock_components["storage"].save_content_item.return_value = 1
        captured: dict[str, Path] = {}
        with (
            patch("src.web.api.MAX_UPLOAD_BYTES", 16),
            patch("src.web.api.tempfile", _fake_tempfile(_recording_mkstemp(captured))),
        ):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", "x" * 1024, "text/csv")},
            )
        assert response.status_code == 413
        assert "exceeds" in response.json()["detail"]
        assert "path" in captured
        assert not captured["path"].exists()

    def test_chunked_copy_aborts_without_ever_exceeding_the_cap(
        self, client, mock_components
    ):
        """The copy loop stops at the cap mid-file, not after writing it all.

        Every other cap test uses a body under the 1 MB chunk size, so the
        ``while chunk := await file.read(...)`` loop runs exactly once and the
        incremental accounting is never exercised — a loop that wrote the whole
        upload before checking would pass them all. Shrinking the chunk size
        alongside the cap forces several iterations and pins the property that
        matters: the partial temp file never grows past the cap.
        """
        mock_components["storage"].save_content_item.return_value = 1
        sizes: list[int] = []
        real_fdopen = os.fdopen

        def recording_fdopen(fd: int, mode: str) -> Any:
            handle = real_fdopen(fd, mode)
            original_write = handle.write

            def write(chunk: bytes) -> int:
                written = original_write(chunk)
                handle.flush()
                sizes.append(handle.tell())
                return written

            handle.write = write
            return handle

        fake_os = Mock(spec=os)
        fake_os.fdopen.side_effect = recording_fdopen
        with (
            patch("src.web.api._UPLOAD_CHUNK_BYTES", 8),
            patch("src.web.api.MAX_UPLOAD_BYTES", 32),
            # The whole ``os`` reference, not ``os.fdopen``: ``api`` uses it for
            # nothing else, and reaching into the shared module would hand every
            # other thread's ``os.fdopen`` call to this test's recorder.
            patch("src.web.api.os", fake_os),
        ):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", "x" * 1024, "text/csv")},
            )

        assert response.status_code == 413
        # More than one iteration — otherwise this proves nothing the
        # single-chunk tests do not already cover.
        assert len(sizes) > 1
        assert max(sizes) <= 32
        mock_components["storage"].save_content_item.assert_not_called()

    def test_upload_of_exactly_the_cap_is_accepted(self, client, mock_components):
        """A file exactly at the byte cap imports; only *over* the cap is 413.

        The check is ``total > MAX_UPLOAD_BYTES``, so the boundary value must
        pass. An off-by-one here would reject a legitimate 50 MB export.
        """
        mock_components["storage"].save_content_item.return_value = 1
        with patch("src.web.api.MAX_UPLOAD_BYTES", len(GOODREADS_CSV)):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )
        assert response.status_code == 200, response.text
        assert response.json()["items_synced"] == 2

    def test_upload_one_byte_over_the_cap_is_rejected(self, client, mock_components):
        """One byte past the cap is a 413 — the other half of the boundary."""
        mock_components["storage"].save_content_item.return_value = 1
        with patch("src.web.api.MAX_UPLOAD_BYTES", len(GOODREADS_CSV) - 1):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )
        assert response.status_code == 413
        mock_components["storage"].save_content_item.assert_not_called()

    def test_cap_is_mirrored_in_the_frontend_constant(self):
        """The Import modal re-declares this cap in TypeScript; pin them together.

        ``resources/js/constants/upload.ts`` holds a copy so the modal can
        refuse an oversized file before spending the upload. Nothing else ties
        the two languages together, so raising one alone would leave the
        client-side check silently disagreeing with the 413.
        """
        constants = _REPO_ROOT / "resources" / "js" / "constants" / "upload.ts"
        match = re.search(
            r"MAX_UPLOAD_MB = (\d+)", constants.read_text(encoding="utf-8")
        )
        assert match is not None, f"MAX_UPLOAD_MB not declared in {constants}"
        assert int(match.group(1)) * 1024 * 1024 == MAX_UPLOAD_BYTES

    def test_returns_503_when_components_uninitialized(self, client, mock_components):
        """With storage uninitialized the endpoint is a clean 503, not a crash.

        Uses the module's ``_require_storage`` helper, like every other
        endpoint, rather than a local truthiness check with its own status.
        """
        app_state.storage = None
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Storage unavailable"

    def test_returns_503_when_config_uninitialized(self, client, mock_components):
        """Config is the second boot dependency, and it gets its own 503.

        The modal's copy names both ("storage or configuration"), but only the
        storage half was pinned. The handler reads feature flags and the
        enrichment block off the config, so a null one would otherwise be an
        ``AttributeError`` 500 rather than a stated unavailability.
        """
        app_state.config = None
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Config unavailable"
        mock_components["storage"].save_content_item.assert_not_called()

    def test_unreadable_file_error_is_masked(self, client):
        """The not-readable FileImportError never leaks the internal temp path.

        The service embeds the (temp) path in its message for the log and the
        CLI; the response carries only the structured ``client_detail`` so the
        client cannot see server-side filesystem paths.
        """
        secret_path = "/tmp/server-secret-upload-xyz.csv"
        with patch(
            "src.web.api.import_file",
            side_effect=FileImportError(
                f"{FILE_NOT_READABLE_MESSAGE}: {secret_path}", UNREADABLE_FILE_DETAIL
            ),
        ):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "The uploaded file could not be read"
        assert secret_path not in response.text

    def test_goodreads_rss_is_not_importable(self, client):
        """The RSS half of the Goodreads split is a feed source, not an import.

        Control for the plugin split: only ``goodreads_csv`` accepts an upload.
        Were ``goodreads_rss`` accidentally flagged as a file import, this would
        return 200 instead of rejecting the upload.
        """
        response = client.post(
            "/api/import",
            data={"source": "goodreads_rss"},
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 422

    def test_missing_source_field_is_a_validation_error(self, client):
        """Omitting the ``source`` form field is a 422, not a 500."""
        response = client.post(
            "/api/import",
            files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
        )
        assert response.status_code == 422

    def test_missing_file_part_is_a_validation_error(self, client):
        """Omitting the ``file`` part is a 422, not a 500."""
        response = client.post("/api/import", data={"source": "goodreads_csv"})
        assert response.status_code == 422

    def test_a_file_part_named_like_an_option_is_ignored(self, client, mock_components):
        """A second *file* part named ``content_type`` must not become an option.

        The handler reads options off the raw multipart form, so a client can
        name a file part after a config field. Only string parts may enter the
        options dict — an UploadFile reaching the plugin config would be a type
        confusion. Here csv_import's required option is supplied only by the
        file part, so the import must fail validation rather than accept it.
        """
        mock_components["storage"].save_content_item.return_value = 1
        response = client.post(
            "/api/import",
            data={"source": "csv_import"},
            files={
                "file": ("books.csv", "title\nDune\n", "text/csv"),
                "content_type": ("book.txt", "book", "text/plain"),
            },
        )
        assert response.status_code == 400
        assert "content_type" in response.json()["detail"]
        mock_components["storage"].save_content_item.assert_not_called()

    def test_temp_file_cleaned_up_on_unexpected_error(self, client):
        """An error the handler does not map still leaves no temp file behind.

        ``FileImportError`` / ``SyncInProgressError`` have explicit handlers; a
        storage or plugin fault that escapes as something else must still hit
        the ``finally`` unlink rather than leaking the upload onto disk.
        """
        captured: dict[str, Path] = {}

        def blow_up(**kwargs: Any) -> None:
            captured["file_path"] = kwargs["file_path"]
            raise RuntimeError("unexpected internal fault")

        # ``raise_server_exceptions=False`` makes the client return the 500 the
        # deployed server would send instead of re-raising into the test.
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        with patch("src.web.api.import_file", side_effect=blow_up):
            response = unwrapped.post(
                "/api/import",
                data={"source": "csv_import", "content_type": "book"},
                files={"file": ("x.csv", "title\nDune\n", "text/csv")},
            )
        assert response.status_code == 500
        # The raw exception text must not reach the client.
        assert "unexpected internal fault" not in response.text
        assert "file_path" in captured
        assert not captured["file_path"].exists()

    def test_unicode_filename_and_content_import_cleanly(self, client, mock_components):
        """A non-ASCII filename and body survive the multipart + temp-file hop.

        The temp file takes its suffix from the uploaded filename, so a
        non-ASCII name must not break ``mkstemp``, and the CSV must still be
        decoded as UTF-8.
        """
        mock_components["storage"].save_content_item.return_value = 1
        csv_content = (
            "title,author,status,rating\n"
            "Les Misérables,Victor Hugo,read,5\n"
            "こころ,夏目漱石,read,4\n"
        )
        response = client.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book"},
            files={"file": ("本の一覧.csv", csv_content, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items_synced"] == 2
        assert body["errors"] == []
        titles = [
            call.args[0].title
            for call in mock_components["storage"].save_content_item.call_args_list
        ]
        assert titles == ["Les Misérables", "こころ"]

    @pytest.mark.parametrize(
        ("source", "options", "filename", "body"),
        [
            ("goodreads_csv", {}, "books.csv", "Title,Author\nCafé,Hugo\n"),
            (
                "csv_import",
                {"content_type": "book"},
                "books.csv",
                "title,author\nCafé,Hugo\n",
            ),
            (
                "storygraph_csv",
                {},
                "library.csv",
                "Title,Authors\nCafé,Hugo\n",
            ),
            (
                "markdown_import",
                {"content_type": "book"},
                "books.md",
                "## Completed\n- **Café** by Hugo\n",
            ),
        ],
    )
    def test_latin1_upload_returns_400_not_500(
        self, client, mock_components, source, options, filename, body
    ):
        """A Latin-1 export is a clean 400 on every text importer, not a 500.

        Regression: ``execute_sync`` called ``list(plugin.fetch(...))`` with no
        exception handling and the import service wrapped only ``SourceError``,
        so a ``UnicodeDecodeError`` from any non-UTF-8 export escaped as a bare
        500 with a stack trace in the log.
        """
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        response = unwrapped.post(
            "/api/import",
            data={"source": source, **options},
            files={"file": (filename, body.encode("latin-1"), "text/plain")},
        )

        assert response.status_code == 400, response.text
        assert "Failed to import file" in response.json()["detail"]
        mock_components["storage"].save_content_item.assert_not_called()

    def test_utf16_upload_returns_400_not_500(self, client, mock_components):
        """A UTF-16 export (what some tools emit by default) is also a 400."""
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        response = unwrapped.post(
            "/api/import",
            data={"source": "json_import", "content_type": "book"},
            files={
                "file": (
                    "books.json",
                    json.dumps([{"title": "Café"}]).encode("utf-16"),
                    "application/json",
                )
            },
        )

        assert response.status_code == 400, response.text
        mock_components["storage"].save_content_item.assert_not_called()

    def test_deeply_nested_json_returns_400_not_500(self, client, mock_components):
        """A few KB of '[' exhausts the JSON parser's stack; that is still a 400.

        ``RecursionError`` is not a ``ValueError``, so it escaped the plugin's
        JSON error handling and the service's wrapper alike.
        """
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        response = unwrapped.post(
            "/api/import",
            data={"source": "json_import", "content_type": "book"},
            files={"file": ("deep.json", "[" * 20000, "application/json")},
        )

        assert response.status_code == 400, response.text
        mock_components["storage"].save_content_item.assert_not_called()

    def test_binary_upload_returns_400_not_500(self, client, mock_components):
        """A binary file (a mistakenly picked .zip) is a 400, not a crash."""
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        response = unwrapped.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "book"},
            files={
                "file": ("books.zip", b"PK\x03\x04\x00\xff\xfe\x00", "application/zip")
            },
        )

        assert response.status_code == 400, response.text

    def test_auto_enrich_triggers_enrichment_on_success(self, client, mock_components):
        """With auto-enrich configured, a successful import starts enrichment.

        The completion callback must forward the live storage_manager and
        config. A Goodreads upload carries no content_type form field, so the
        enrichment run is unscoped (content_type is None) and covers all types.
        """
        mock_components["storage"].save_content_item.return_value = 1
        app_state.config["enrichment"] = {
            "enabled": True,
            "auto_enrich_on_sync": True,
        }
        with patch("src.web.api.get_enrichment_manager") as mock_get:
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )
        assert response.status_code == 200, response.text
        mock_get.return_value.start_enrichment.assert_called_once_with(
            storage_manager=mock_components["storage"],
            config=app_state.config,
            content_type=None,
        )

    def test_auto_enrich_scopes_the_run_to_the_imported_content_type(
        self, client, mock_components
    ):
        """A content_type option narrows the enrichment run to that type.

        The unscoped case above cannot tell a working scope from one that is
        always ``None``; this is the branch where the option is read.
        """
        mock_components["storage"].save_content_item.return_value = 1
        app_state.config["enrichment"] = {
            "enabled": True,
            "auto_enrich_on_sync": True,
        }
        with patch("src.web.api.get_enrichment_manager") as mock_get:
            response = client.post(
                "/api/import",
                data={"source": "csv_import", "content_type": "movie"},
                files={"file": ("films.csv", "title\nDune\n", "text/csv")},
            )
        assert response.status_code == 200, response.text
        mock_get.return_value.start_enrichment.assert_called_once_with(
            storage_manager=mock_components["storage"],
            config=app_state.config,
            content_type=ContentType.MOVIE,
        )

    def test_auto_enrich_off_neither_enriches_nor_marks_items(
        self, client, mock_components
    ):
        """Without auto-enrich the import must not start a run or mark items.

        ``mark_for_enrichment`` rides the same flag, so an inverted condition
        would leave every imported item queued for a run that never starts.
        """
        mock_components["storage"].save_content_item.return_value = 1
        app_state.config["enrichment"] = {
            "enabled": True,
            "auto_enrich_on_sync": False,
        }
        with (
            patch("src.web.api.get_enrichment_manager") as mock_get,
            patch("src.web.api.import_file") as mock_import,
        ):
            mock_import.return_value = SyncResult(
                source_name="goodreads_csv", items_synced=2, total_items=2
            )
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )
        assert response.status_code == 200, response.text
        mock_get.return_value.start_enrichment.assert_not_called()
        assert mock_import.call_args.kwargs["mark_for_enrichment"] is False

    def test_unrecognised_content_type_is_a_400_not_a_500(
        self, client, mock_components
    ):
        """An unusable content_type is refused by the plugin, not by a crash.

        The handler derives the enrichment scope from the same option before
        the import runs. That derivation must degrade to "unscoped" rather than
        raising — auto-enrich is on here, so a raise would turn the plugin's
        clean 400 into an unhandled 500.
        """
        app_state.config["enrichment"] = {
            "enabled": True,
            "auto_enrich_on_sync": True,
        }
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        response = unwrapped.post(
            "/api/import",
            data={"source": "csv_import", "content_type": "paperback"},
            files={"file": ("books.csv", "title\nDune\n", "text/csv")},
        )
        assert response.status_code == 400, response.text
        assert "Invalid content_type 'paperback'" in response.json()["detail"]
        mock_components["storage"].save_content_item.assert_not_called()

    def test_job_label_prefix_is_mirrored_in_the_import_modal(self):
        """The ``Import: `` job label is built in two languages; pin them together.

        ``src/web/api.py`` labels the job; ``ImportFileModal.vue`` rebuilds the
        same string to find that job in the sync-status poll and drive its
        progress bar. Nothing else ties them, so changing the prefix on one
        side would blank the progress bar with every test still green — the
        same failure mode the upload-cap constant is pinned against.
        """
        modal = (
            _REPO_ROOT
            / "resources"
            / "js"
            / "components"
            / "organisms"
            / "ImportFileModal.vue"
        )
        source = modal.read_text(encoding="utf-8")
        assert f"jobForLabel(`{IMPORT_JOB_LABEL_PREFIX}${{" in source


class TestImportResponsesNeverLeakTheTempPath:
    """No import failure puts the server-side temp path on the wire.

    Regression: the handler returned ``detail=str(error)`` for every
    FileImportError except one exact prefix, and the service forwarded plugin
    text verbatim — three of the file plugins embed the temp path in their
    not-found messages, and the parse branches forwarded raw library text. The
    error now carries a structured ``client_detail`` and the full message is
    logged instead.
    """

    @pytest.mark.parametrize(
        ("source", "options", "body"),
        [
            # Wrong format for the importer (plugin SourceError).
            (
                "csv_import",
                {"content_type": "book"},
                b'[\n  {\n    "name": "Dune"\n  }\n]\n',
            ),
            # Missing required option (plugin validation).
            ("csv_import", {}, b"title\nDune\n"),
            # Unparseable JSON (raw library text).
            ("json_import", {"content_type": "book"}, b"{not valid json"),
            # Not UTF-8 (decode failure).
            (
                "markdown_import",
                {"content_type": "book"},
                "- **Café**\n".encode("latin-1"),
            ),
            ("goodreads_csv", {}, "Title\nCafé\n".encode("latin-1")),
        ],
    )
    def test_failure_details_carry_no_temp_path(
        self, client, mock_components, source, options, body
    ):
        """Whatever goes wrong, the response never names the upload's temp file."""
        captured: dict[str, Path] = {}
        unwrapped = TestClient(client.app, raise_server_exceptions=False)
        with patch(
            "src.web.api.tempfile", _fake_tempfile(_recording_mkstemp(captured))
        ):
            response = unwrapped.post(
                "/api/import",
                data={"source": source, **options},
                files={"file": ("books.csv", body, "text/csv")},
            )

        assert response.status_code == 400, response.text
        assert "path" in captured
        temp_path = captured["path"]
        assert str(temp_path) not in response.text
        assert temp_path.name not in response.text
        assert tempfile.gettempdir() not in response.text

    def test_unreadable_upload_detail_carries_no_temp_path(
        self, client, mock_components
    ):
        """The not-readable branch is the one that embeds the path in its message.

        The temp file is removed the moment it is created, so ``import_file``
        takes its ``File not found or not readable: <temp path>`` branch — the
        exact message the handler used to prefix-match on.
        """
        captured: dict[str, Path] = {}
        real_mkstemp = tempfile.mkstemp

        def vanishing_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
            fd, name = real_mkstemp(*args, **kwargs)
            captured["path"] = Path(name)
            Path(name).unlink()
            return fd, name

        with patch("src.web.api.tempfile", _fake_tempfile(vanishing_mkstemp)):
            response = client.post(
                "/api/import",
                data={"source": "goodreads_csv"},
                files={"file": ("books.csv", GOODREADS_CSV, "text/csv")},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "The uploaded file could not be read"
        assert str(captured["path"]) not in response.text


class TestUploadTempSuffix:
    """The uploaded filename's extension is constrained before ``mkstemp``."""

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("books.csv", ".csv"),
            ("books.JSON", ".JSON"),
            ("archive.tar.gz", ".gz"),
            ("../../etc/passwd.csv", ".csv"),
            ("no-extension", ""),
            (None, ""),
            # An embedded NUL makes mkstemp raise ValueError.
            ("books.cs\x00v", ""),
            # An overlong extension makes mkstemp raise OSError.
            ("books." + "a" * 300, ""),
            # Not a plain extension: separators, spaces, unicode.
            ("books.c v", ""),
            ("本の一覧.csv", ".csv"),
            ("books.☃", ""),
        ],
    )
    def test_only_plain_short_extensions_survive(self, filename, expected):
        assert _safe_temp_suffix(filename) == expected

    def test_hostile_filename_still_imports(self, client, mock_components):
        """An unusable extension costs the suffix, not the upload.

        Both an embedded NUL and a 300-character extension used to reach
        ``mkstemp`` and raise (``ValueError`` / ``OSError: File name too
        long``) from above the try/finally — an unhandled 500.
        """
        mock_components["storage"].save_content_item.return_value = 1
        response = client.post(
            "/api/import",
            data={"source": "goodreads_csv"},
            files={"file": ("books." + "a" * 300, GOODREADS_CSV, "text/csv")},
        )

        assert response.status_code == 200, response.text
        assert response.json()["items_synced"] == 2


class TestPluginPickerExcludesFileImports:
    """GET /api/plugins offers only plugins that can become a source.

    Runs against the real registry (not fakes) so it pins the actual plugin
    ids: the five file-import plugins are addressed through POST /api/import,
    and offering them in the Add-source picker would only lead the user into a
    source that can never sync. ``goodreads_rss`` is the control — the RSS half
    of the Goodreads split polls a feed and must stay offerable.
    """

    def test_file_import_plugins_are_not_offered(self, client):
        response = client.get("/api/plugins")
        assert response.status_code == 200
        names = {plugin["name"] for plugin in response.json()}

        assert names.isdisjoint(
            {
                "goodreads_csv",
                "csv_import",
                "json_import",
                "markdown_import",
                "storygraph_csv",
            }
        )
        assert "goodreads_rss" in names
        assert "sonarr" in names
        # roms scans directories rather than reading one uploaded file, so it
        # stays here. That it is absent from the import listing is already an
        # exact-set assertion in TestImportSourcesEndpoint; its own
        # ``is_file_import`` flag and config schema are pinned in
        # tests/test_plugin_base.py and src/.../roms/test_roms.py.
        assert "roms" in names
