"""Tests for theme discovery and API endpoints."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.api import ThemeResponse, discover_themes, router
from src.web.state import app_state


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Create a test client with minimal state for theme endpoints."""
    original_state = app_state.copy()

    app_state["config"] = {"web": {"theme": "nord"}}

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        yield client

    app_state.clear()
    app_state.update(original_state)


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

    def test_returns_empty_list_when_directory_missing(self, tmp_path: Path) -> None:
        """Non-existent directory returns empty list."""
        result = discover_themes(tmp_path / "nonexistent")

        assert result == []

    def test_skips_directories_without_theme_json(self, tmp_path: Path) -> None:
        """Directories without theme.json are ignored."""
        (tmp_path / "incomplete").mkdir()
        (tmp_path / "incomplete" / "colors.css").write_text("/* no theme.json */")

        result = discover_themes(tmp_path)

        assert result == []

    def test_skips_directories_with_invalid_json(self, tmp_path: Path) -> None:
        """Directories with malformed theme.json are skipped."""
        theme_dir = tmp_path / "broken"
        theme_dir.mkdir()
        (theme_dir / "theme.json").write_text("not valid json {{{")

        result = discover_themes(tmp_path)

        assert result == []

    def test_skips_directories_with_missing_keys(self, tmp_path: Path) -> None:
        """Directories with incomplete theme.json are skipped."""
        theme_dir = tmp_path / "partial"
        theme_dir.mkdir()
        (theme_dir / "theme.json").write_text(json.dumps({"name": "Partial"}))

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

    def test_skips_non_directory_entries(self, tmp_path: Path) -> None:
        """Files in the themes directory are ignored."""
        (tmp_path / "readme.txt").write_text("not a theme")

        result = discover_themes(tmp_path)

        assert result == []

    def test_multiple_valid_themes(self, tmp_path: Path) -> None:
        """Multiple valid themes are all returned."""
        for name, theme_type in [("dark-one", "dark"), ("light-one", "light")]:
            theme_dir = tmp_path / name
            theme_dir.mkdir()
            theme_json = {
                "name": name,
                "description": f"A {theme_type} theme",
                "author": "Test",
                "version": "1.0.0",
                "type": theme_type,
            }
            (theme_dir / "theme.json").write_text(json.dumps(theme_json))

        result = discover_themes(tmp_path)

        assert len(result) == 2


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
        original_state = app_state.copy()
        app_state["config"] = {"web": {}}

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get("/api/themes/default")

        app_state.clear()
        app_state.update(original_state)

        assert response.status_code == 200
        assert response.json()["theme"] == "nord"

    def test_theme_response_model_fields(self) -> None:
        """ThemeResponse model has the expected fields."""
        theme = ThemeResponse(
            id="test",
            name="Test",
            description="A test theme",
            author="Test Author",
            version="1.0.0",
            theme_type="dark",
        )
        assert theme.id == "test"
        assert theme.theme_type == "dark"
