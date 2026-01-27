"""Tests for generic CSV import plugin."""

from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.generic_csv import CsvImportPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> CsvImportPlugin:
    """Create a CsvImportPlugin instance."""
    return CsvImportPlugin()


class TestCsvImportPluginProperties:
    """Tests for CsvImportPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: CsvImportPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: CsvImportPlugin) -> None:
        assert plugin.name == "csv_import"

    def test_display_name(self, plugin: CsvImportPlugin) -> None:
        assert plugin.display_name == "CSV Import"

    def test_content_types(self, plugin: CsvImportPlugin) -> None:
        assert ContentType.BOOK in plugin.content_types
        assert ContentType.MOVIE in plugin.content_types
        assert ContentType.TV_SHOW in plugin.content_types
        assert ContentType.VIDEO_GAME in plugin.content_types

    def test_requires_api_key(self, plugin: CsvImportPlugin) -> None:
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: CsvImportPlugin) -> None:
        assert plugin.requires_network is False

    def test_config_schema(self, plugin: CsvImportPlugin) -> None:
        schema = plugin.get_config_schema()
        assert len(schema) == 2
        names = [field.name for field in schema]
        assert "csv_path" in names
        assert "content_type" in names

    def test_get_source_identifier(self, plugin: CsvImportPlugin) -> None:
        assert plugin.get_source_identifier() == "csv_import"

    def test_get_info(self, plugin: CsvImportPlugin) -> None:
        info = plugin.get_info()
        assert info.name == "csv_import"
        assert info.display_name == "CSV Import"
        assert info.requires_api_key is False
        assert info.requires_network is False


class TestCsvImportPluginValidation:
    """Tests for CsvImportPlugin config validation."""

    def test_validate_valid_config(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("title\n")
        errors = plugin.validate_config(
            {"csv_path": str(csv_file), "content_type": "book"}
        )
        assert errors == []

    def test_validate_missing_csv_path(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("csv_path" in error for error in errors)

    def test_validate_empty_csv_path(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config({"csv_path": "", "content_type": "book"})
        assert any("csv_path" in error for error in errors)

    def test_validate_nonexistent_file(self, plugin: CsvImportPlugin) -> None:
        errors = plugin.validate_config(
            {"csv_path": "/nonexistent/path.csv", "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_missing_content_type(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("title\n")
        errors = plugin.validate_config({"csv_path": str(csv_file)})
        assert any("content_type" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text("title\n")
        errors = plugin.validate_config(
            {"csv_path": str(csv_file), "content_type": "podcast"}
        )
        assert any("Invalid content_type" in error for error in errors)

    def test_validate_all_content_types(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title\n")
        for content_type in ContentType:
            errors = plugin.validate_config(
                {"csv_path": str(csv_file), "content_type": content_type.value}
            )
            assert errors == [], f"Failed for content_type={content_type.value}"


class TestCsvImportPluginFetchBooks:
    """Tests for CSV import of books."""

    def test_fetch_basic_book(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status,date_completed,review,notes,isbn,pages,year_published,genre\n"
            "The Name of the Wind,Patrick Rothfuss,5,completed,2024-06-15,Great book,,978-0756404741,662,2007,Fantasy\n"
        )

        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Name of the Wind"
        assert item.author == "Patrick Rothfuss"
        assert item.content_type == ContentType.BOOK.value
        assert item.rating == 5
        assert item.status == ConsumptionStatus.COMPLETED.value
        assert item.date_completed == date(2024, 6, 15)
        assert item.review == "Great book"
        assert item.source == "csv_import"
        assert item.metadata["isbn"] == "978-0756404741"
        assert item.metadata["pages"] == "662"
        assert item.metadata["year_published"] == "2007"
        assert item.metadata["genre"] == "Fantasy"

    def test_fetch_multiple_books(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status\n"
            "Book One,Author A,5,completed\n"
            "Book Two,Author B,3,in_progress\n"
            "Book Three,Author C,,unread\n"
        )

        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))

        assert len(items) == 3
        assert items[0].title == "Book One"
        assert items[0].rating == 5
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[1].title == "Book Two"
        assert items[1].rating == 3
        assert items[1].status == ConsumptionStatus.CURRENTLY_CONSUMING.value
        assert items[2].title == "Book Three"
        assert items[2].rating is None
        assert items[2].status == ConsumptionStatus.UNREAD.value

    def test_fetch_empty_title_skipped(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status\n"
            ",Author A,5,completed\n"
            "Valid Book,Author B,4,completed\n"
        )

        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Valid Book"

    def test_fetch_notes_in_metadata(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "books.csv"
        csv_file.write_text(
            "title,author,rating,status,notes\n"
            "Test Book,Author,5,completed,Recommended by friend\n"
        )

        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))

        assert items[0].metadata["notes"] == "Recommended by friend"


class TestCsvImportPluginFetchMovies:
    """Tests for CSV import of movies."""

    def test_fetch_movie(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "movies.csv"
        csv_file.write_text(
            "title,director,rating,status,year,runtime_minutes,genre\n"
            "Inception,Christopher Nolan,5,completed,2010,148,Sci-Fi\n"
        )

        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "movie"}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Inception"
        assert item.author == "Christopher Nolan"
        assert item.content_type == ContentType.MOVIE.value
        assert item.rating == 5
        assert item.metadata["year"] == "2010"
        assert item.metadata["runtime_minutes"] == "148"
        assert item.metadata["genre"] == "Sci-Fi"


class TestCsvImportPluginFetchTvShows:
    """Tests for CSV import of TV shows."""

    def test_fetch_tv_show(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "tv_shows.csv"
        csv_file.write_text(
            "title,creator,rating,status,seasons_watched,total_seasons,year,genre\n"
            "Breaking Bad,Vince Gilligan,5,completed,5,5,2008,Drama\n"
        )

        items = list(
            plugin.fetch({"csv_path": str(csv_file), "content_type": "tv_show"})
        )

        assert len(items) == 1
        item = items[0]
        assert item.title == "Breaking Bad"
        assert item.author == "Vince Gilligan"
        assert item.content_type == ContentType.TV_SHOW.value
        assert item.metadata["seasons_watched"] == "5"
        assert item.metadata["total_seasons"] == "5"


class TestCsvImportPluginFetchVideoGames:
    """Tests for CSV import of video games."""

    def test_fetch_video_game(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "games.csv"
        csv_file.write_text(
            "title,developer,rating,status,platform,genre,hours_played\n"
            "The Witcher 3,CD Projekt Red,5,completed,PC,RPG,120\n"
        )

        items = list(
            plugin.fetch({"csv_path": str(csv_file), "content_type": "video_game"})
        )

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Witcher 3"
        assert item.author == "CD Projekt Red"
        assert item.content_type == ContentType.VIDEO_GAME.value
        assert item.metadata["platform"] == "PC"
        assert item.metadata["genre"] == "RPG"
        assert item.metadata["hours_played"] == "120"


class TestCsvImportPluginStatusMapping:
    """Tests for status string mapping."""

    def test_status_completed(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,completed\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.COMPLETED.value

    def test_status_in_progress(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,in_progress\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING.value

    def test_status_unread(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,unread\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_unknown_defaults_to_unread(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,something_else\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_empty_defaults_to_unread(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_status_wishlist(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,status\nTest,wishlist\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.UNREAD.value


class TestCsvImportPluginRating:
    """Tests for rating normalization."""

    def test_valid_ratings(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\n" "A,1\n" "B,2\n" "C,3\n" "D,4\n" "E,5\n")

        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))

        assert [item.rating for item in items] == [1, 2, 3, 4, 5]

    def test_empty_rating_is_none(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\nTest,\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].rating is None

    def test_zero_rating_is_none(self, plugin: CsvImportPlugin, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,rating\nTest,0\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert items[0].rating is None


class TestCsvImportPluginErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(self, plugin: CsvImportPlugin) -> None:
        with pytest.raises(SourceError, match="CSV file not found"):
            list(
                plugin.fetch(
                    {"csv_path": "/nonexistent/file.csv", "content_type": "book"}
                )
            )

    def test_invalid_content_type_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title\nTest\n")
        with pytest.raises(SourceError, match="Invalid content type"):
            list(plugin.fetch({"csv_path": str(csv_file), "content_type": "podcast"}))

    def test_missing_title_column_raises_source_error(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name,rating\nTest,5\n")
        with pytest.raises(SourceError, match="missing required column"):
            list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))

    def test_invalid_date_does_not_crash(
        self, plugin: CsvImportPlugin, tmp_path: Path
    ) -> None:
        """Invalid dates should warn but not crash."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("title,date_completed\nTest,not-a-date\n")
        items = list(plugin.fetch({"csv_path": str(csv_file), "content_type": "book"}))
        assert len(items) == 1
        assert items[0].date_completed is None


class TestCsvTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self) -> Path:
        return Path("templates")

    def test_books_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "books.csv").exists()

    def test_movies_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "movies.csv").exists()

    def test_tv_shows_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "tv_shows.csv").exists()

    def test_video_games_template_exists(self, templates_dir: Path) -> None:
        assert (templates_dir / "video_games.csv").exists()

    def test_books_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {"csv_path": str(templates_dir / "books.csv"), "content_type": "book"}
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"

    def test_movies_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "csv_path": str(templates_dir / "movies.csv"),
                    "content_type": "movie",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "Inception"

    def test_tv_shows_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "csv_path": str(templates_dir / "tv_shows.csv"),
                    "content_type": "tv_show",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "Breaking Bad"

    def test_video_games_template_parseable(
        self, plugin: CsvImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "csv_path": str(templates_dir / "video_games.csv"),
                    "content_type": "video_game",
                }
            )
        )
        assert len(items) == 1
        assert items[0].title == "The Witcher 3"
