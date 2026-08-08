"""Tests for web application state management functions."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.conversation.engine import ConversationEngine
from src.llm.client import OllamaClient
from src.storage.manager import StorageManager
from src.web.state import (
    AppState,
    ConfigWatcher,
    app_state,
    get_conversation_engine,
    get_ollama_client,
    reload_config,
)
from tests.factories import back_mock_settings_store

_AWATCH_PATCH_TARGET = "watchfiles.awatch"


@pytest.fixture(autouse=True)
def _clean_app_state() -> Any:
    """Save and restore app_state around each test."""
    saved = {f.name: getattr(app_state, f.name) for f in fields(app_state)}
    # Reset to defaults
    fresh = AppState()
    for f in fields(fresh):
        setattr(app_state, f.name, getattr(fresh, f.name))
    yield
    for f in fields(app_state):
        setattr(app_state, f.name, saved[f.name])


# ---------------------------------------------------------------------------
# reload_config tests
# ---------------------------------------------------------------------------


class TestReloadConfig:
    """Tests for reload_config() function."""

    def test_reload_config_success(self, tmp_path: Path) -> None:
        """reload_config returns True and updates app_state on success."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("ollama:\n  model: test-model\n")

        app_state.config_path = str(config_file)
        app_state.config = {"old": "config"}

        result = reload_config()

        assert result is True
        assert app_state.config["ollama"]["model"] == "test-model"
        assert "old" not in app_state.config

    def test_reload_config_runs_both_source_migrations(self, tmp_path: Path) -> None:
        """reload_config runs both source migrations with the stored storage.

        When storage is present, a hot-reload must relabel stored items and DB
        source configs, mirroring the migrations that run on web startup.
        """
        config_file = tmp_path / "config.yaml"
        config_file.write_text("ollama:\n  model: test-model\n")

        app_state.config_path = str(config_file)
        storage = Mock(spec=StorageManager)
        # The real settings hook now runs on hot-reload; back the mock's
        # settings store so it is an isolated no-op here.
        back_mock_settings_store(storage)
        app_state.storage = storage

        with (
            patch("src.web.state.migrate_config_credentials"),
            patch("src.web.state.migrate_source_labels") as mock_labels,
            patch("src.web.state.migrate_source_config_plugins") as mock_plugins,
        ):
            result = reload_config()

        assert result is True
        mock_labels.assert_called_once_with(storage)
        mock_plugins.assert_called_once_with(storage)

    def test_reload_config_no_config_path(self) -> None:
        """reload_config returns False when no config_path is stored."""
        # app_state is empty (no config_path)
        result = reload_config()

        assert result is False

    def test_reload_config_no_config_path_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """reload_config logs a warning when config_path is missing."""
        with caplog.at_level(logging.WARNING, logger="src.web.state"):
            reload_config()

        assert "Cannot reload config" in caplog.text

    def test_reload_reapplies_settings_overlay(self, tmp_path: Path) -> None:
        """Hot-reload re-runs the real settings migration/overlay against the DB.

        Regression: DB-backed global config must survive a config file edit —
        the watcher reloads YAML, so the overlay has to re-apply or the YAML
        value would silently win until the next restart. Drives the real hook
        against an isolated temp-DB StorageManager (no stub).
        """
        config_file = tmp_path / "config.yaml"
        config_file.write_text("recommendations:\n  default_count: 11\n")

        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        # A DB leaf the operator edited must win over the reloaded YAML value.
        # All three layers differ (const 5 < YAML 11 < DB 9), so 9 can only
        # reach the running config through the overlay.
        storage.set_setting("recommendations.default_count", 9)

        app_state.config_path = str(config_file)
        app_state.storage = storage

        result = reload_config()

        assert result is True
        assert app_state.config["recommendations"]["default_count"] == 9

    def test_reload_sweeps_config_secret_into_storage(self, tmp_path: Path) -> None:
        """Hot-reload re-runs the secret sweep against the DB.

        Regression: an edited YAML that reintroduces a provider api_key must be
        swept into encrypted storage and stripped from the running config on
        reload — otherwise a plaintext secret would linger in app_state until
        the next restart. Drives the real hook against an isolated temp-DB.
        """
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "enrichment:\n  providers:\n    tmdb:\n      api_key: tmdb-secret\n"
        )

        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        app_state.config_path = str(config_file)
        app_state.storage = storage

        result = reload_config()

        assert result is True
        assert storage.has_global_secret("enrichment.providers.tmdb.api_key") is True
        providers = app_state.config["enrichment"]["providers"]
        assert providers.get("tmdb", {}).get("api_key") is None

    def test_reload_swaps_the_running_config_without_touching_the_old_one(
        self, tmp_path: Path
    ) -> None:
        """Hot-reload binds a fresh config and leaves the previous one alone.

        Regression: the reload used to empty the running dict and refill it,
        because the recommendation engine held that dict by reference. Scoring
        runs in a threadpool worker while the reload runs on the event loop, so
        a request landing between the two calls read an empty config and
        silently scored on the engine's constructor baseline instead of the
        configured weights. The engine now resolves the config through
        ``get_config`` on every read, which is what lets this be one rebind.
        """
        config_file = tmp_path / "config.yaml"
        config_file.write_text("recommendations:\n  min_rating_for_preference: 2\n")

        running: dict[str, Any] = {"old": "config"}
        app_state.config_path = str(config_file)
        app_state.config = running

        result = reload_config()

        assert result is True
        # What a reader already holding the old config keeps seeing.
        assert running == {"old": "config"}
        assert app_state.config is not running
        assert "old" not in app_state.config
        assert app_state.config["recommendations"]["min_rating_for_preference"] == 2

    def test_reload_config_load_raises_returns_false(self) -> None:
        """reload_config returns False when load_config raises an exception.

        load_config has fallback behavior (falls back to example.yaml),
        so we mock it to simulate a genuine failure such as a permissions
        error or corrupted file.
        """
        app_state.config_path = "/some/path.yaml"

        with patch(
            "src.web.state.load_config",
            side_effect=OSError("Permission denied"),
        ):
            result = reload_config()

        assert result is False

    def test_reload_config_preserves_old_config_on_failure(self) -> None:
        """reload_config does not replace existing config when reload fails."""
        original_config = {"preserved": True}
        app_state.config = original_config
        app_state.config_path = "/some/path.yaml"

        with patch(
            "src.web.state.load_config",
            side_effect=ValueError("Bad config"),
        ):
            reload_config()

        assert app_state.config is original_config

    def test_reload_config_logs_error_on_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """reload_config logs an error when the reload fails."""
        app_state.config_path = "/some/path.yaml"

        with (
            caplog.at_level(logging.ERROR, logger="src.web.state"),
            patch(
                "src.web.state.load_config",
                side_effect=RuntimeError("disk error"),
            ),
        ):
            reload_config()

        assert "Failed to reload config" in caplog.text


