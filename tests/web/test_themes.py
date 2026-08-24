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
from src.web.api import router
from src.web.state import app_state
from src.web.themes import discover_themes
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

    app_state.config = {}
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

    def test_the_default_is_a_theme_this_install_ships(
        self, test_client: TestClient
    ) -> None:
        """Naming one it does not leaves a new user's first paint on a 404."""
        default = test_client.get("/api/themes/default").json()["theme"]

        assert default in [
            theme["id"] for theme in test_client.get("/api/themes").json()
        ]
