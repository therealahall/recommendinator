"""Tests for generic JSON/JSONL import plugin."""

import json
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.generic_json import JsonImportPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> JsonImportPlugin:
    """Create a JsonImportPlugin instance."""
    return JsonImportPlugin()


class TestJsonImportPluginProperties:
    """Tests for JsonImportPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: JsonImportPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: JsonImportPlugin) -> None:
        assert plugin.name == "json_import"

    def test_display_name(self, plugin: JsonImportPlugin) -> None:
        assert plugin.display_name == "JSON Import"

    def test_content_types(self, plugin: JsonImportPlugin) -> None:
        assert ContentType.BOOK in plugin.content_types
        assert ContentType.MOVIE in plugin.content_types
        assert ContentType.TV_SHOW in plugin.content_types
        assert ContentType.VIDEO_GAME in plugin.content_types

    def test_requires_api_key(self, plugin: JsonImportPlugin) -> None:
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: JsonImportPlugin) -> None:
        assert plugin.requires_network is False

    def test_config_schema(self, plugin: JsonImportPlugin) -> None:
        schema = plugin.get_config_schema()
        assert len(schema) == 2
        names = [field.name for field in schema]
        assert "json_path" in names
        assert "content_type" in names

    def test_get_source_identifier(self, plugin: JsonImportPlugin) -> None:
        assert plugin.get_source_identifier() == "json_import"


class TestJsonImportPluginValidation:
    """Tests for JsonImportPlugin config validation."""

    def test_validate_valid_config(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "books.json"
        json_file.write_text("[]")
        errors = plugin.validate_config(
            {"json_path": str(json_file), "content_type": "book"}
        )
        assert errors == []

    def test_validate_missing_json_path(self, plugin: JsonImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("json_path" in error for error in errors)

    def test_validate_nonexistent_file(self, plugin: JsonImportPlugin) -> None:
        errors = plugin.validate_config(
            {"json_path": "/nonexistent/path.json", "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_missing_content_type(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")
        errors = plugin.validate_config({"json_path": str(json_file)})
        assert any("content_type" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")
        errors = plugin.validate_config(
            {"json_path": str(json_file), "content_type": "podcast"}
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

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )

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
        assert item.metadata["genre"] == "Fantasy"

    def test_fetch_movie(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "movies.json"
        data = [
            {
                "title": "Inception",
                "director": "Christopher Nolan",
                "rating": 5,
                "status": "completed",
                "year": 2010,
                "runtime_minutes": 148,
                "genre": "Sci-Fi",
            }
        ]
        json_file.write_text(json.dumps(data))

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "movie"})
        )

        assert len(items) == 1
        assert items[0].title == "Inception"
        assert items[0].author == "Christopher Nolan"
        assert items[0].metadata["year"] == 2010
        assert items[0].metadata["runtime_minutes"] == 148

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

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "tv_show"})
        )

        assert len(items) == 1
        assert items[0].author == "Vince Gilligan"
        assert items[0].metadata["seasons_watched"] == 5

    def test_fetch_video_game(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "games.json"
        data = [
            {
                "title": "The Witcher 3",
                "developer": "CD Projekt Red",
                "rating": 5,
                "status": "completed",
                "platform": "PC",
                "genre": "RPG",
                "hours_played": 120,
            }
        ]
        json_file.write_text(json.dumps(data))

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "video_game"})
        )

        assert len(items) == 1
        assert items[0].author == "CD Projekt Red"
        assert items[0].metadata["platform"] == "PC"
        assert items[0].metadata["hours_played"] == 120

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

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )

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

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].title == "Valid"

    def test_fetch_empty_array(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )

        assert len(items) == 0

    def test_fetch_empty_file(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("")

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )

        assert len(items) == 0

    def test_fetch_notes_in_metadata(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        data = [{"title": "Test", "notes": "Recommended by friend"}]
        json_file.write_text(json.dumps(data))

        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )

        assert items[0].metadata["notes"] == "Recommended by friend"


class TestJsonlSupport:
    """Tests for JSONL (one object per line) format."""

    def test_fetch_jsonl(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        jsonl_file = tmp_path / "books.jsonl"
        lines = [
            json.dumps({"title": "Book One", "rating": 5, "status": "completed"}),
            json.dumps({"title": "Book Two", "rating": 4, "status": "unread"}),
        ]
        jsonl_file.write_text("\n".join(lines))

        items = list(
            plugin.fetch({"json_path": str(jsonl_file), "content_type": "book"})
        )

        assert len(items) == 2
        assert items[0].title == "Book One"
        assert items[1].title == "Book Two"

    def test_fetch_jsonl_with_blank_lines(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        jsonl_file = tmp_path / "books.jsonl"
        content = (
            json.dumps({"title": "Book One"})
            + "\n\n"
            + json.dumps({"title": "Book Two"})
            + "\n"
        )
        jsonl_file.write_text(content)

        items = list(
            plugin.fetch({"json_path": str(jsonl_file), "content_type": "book"})
        )

        assert len(items) == 2


class TestJsonImportPluginRating:
    """Tests for rating normalization."""

    def test_integer_rating(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 4}]))
        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )
        assert items[0].rating == 4

    def test_null_rating(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": None}]))
        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )
        assert items[0].rating is None

    def test_zero_rating_is_none(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 0}]))
        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )
        assert items[0].rating is None

    def test_missing_rating_is_none(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test"}]))
        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )
        assert items[0].rating is None

    def test_out_of_range_rating_clamped(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 10}]))
        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )
        assert items[0].rating == 5


class TestJsonImportPluginErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(
        self, plugin: JsonImportPlugin
    ) -> None:
        with pytest.raises(SourceError, match="JSON file not found"):
            list(
                plugin.fetch(
                    {"json_path": "/nonexistent/file.json", "content_type": "book"}
                )
            )

    def test_invalid_json_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "bad.json"
        json_file.write_text("{not valid json")
        with pytest.raises(SourceError, match="Failed to parse JSON"):
            list(
                plugin.fetch(
                    {"json_path": str(json_file), "content_type": "book"}
                )
            )

    def test_invalid_jsonl_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        jsonl_file = tmp_path / "bad.jsonl"
        jsonl_file.write_text('{"title": "ok"}\nnot json\n')
        with pytest.raises(SourceError, match="Failed to parse JSON"):
            list(
                plugin.fetch(
                    {"json_path": str(jsonl_file), "content_type": "book"}
                )
            )

    def test_invalid_content_type_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")
        with pytest.raises(SourceError, match="Invalid content type"):
            list(
                plugin.fetch(
                    {"json_path": str(json_file), "content_type": "podcast"}
                )
            )

    def test_invalid_date_does_not_crash(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "date_completed": "not-a-date"}])
        )
        items = list(
            plugin.fetch({"json_path": str(json_file), "content_type": "book"})
        )
        assert len(items) == 1
        assert items[0].date_completed is None


class TestJsonTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self) -> Path:
        return Path("templates")

    def test_books_template_parseable(
        self, plugin: JsonImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "json_path": str(templates_dir / "books.json"),
                    "content_type": "book",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"

    def test_movies_template_parseable(
        self, plugin: JsonImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "json_path": str(templates_dir / "movies.json"),
                    "content_type": "movie",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "Inception"

    def test_tv_shows_template_parseable(
        self, plugin: JsonImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "json_path": str(templates_dir / "tv_shows.json"),
                    "content_type": "tv_show",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "Breaking Bad"

    def test_video_games_template_parseable(
        self, plugin: JsonImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "json_path": str(templates_dir / "video_games.json"),
                    "content_type": "video_game",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Witcher 3"