# ---------------------------------------------------------------------------
# get_conversation_engine tests
# ---------------------------------------------------------------------------


class TestGetConversationEngine:
    """Tests for get_conversation_engine() function."""

    def test_returns_none_when_not_set(self) -> None:
        """get_conversation_engine returns None when not in app_state."""
        result = get_conversation_engine()

        assert result is None

    def test_returns_engine_when_set(self) -> None:
        """get_conversation_engine returns the stored engine."""
        mock_engine = MagicMock(spec=ConversationEngine)
        app_state.conversation_engine = mock_engine

        result = get_conversation_engine()

        assert result is mock_engine


# ---------------------------------------------------------------------------
# get_ollama_client tests
# ---------------------------------------------------------------------------


class TestGetOllamaClient:
    """Tests for get_ollama_client() function."""

    def test_returns_none_when_not_set(self) -> None:
        """get_ollama_client returns None when not in app_state."""
        result = get_ollama_client()

        assert result is None

    def test_returns_client_when_set(self) -> None:
        """get_ollama_client returns the stored client."""
        mock_client = MagicMock(spec=OllamaClient)
        app_state.ollama_client = mock_client

        result = get_ollama_client()

        assert result is mock_client


# ---------------------------------------------------------------------------
# ConfigWatcher tests
# ---------------------------------------------------------------------------


