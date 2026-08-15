"""Tests for generic JSON/JSONL import plugin."""

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.generic_json.generic_json import JsonImportPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> JsonImportPlugin:
    """Create a JsonImportPlugin instance."""
    return JsonImportPlugin()


class TestJsonImportPluginValidation:
    """Tests for JsonImportPlugin config validation."""

    def test_validate_missing_json_path(self, plugin: JsonImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("path" in error for error in errors)

    def test_validate_nonexistent_file(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        errors = plugin.validate_config(
            {"path": str(tmp_path / "missing.json"), "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")
        errors = plugin.validate_config(
            {"path": str(json_file), "content_type": "podcast"}
        )
        assert any("Invalid content_type" in error for error in errors)


class TestJsonImportPluginFetch:
    """Tests for JSON import fetch functionality."""

    def test_fetch_basic_book(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "books.json"
        data = [
            {
                "title": "The Name of the Wind",
                "author": "Patrick Rothfuss",
                "rating": 5,
                "status": "completed",
                "date_completed": "2024-06-15",
                "review": "Great book",
                "isbn": "978-0756404741",
                "pages": 662,
                "year_published": 2007,
                "genre": "Fantasy",
            }
        ]
        json_file.write_text(json.dumps(data))

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Name of the Wind"
        assert item.author == "Patrick Rothfuss"
        assert item.content_type == ContentType.BOOK.value
        assert item.rating == 5
        assert item.status == ConsumptionStatus.COMPLETED.value
        assert item.date_completed == date(2024, 6, 15)
        assert item.review == "Great book"
        assert item.source == "json_import"
        assert item.metadata["isbn"] == "978-0756404741"
        assert item.metadata["pages"] == 662
        assert item.metadata["genres"] == ["Fantasy"]

    def test_fetch_tv_show(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "tv.json"
        data = [
            {
                "title": "Breaking Bad",
                "creator": "Vince Gilligan",
                "rating": 5,
                "status": "completed",
                "seasons_watched": 5,
                "total_seasons": 5,
            }
        ]
        json_file.write_text(json.dumps(data))

        items = list(plugin.fetch({"path": str(json_file), "content_type": "tv_show"}))

        assert len(items) == 1
        assert items[0].author == "Vince Gilligan"
        assert items[0].metadata["seasons_watched"] == [1, 2, 3, 4, 5]
        assert items[0].metadata["seasons"] == 5

    def test_fetch_video_game_keeps_list_valued_fields_as_lists(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """A JSON array under a list-valued field is kept whole.

        JSON, unlike a CSV cell, can hold several genres or platforms
        directly. The single-value form is wrapped on import, so an array
        must pass through unwrapped rather than becoming a list of one list.
        """
        json_file = tmp_path / "games.json"
        data = [
            {
                "title": "Hades",
                "developer": "Supergiant Games",
                "status": "completed",
                "platform": ["PC", "Switch"],
                "genre": ["Roguelike", "Action"],
            }
        ]
        json_file.write_text(json.dumps(data))

        items = list(
            plugin.fetch({"path": str(json_file), "content_type": "video_game"})
        )

        assert items[0].metadata["platforms"] == ["PC", "Switch"]
        assert items[0].metadata["genres"] == ["Roguelike", "Action"]

    def test_fetch_multiple_items(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "books.json"
        data = [
            {"title": "Book One", "rating": 5, "status": "completed"},
            {"title": "Book Two", "rating": 3, "status": "in_progress"},
            {"title": "Book Three", "status": "unread"},
        ]
        json_file.write_text(json.dumps(data))

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 3
        assert items[0].rating == 5
        assert items[1].rating == 3
        assert items[2].rating is None

    def test_fetch_empty_title_skipped(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        data = [
            {"title": "", "rating": 5},
            {"title": "Valid", "rating": 4},
        ]
        json_file.write_text(json.dumps(data))

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Valid"


class TestJsonlSupport:
    """Tests for JSONL (one object per line) format."""

    def test_fetch_jsonl(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        jsonl_file = tmp_path / "books.jsonl"
        lines = [
            json.dumps({"title": "Book One", "rating": 5, "status": "completed"}),
            json.dumps({"title": "Book Two", "rating": 4, "status": "unread"}),
        ]
        jsonl_file.write_text("\n".join(lines))

        items = list(plugin.fetch({"path": str(jsonl_file), "content_type": "book"}))

        assert len(items) == 2
        assert items[0].title == "Book One"
        assert items[1].title == "Book Two"


class TestJsonImportPluginRating:
    """Tests for rating normalization."""

    def test_zero_rating_is_none(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 0}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].rating is None

    def test_out_of_range_rating_clamped(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 10}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].rating == 5


class TestJsonImportPluginErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match="JSON file not found"):
            list(
                plugin.fetch(
                    {"path": str(tmp_path / "missing.json"), "content_type": "book"}
                )
            )

    def test_invalid_json_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "bad.json"
        json_file.write_text("{not valid json")
        with pytest.raises(SourceError, match="Failed to parse JSON"):
            list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

    def test_invalid_date_does_not_crash(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "date_completed": "not-a-date"}])
        )
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert len(items) == 1
        assert items[0].date_completed is None


class TestJsonTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self, allowed_source_roots: Callable[[Path], None]) -> Path:
        """The repository templates, added to the file-import allowlist."""
        directory = Path("templates")
        allowed_source_roots(directory)
        return directory

    def test_books_template_parseable(
        self, plugin: JsonImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "path": str(templates_dir / "books.json"),
                    "content_type": "book",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"


class TestJsonImportIgnored:
    """Tests for ignored field parsing in JSON import."""

    def test_ignored_field_absent_is_unspecified(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """An entry with no ignored field says nothing about the flag.

        ``ignored=None`` is the "not specified by this source" contract on
        ContentItem, which storage honours by preserving the stored value.
        """
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "status": "completed"}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is None

    def test_null_ignored_is_unspecified_regression(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """A JSON null says nothing about the flag, so the stored one stands.

        Bug reported: re-importing an exported library cleared ignore flags
        the user had set in the app. The CSV sibling reached storage with a
        stated False through a blank cell; JSON reaches it through a null.
        Root cause: an explicit null parsed as False, which is a statement
        that the item is not ignored, and storage wrote it.
        Fix: only a real true/false counts; null means unspecified.
        """
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "status": "completed", "ignored": None}])
        )
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is None


