"""Tests for theme discovery and API endpoints."""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.storage.manager import StorageManager
from src.web.api import discover_themes, router
from src.web.state import app_state
from tests.factories import authenticated_client


def _save_state() -> dict:
    return {f.name: getattr(app_state, f.name) for f in fields(app_state)}


def _restore_state(saved: dict) -> None:
    for key, value in saved.items():
        setattr(app_state, key, value)


def _mounted_bare() -> FastAPI:
    """The router on a plain app, which is what these tests are mounting.

    The session dependency rides on the router itself, so this is as
    authenticated as the routes ``create_app`` serves — the arrangement
    ``tests/web/test_auth.py`` pins.
    """
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Create a test client with minimal state for theme endpoints."""
    original_state = _save_state()

    app_state.config = {"web": {"theme": "nord"}}
    app_state.storage = Mock(spec=StorageManager)

    with authenticated_client(_mounted_bare()) as client:
        yield client

    _restore_state(original_state)


class TestDiscoverThemes:
    """Tests for the discover_themes() function."""

    def test_returns_themes_from_valid_directories(self, tmp_path: Path) -> None:
        """Valid theme directories with theme.json are returned."""
        theme_dir = tmp_path / "alpine"
        theme_dir.mkdir()
        theme_json = {
            "name": "Alpine",
            "description": "A mountain theme",
            "author": "Test",
            "version": "1.0.0",
            "type": "dark",
        }
        (theme_dir / "theme.json").write_text(json.dumps(theme_json))

        result = discover_themes(tmp_path)

        assert len(result) == 1
        assert result[0].id == "alpine"
        assert result[0].name == "Alpine"
        assert result[0].description == "A mountain theme"
        assert result[0].author == "Test"
        assert result[0].version == "1.0.0"
        assert result[0].theme_type == "dark"

    def test_skips_directories_with_invalid_json(self, tmp_path: Path) -> None:
        """Directories with malformed theme.json are skipped."""
        theme_dir = tmp_path / "broken"
        theme_dir.mkdir()
        (theme_dir / "theme.json").write_text("not valid json {{{")

        result = discover_themes(tmp_path)

        assert result == []

    def test_sorts_themes_alphabetically(self, tmp_path: Path) -> None:
        """Themes are returned sorted by directory name."""
        for name in ["zebra", "alpha", "middle"]:
            theme_dir = tmp_path / name
            theme_dir.mkdir()
            theme_json = {
                "name": name.capitalize(),
                "description": f"The {name} theme",
                "author": "Test",
                "version": "1.0.0",
                "type": "dark",
            }
            (theme_dir / "theme.json").write_text(json.dumps(theme_json))

        result = discover_themes(tmp_path)

        assert [theme.id for theme in result] == ["alpha", "middle", "zebra"]


class TestThemeEndpoints:
    """Tests for theme API endpoints."""

    def test_list_themes_returns_builtin_themes(self, test_client: TestClient) -> None:
        """GET /api/themes returns the built-in themes."""
        response = test_client.get("/api/themes")

        assert response.status_code == 200
        themes = response.json()
        theme_ids = [theme["id"] for theme in themes]
        assert "nord" in theme_ids

    def test_get_default_theme_returns_config_value(
        self,
        test_client: TestClient,
    ) -> None:
        """GET /api/themes/default returns the configured default theme."""
        response = test_client.get("/api/themes/default")

        assert response.status_code == 200
        data = response.json()
        assert data["theme"] == "nord"

    def test_get_default_theme_falls_back_to_nord(self) -> None:
        """GET /api/themes/default falls back to nord when not configured."""
        original_state = _save_state()
        app_state.config = {"web": {}}
        app_state.storage = Mock(spec=StorageManager)

        with authenticated_client(_mounted_bare()) as client:
            response = client.get("/api/themes/default")

        _restore_state(original_state)

        assert response.status_code == 200
        assert response.json()["theme"] == "nord"
