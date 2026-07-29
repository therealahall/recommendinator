"""Tests for generic JSON/JSONL import plugin."""

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.generic_json.generic_json import JsonImportPlugin
from src.models.content import ConsumptionStatus, ContentType

_JSON_LOGGER = "src.ingestion.sources.generic_json.generic_json"


def _invalid_date_message(caplog: pytest.LogCaptureFixture) -> str:
    """Return the one invalid-date warning the parse emitted."""
    messages = [
        record.getMessage()
        for record in caplog.records
        if "Invalid date format" in record.getMessage()
    ]
    assert len(messages) == 1
    return messages[0]


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

    def test_is_file_import(self, plugin: JsonImportPlugin) -> None:
        assert plugin.is_file_import is True

    def test_config_schema_has_no_path(self, plugin: JsonImportPlugin) -> None:
        """Path is injected by the import service; only content_type remains."""
        names = [field.name for field in plugin.get_config_schema()]
        assert names == ["content_type"]

    def test_get_source_identifier(self, plugin: JsonImportPlugin) -> None:
        assert plugin.get_source_identifier() == "json_import"

    def test_get_info_is_file_import(self, plugin: JsonImportPlugin) -> None:
        assert plugin.get_info().is_file_import is True


class TestJsonImportPluginValidation:
    """Tests for JsonImportPlugin config validation."""

    def test_validate_valid_config(self, plugin: JsonImportPlugin) -> None:
        assert plugin.validate_config({"content_type": "book"}) == []

    def test_validate_does_not_require_path(self, plugin: JsonImportPlugin) -> None:
        """validate_config no longer requires a path — the service injects it."""
        assert (
            plugin.validate_config(
                {"path": "/nonexistent/path.json", "content_type": "book"}
            )
            == []
        )

    def test_validate_missing_content_type(self, plugin: JsonImportPlugin) -> None:
        errors = plugin.validate_config({})
        assert any("content_type" in error for error in errors)

    def test_validate_invalid_content_type(self, plugin: JsonImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "podcast"})
        assert any("Invalid content_type" in error for error in errors)

    def test_validate_accepts_any_case(self, plugin: JsonImportPlugin) -> None:
        """Every content type validates however it is cased.

        The value arrives raw from ``--option content_type=BOOK`` and from the
        multipart field on POST /api/import; only the ``--content-type`` flag
        lowercases it first, so an exact-case check split the three routes.
        """
        for content_type in ContentType:
            spelled = content_type.value.upper()
            assert plugin.validate_config({"content_type": spelled}) == [], spelled


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
        assert item.metadata["genre"] == "Fantasy"

    def test_fetch_resolves_a_mixed_case_content_type(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """``BOOK`` types the entries exactly as ``book`` does.

        Validation is only half the route: ``fetch`` resolves the option again
        on its own, so a case-sensitive lookup there would accept the config and
        then refuse the file it had just approved.
        """
        json_file = tmp_path / "books.json"
        json_file.write_text(json.dumps([{"title": "Dune", "status": "completed"}]))

        items = list(plugin.fetch({"path": str(json_file), "content_type": "BOOK"}))

        assert [item.content_type for item in items] == [ContentType.BOOK.value]

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

        items = list(plugin.fetch({"path": str(json_file), "content_type": "movie"}))

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

        items = list(plugin.fetch({"path": str(json_file), "content_type": "tv_show"}))

        assert len(items) == 1
        assert items[0].author == "Vince Gilligan"
        assert items[0].metadata["seasons_watched"] == [1, 2, 3, 4, 5]

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
            plugin.fetch({"path": str(json_file), "content_type": "video_game"})
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

    def test_fetch_empty_array(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 0

    def test_fetch_empty_file(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("")

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 0

    def test_fetch_notes_in_metadata(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        data = [{"title": "Test", "notes": "Recommended by friend"}]
        json_file.write_text(json.dumps(data))

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

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

        items = list(plugin.fetch({"path": str(jsonl_file), "content_type": "book"}))

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

        items = list(plugin.fetch({"path": str(jsonl_file), "content_type": "book"}))

        assert len(items) == 2

    def test_an_array_in_a_jsonl_file_is_read_as_an_array(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """The extension does not decide the format; the content does.

        The README promises that renaming an export does not change how it is
        read, but every other test in this file pairs ``.json`` with an array
        and ``.jsonl`` with lines — so a parser that branched on the suffix
        would satisfy all of them.
        """
        jsonl_file = tmp_path / "books.jsonl"
        jsonl_file.write_text(
            json.dumps([{"title": "Book One"}, {"title": "Book Two"}])
        )

        items = list(plugin.fetch({"path": str(jsonl_file), "content_type": "book"}))

        assert [item.title for item in items] == ["Book One", "Book Two"]

    def test_lines_in_a_json_file_are_read_as_lines(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """The mirror image: line-delimited objects under a ``.json`` name."""
        json_file = tmp_path / "books.json"
        json_file.write_text(
            json.dumps({"title": "Book One"}) + "\n" + json.dumps({"title": "Book Two"})
        )

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert [item.title for item in items] == ["Book One", "Book Two"]


class TestJsonImportBomRegression:
    """Regression: a BOM-prefixed JSON file imports instead of failing.

    Sibling of the Excel-BOM CSV defect: an editor that saves UTF-8 with a
    byte-order mark put the BOM in front of the opening bracket, and reading
    as plain ``utf-8`` made the whole file unparseable JSON. Fix: read with
    ``utf-8-sig``, which strips the BOM when present and decodes a BOM-less
    file identically.
    """

    def test_fetch_bom_prefixed_json(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "books.json"
        # Writing as utf-8-sig emits the real byte-order mark.
        json_file.write_text(
            json.dumps([{"title": "Dune", "author": "Frank Herbert"}]),
            encoding="utf-8-sig",
        )

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Dune"

    def test_fetch_bom_prefixed_jsonl(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        jsonl_file = tmp_path / "books.jsonl"
        jsonl_file.write_text(
            json.dumps({"title": "Dune"}) + "\n",
            encoding="utf-8-sig",
        )

        items = list(plugin.fetch({"path": str(jsonl_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Dune"


class TestJsonImportPluginRating:
    """Tests for rating normalization."""

    def test_integer_rating(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 4}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].rating == 4

    def test_null_rating(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": None}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].rating is None

    def test_zero_rating_is_none(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "rating": 0}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].rating is None

    def test_missing_rating_is_none(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test"}]))
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

    def test_file_not_found_raises_source_error(self, plugin: JsonImportPlugin) -> None:
        with pytest.raises(SourceError, match="JSON file not found"):
            list(
                plugin.fetch({"path": "/nonexistent/file.json", "content_type": "book"})
            )

    def test_invalid_json_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "bad.json"
        json_file.write_text("{not valid json")
        with pytest.raises(SourceError, match="Failed to parse JSON"):
            list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

    def test_invalid_jsonl_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        jsonl_file = tmp_path / "bad.jsonl"
        jsonl_file.write_text('{"title": "ok"}\nnot json\n')
        with pytest.raises(SourceError, match="Failed to parse JSON"):
            list(plugin.fetch({"path": str(jsonl_file), "content_type": "book"}))

    def test_invalid_content_type_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text("[]")
        with pytest.raises(SourceError, match="Invalid content type"):
            list(plugin.fetch({"path": str(json_file), "content_type": "podcast"}))

    def test_non_utf8_file_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """A UTF-16 export is refused as a SourceError, not a decode crash."""
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Café"}]), encoding="utf-16")

        with pytest.raises(SourceError, match="not UTF-8 text"):
            list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

    def test_deeply_nested_json_raises_source_error(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """A few KB of '[' exhausts the parser's stack; that is not a 500.

        ``RecursionError`` is not a ``ValueError``, so it slipped past the
        JSON error handling and escaped ``fetch`` unhandled.
        """
        json_file = tmp_path / "deep.json"
        json_file.write_text("[" * 20000)

        with pytest.raises(SourceError, match="nested too deeply"):
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
    def templates_dir(self) -> Path:
        return Path("templates")

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

    def test_movies_template_parseable(
        self, plugin: JsonImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "path": str(templates_dir / "movies.json"),
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
                    "path": str(templates_dir / "tv_shows.json"),
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
                    "path": str(templates_dir / "video_games.json"),
                    "content_type": "video_game",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Witcher 3"


class TestJsonImportIgnored:
    """Tests for ignored field parsing in JSON import."""

    def test_ignored_true(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "status": "completed", "ignored": True}])
        )
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is True

    def test_ignored_false(self, plugin: JsonImportPlugin, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "status": "completed", "ignored": False}])
        )
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is False

    def test_ignored_string_true(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "status": "completed", "ignored": "true"}])
        )
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is True

    def test_ignored_missing_defaults_false(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([{"title": "Test", "status": "completed"}]))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is False

    def test_ignored_null_defaults_false(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "Test", "status": "completed", "ignored": None}])
        )
        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))
        assert items[0].ignored is False


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

    def test_integer_backward_compat(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        data = [
            {
                "title": "Show",
                "creator": "Creator",
                "status": "completed",
                "seasons_watched": 5,
                "total_seasons": 5,
            }
        ]
        json_file.write_text(json.dumps(data))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "tv_show"}))
        assert items[0].metadata["seasons_watched"] == [1, 2, 3, 4, 5]

    def test_null_seasons_watched(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "data.json"
        data = [
            {
                "title": "Show",
                "creator": "Creator",
                "status": "unread",
                "seasons_watched": None,
                "total_seasons": 5,
            }
        ]
        json_file.write_text(json.dumps(data))
        items = list(plugin.fetch({"path": str(json_file), "content_type": "tv_show"}))
        # null seasons_watched becomes [], which is falsy so _build_json_metadata
        # won't include it, but the post-processing replaces it
        assert items[0].metadata.get("seasons_watched", []) == []


class TestJsonImportPluginLogInjectionRegression:
    """An entry cannot forge or bloat the invalid-date warning.

    Reported: ``POST /api/import`` accepts an arbitrary JSON file, so both
    values in the warning are attacker-chosen. Root cause: the warning
    interpolated the raw title and date field, and a JSON string may hold any
    control character, so one entry could end the record and append a forged
    one under the app's ``... | LEVEL | logger | message`` format (CWE-117). It
    fires once per bad entry, so an oversized field also let one file bury the
    whole log. Fixed by passing both values through ``sanitize_for_log``.
    """

    def test_invalid_date_warning_escapes_the_entry(
        self, plugin: JsonImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps(
                [
                    {
                        "title": "Dune\r\n2099-01-01 | ERROR | src.web.api | forged",
                        "date_completed": "not-a-date",
                    }
                ]
            )
        )

        with caplog.at_level(logging.WARNING, logger=_JSON_LOGGER):
            items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 1
        message = _invalid_date_message(caplog)
        assert "\r" not in message
        assert "\n" not in message
        assert "Dune\\r\\n2099-01-01 | ERROR | src.web.api | forged" in message

    def test_invalid_date_warning_is_length_capped(
        self, plugin: JsonImportPlugin, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text(
            json.dumps([{"title": "T" * 50_000, "date_completed": "D" * 50_000}])
        )

        with caplog.at_level(logging.WARNING, logger=_JSON_LOGGER):
            list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(_invalid_date_message(caplog)) < 600