class TestJsonImportSeasonsWatched:
    """Tests for seasons_watched parsing in JSON import."""

    def test_array_of_seasons(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        data = [
            {
                "title": "Show",
                "creator": "Creator",
                "status": "completed",
                "seasons_watched": [1, 2, 5, 6],
                "total_seasons": 8,
            }
        ]
        json_file.write_text(json.dumps(data))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "tv_show"}))
        assert items[0].metadata["seasons_watched"] == [1, 2, 5, 6]


class TestJsonImportPathContainmentRegression:
    """Regression: source config as an arbitrary-file reader.

    Bug: ``path`` came straight from HTTP-writable source config, so any host
    file could be imported. Cause: no containment. Fix: validate and fetch
    resolve it against ``security.allowed_source_roots``.
    """

    def test_validate_refuses_a_path_outside_every_root(
        self, plugin: JsonImportPlugin
    ) -> None:
        errors = plugin.validate_config({"path": "/etc/hosts", "content_type": "book"})
        assert errors == [
            "Path is outside the allowed source roots: /etc/hosts. "
            "Add its directory to security.allowed_source_roots in config.yaml."
        ]

    def test_fetch_refuses_and_yields_nothing(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        secret = outside / "secret.json"
        secret.write_text(json.dumps([{"title": "Leaked"}]))

        collected = []
        with pytest.raises(SourceError, match="outside the allowed source roots"):
            for item in plugin.fetch({"path": str(secret), "content_type": "book"}):
                collected.append(item)

        # list() would discard these, leaving the leak half of the name unproven.
        assert collected == []


JSON_LOGGER = "src.ingestion.sources.generic_json.generic_json"

FORGED_TITLE = "Dune\nImported 9999 items from JSON file"
ESCAPED_TITLE = "Dune\\nImported 9999 items from JSON file"


class TestJsonImportLogInjectionRegression:
    """Regression: an imported field forged log entries.

    Bug: the title and the date were logged raw, and a JSON string carries any
    character. Cause: no sanitiser on this path. Fix: ``sanitize_for_log`` at
    every sink.
    """

    @staticmethod
    def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == JSON_LOGGER
        ]

    def test_a_newline_in_a_title_cannot_forge_a_log_entry(
        self, plugin: JsonImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        json_file = tmp_path / "books.json"
        json_file.write_text(
            json.dumps([{"title": FORGED_TITLE, "date_completed": "yesterday"}])
        )

        with caplog.at_level(logging.WARNING, logger=JSON_LOGGER):
            items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        # The item keeps the title it was given; only the log line is escaped.
        assert [item.title for item in items] == [FORGED_TITLE]
        assert self._messages(caplog) == [
            f"Invalid date format for '{ESCAPED_TITLE}': yesterday. "
            "Expected YYYY-MM-DD."
        ]
