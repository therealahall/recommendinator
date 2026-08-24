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
from src.web.themes import (
    DEFAULT_THEME_ID,
    THEMES_URL,
    discover_themes,
    installed_theme_ids,
    installed_themes,
    themed_shell,
)
from tests.factories import authenticated_client, booted_web_app


def _save_state() -> dict:
    return {f.name: getattr(app_state, f.name) for f in fields(app_state)}


def _restore_state(saved: dict) -> None:
    for key, value in saved.items():
        setattr(app_state, key, value)


SHELL = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
    '<link rel="stylesheet" href="/static/dist/assets/app.css">\n'
    "</head>\n<body></body>\n</html>"
)


def _write_theme(folder: Path, theme_type: str = "dark") -> None:
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text(
        json.dumps(
            {
                "name": folder.name,
                "description": "",
                "author": "Test",
                "version": "1.0.0",
                "type": theme_type,
            }
        )
    )
    (folder / "colors.css").write_text(":root { color-scheme: dark; }")


def _dist_holding(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    (static / "dist").mkdir(parents=True)
    (static / "dist" / "index.html").write_text(SHELL, encoding="utf-8")
    return static


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

        result = discover_themes(tmp_path, THEMES_URL)

        assert len(result) == 1
        assert result[0].id == "alpine"
        assert result[0].name == "Alpine"
        assert result[0].description == "A mountain theme"
        assert result[0].author == "Test"
        assert result[0].version == "1.0.0"
        assert result[0].theme_type == "dark"
        assert result[0].css_url == f"{THEMES_URL}/alpine/colors.css"

    def test_skips_directories_with_invalid_json(self, tmp_path: Path) -> None:
        """Directories with malformed theme.json are skipped."""
        theme_dir = tmp_path / "broken"
        theme_dir.mkdir()
        (theme_dir / "theme.json").write_text("not valid json {{{")

        result = discover_themes(tmp_path, THEMES_URL)

        assert result == []

    def test_a_directory_named_to_close_the_link_tag_is_not_a_theme(
        self, tmp_path: Path
    ) -> None:
        _write_theme(tmp_path / 'x"><script>alert(1)</script>')

        assert discover_themes(tmp_path, THEMES_URL) == []

    def test_a_directory_naming_a_type_the_app_cannot_paint_is_not_a_theme(
        self, tmp_path: Path
    ) -> None:
        _write_theme(tmp_path / "sepia", theme_type='dark" onload="alert(1)')

        assert discover_themes(tmp_path, THEMES_URL) == []


class TestPrivateThemesAreInstalledLikePrivatePlugins:
    def test_a_folder_in_private_themes_is_installed_and_names_its_stylesheet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_theme(tmp_path / "midnight")
        monkeypatch.setattr("src.web.themes.PRIVATE_THEMES_DIR", tmp_path)

        installed = {theme.id: theme for theme in installed_themes()}

        assert installed["midnight"].css_url == (
            "/static/private-themes/midnight/colors.css"
        )

    def test_a_private_folder_cannot_take_the_id_of_a_shipped_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_theme(tmp_path / DEFAULT_THEME_ID, theme_type="light")
        monkeypatch.setattr("src.web.themes.PRIVATE_THEMES_DIR", tmp_path)

        shipped = [t for t in installed_themes() if t.id == DEFAULT_THEME_ID]

        assert len(shipped) == 1
        assert shipped[0].css_url == f"{THEMES_URL}/{DEFAULT_THEME_ID}/colors.css"

    def test_a_private_theme_is_one_the_doors_accept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_theme(tmp_path / "midnight")
        monkeypatch.setattr("src.web.themes.PRIVATE_THEMES_DIR", tmp_path)

        assert "midnight" in installed_theme_ids()


class TestTheShellArrivesAlreadyThemed:
    def test_the_theme_link_follows_the_bundle_it_has_to_override(self) -> None:
        html = themed_shell(SHELL, "snowstorm")

        assert (
            '<link id="theme-stylesheet" rel="stylesheet" '
            'href="/static/themes/snowstorm/colors.css">'
        ) in html
        assert html.index("theme-stylesheet") > html.index("/static/dist/assets")

    def test_the_document_element_carries_the_theme_and_its_kind(self) -> None:
        html = themed_shell(SHELL, "snowstorm")

        assert 'data-theme="snowstorm"' in html
        assert 'data-theme-type="light"' in html

    def test_a_stored_theme_that_is_no_longer_installed_paints_the_default(
        self,
    ) -> None:
        html = themed_shell(SHELL, "retired")

        assert f'href="{THEMES_URL}/{DEFAULT_THEME_ID}/colors.css"' in html

    def test_a_private_folder_named_to_inject_markup_never_reaches_the_html(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hostile = 'x"><script>alert(1)</script>'
        _write_theme(tmp_path / hostile)
        monkeypatch.setattr("src.web.themes.PRIVATE_THEMES_DIR", tmp_path)

        html = themed_shell(SHELL, hostile)

        assert "<script>" not in html
        assert f'data-theme="{DEFAULT_THEME_ID}"' in html


class TestShellRoute:
    def test_the_served_shell_carries_the_theme_the_store_holds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.web.app.STATIC_DIR", _dist_holding(tmp_path))
        storage = StorageManager(sqlite_path=tmp_path / "theme.db")
        storage.ui_settings.set_theme(1, "snowstorm")

        with booted_web_app(storage, {}) as app:
            body = TestClient(app).get("/").text

        assert 'href="/static/themes/snowstorm/colors.css"' in body
        assert 'data-theme="snowstorm"' in body

    def test_a_private_theme_is_linked_where_it_is_actually_served(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_theme(tmp_path / "midnight")
        monkeypatch.setattr("src.web.app.PRIVATE_THEMES_DIR", tmp_path)
        monkeypatch.setattr("src.web.themes.PRIVATE_THEMES_DIR", tmp_path)
        monkeypatch.setattr("src.web.app.STATIC_DIR", _dist_holding(tmp_path))
        storage = StorageManager(sqlite_path=tmp_path / "theme.db")
        storage.ui_settings.set_theme(1, "midnight")

        with booted_web_app(storage, {}) as app:
            client = TestClient(app)
            body = client.get("/").text
            midnight = next(t for t in installed_themes() if t.id == "midnight")
            served = client.get(midnight.css_url)

        assert midnight.css_url in body
        assert served.status_code == 200

    def test_the_themed_shell_carries_no_style_the_csp_would_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.web.app.STATIC_DIR", _dist_holding(tmp_path))
        storage = StorageManager(sqlite_path=tmp_path / "theme.db")
        storage.ui_settings.set_theme(1, "snowstorm")

        with booted_web_app(storage, {}) as app:
            served = TestClient(app).get("/")

        policy = served.headers["Content-Security-Policy"]
        assert "style-src 'self'" in policy
        assert "unsafe-inline" not in policy
        assert "nonce-" not in policy
        assert "<style" not in served.text

    def test_the_stylesheet_is_served_from_any_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        storage = StorageManager(sqlite_path=tmp_path / "theme.db")

        with booted_web_app(storage, {}) as app:
            default = next(t for t in installed_themes() if t.id == DEFAULT_THEME_ID)
            served = TestClient(app).get(default.css_url)

        assert served.status_code == 200


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
