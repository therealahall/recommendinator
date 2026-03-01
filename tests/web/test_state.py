"""Tests for web application state management functions."""

import logging
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.web.state import (
    AppState,
    app_state,
    get_conversation_engine,
    get_ollama_client,
    reload_config,
)


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
        mock_engine = MagicMock()
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
        mock_client = MagicMock()
        app_state.ollama_client = mock_client

        result = get_ollama_client()

        assert result is mock_client