_HANDLED_TIMEOUT_SECONDS = 5.0


def _awatch_one_event(
    handled: asyncio.Event,
) -> Callable[[Path], AsyncIterator[set[tuple[str, str]]]]:
    """Build an ``awatch`` stand-in that yields one change, then flags *handled*.

    The watcher hands the reload to a worker thread, so "reload_config ran" and
    "the watcher is done with the event" are two different moments. The
    generator is resumed only at the second one, which is the one a test can
    assert the watcher's own logging against.
    """

    async def awatch(path: Path) -> AsyncIterator[set[tuple[str, str]]]:
        yield {("modified", str(path))}
        handled.set()
        await asyncio.Event().wait()

    return awatch


async def _fake_awatch_no_events(
    path: Path,
) -> AsyncIterator[set[tuple[str, str]]]:
    """Block forever without yielding — simulates no file changes."""
    await asyncio.Event().wait()
    yield set()  # pragma: no cover


async def _fake_awatch_raising(
    path: Path,
) -> AsyncIterator[set[tuple[str, str]]]:
    """Immediately raise OSError — simulates inotify limit or permission error."""
    raise OSError("inotify limit reached")
    yield set()  # pragma: no cover


class TestConfigWatcher:
    """Tests for ConfigWatcher — automatic config file hot-reload.

    Bug: Config changes required a Docker container restart (issue #9).
    Root cause: Config was loaded once at startup with no file watching.
    Fix: Added ConfigWatcher that uses watchfiles to detect changes and
    automatically calls reload_config().
    """

    def test_watcher_calls_reload_on_change(self) -> None:
        """ConfigWatcher calls reload_config when awatch yields a change.

        Regression test for #9: config changes should be hot-reloaded
        without requiring a container restart.
        """

        async def _run() -> None:
            handled = asyncio.Event()
            with (
                patch(_AWATCH_PATCH_TARGET, side_effect=_awatch_one_event(handled)),
                patch("src.web.state.reload_config", return_value=True) as mock_reload,
            ):
                watcher = ConfigWatcher()
                await watcher.start(Path("/fake/config.yaml"))
                try:
                    await asyncio.wait_for(
                        handled.wait(), timeout=_HANDLED_TIMEOUT_SECONDS
                    )
                finally:
                    await watcher.stop()

                mock_reload.assert_called_once()

        asyncio.run(_run())

    def test_watcher_logs_warning_on_reload_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """ConfigWatcher logs a warning when reload_config returns False."""

        async def _run() -> None:
            handled = asyncio.Event()
            with (
                caplog.at_level(logging.WARNING, logger="src.web.state"),
                patch(_AWATCH_PATCH_TARGET, side_effect=_awatch_one_event(handled)),
                patch("src.web.state.reload_config", return_value=False),
            ):
                watcher = ConfigWatcher()
                await watcher.start(Path("/fake/config.yaml"))
                try:
                    await asyncio.wait_for(
                        handled.wait(), timeout=_HANDLED_TIMEOUT_SECONDS
                    )
                finally:
                    await watcher.stop()

        asyncio.run(_run())
        assert "Config hot-reload failed" in caplog.text

    def test_watcher_stop_is_idempotent(self) -> None:
        """Calling stop() when not started does not raise."""

        async def _run() -> None:
            watcher = ConfigWatcher()
            await watcher.stop()  # Should not raise

        asyncio.run(_run())

    def test_watcher_start_is_idempotent(self) -> None:
        """Calling start() twice does not create a second watcher."""

        async def _run() -> None:
            with patch(
                _AWATCH_PATCH_TARGET,
                side_effect=_fake_awatch_no_events,
            ):
                watcher = ConfigWatcher()
                await watcher.start(Path("/fake/config.yaml"))
                try:
                    assert watcher.running
                    # Second start should be a no-op
                    await watcher.start(Path("/fake/config.yaml"))
                    assert watcher.running
                finally:
                    await watcher.stop()

        asyncio.run(_run())

    def test_running_property(self) -> None:
        """running property reflects watcher state."""

        async def _run() -> None:
            with patch(
                _AWATCH_PATCH_TARGET,
                side_effect=_fake_awatch_no_events,
            ):
                watcher = ConfigWatcher()
                assert not watcher.running

                await watcher.start(Path("/fake/config.yaml"))
                assert watcher.running

                await watcher.stop()
                assert not watcher.running

        asyncio.run(_run())

    def test_watcher_recovers_after_dead_task(self) -> None:
        """start() works again after the previous task has died."""

        async def _run() -> None:
            with patch(
                _AWATCH_PATCH_TARGET,
                side_effect=_fake_awatch_raising,
            ):
                watcher = ConfigWatcher()
                await watcher.start(Path("/fake/config.yaml"))
                # Let the task crash
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not watcher.running

            # Should be able to restart with a working awatch
            with patch(
                _AWATCH_PATCH_TARGET,
                side_effect=_fake_awatch_no_events,
            ):
                await watcher.start(Path("/fake/config.yaml"))
                assert watcher.running
                await watcher.stop()

        asyncio.run(_run())

    def test_watcher_logs_crash(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unexpected exceptions in _watch are logged before the task dies."""

        async def _run() -> None:
            with (
                caplog.at_level(logging.ERROR, logger="src.web.state"),
                patch(
                    _AWATCH_PATCH_TARGET,
                    side_effect=_fake_awatch_raising,
                ),
            ):
                watcher = ConfigWatcher()
                await watcher.start(Path("/fake/config.yaml"))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await watcher.stop()

        asyncio.run(_run())
        assert "Config watcher crashed" in caplog.text

    def test_stop_after_crash_does_not_raise(self) -> None:
        """stop() on a crashed watcher does not propagate the stored exception."""

        async def _run() -> None:
            with patch(
                _AWATCH_PATCH_TARGET,
                side_effect=_fake_awatch_raising,
            ):
                watcher = ConfigWatcher()
                await watcher.start(Path("/fake/config.yaml"))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not watcher.running

                # stop() should not re-raise the OSError from the crashed task
                await watcher.stop()
                assert watcher._task is None

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# lifespan tests
# ---------------------------------------------------------------------------


class TestLifespan:
    """Tests for the FastAPI lifespan context manager."""

    def test_lifespan_starts_watcher_when_config_path_set(self) -> None:
        """lifespan starts the config watcher when config_path is present."""
        from src.web.app import lifespan

        async def _run() -> None:
            app_state.config_path = "/fake/config.yaml"
            mock_watcher = MagicMock(spec=ConfigWatcher)
            mock_watcher.start = AsyncMock()
            mock_watcher.stop = AsyncMock()
            app_state.config_watcher = mock_watcher

            async with lifespan(MagicMock()):
                pass

            mock_watcher.start.assert_awaited_once_with(Path("/fake/config.yaml"))
            mock_watcher.stop.assert_awaited_once()

        asyncio.run(_run())

    def test_lifespan_skips_start_when_no_config_path(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """lifespan does not start watcher when config_path is None."""
        from src.web.app import lifespan

        async def _run() -> None:
            app_state.config_path = None
            mock_watcher = MagicMock(spec=ConfigWatcher)
            mock_watcher.start = AsyncMock()
            mock_watcher.stop = AsyncMock()
            app_state.config_watcher = mock_watcher

            with caplog.at_level(logging.WARNING, logger="src.web.app"):
                async with lifespan(MagicMock()):
                    pass

            mock_watcher.start.assert_not_awaited()
            mock_watcher.stop.assert_awaited_once()

        asyncio.run(_run())
        assert "Config watcher not started" in caplog.text
