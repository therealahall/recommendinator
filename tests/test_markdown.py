"""Tests for Markdown import plugin."""

from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.markdown import MarkdownImportPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> MarkdownImportPlugin:
    """Create a MarkdownImportPlugin instance."""
    return MarkdownImportPlugin()


class TestMarkdownImportPluginProperties:
    """Tests for MarkdownImportPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: MarkdownImportPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: MarkdownImportPlugin) -> None:
        assert plugin.name == "markdown_import"

    def test_display_name(self, plugin: MarkdownImportPlugin) -> None:
        assert plugin.display_name == "Markdown Import"

    def test_content_types(self, plugin: MarkdownImportPlugin) -> None:
        assert ContentType.BOOK in plugin.content_types
        assert ContentType.MOVIE in plugin.content_types
        assert ContentType.TV_SHOW in plugin.content_types
        assert ContentType.VIDEO_GAME in plugin.content_types

    def test_requires_api_key(self, plugin: MarkdownImportPlugin) -> None:
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: MarkdownImportPlugin) -> None:
        assert plugin.requires_network is False

    def test_config_schema(self, plugin: MarkdownImportPlugin) -> None:
        schema = plugin.get_config_schema()
        assert len(schema) == 2
        names = [field.name for field in schema]
        assert "markdown_path" in names
        assert "content_type" in names

    def test_get_source_identifier(self, plugin: MarkdownImportPlugin) -> None:
        assert plugin.get_source_identifier() == "markdown_import"


class TestMarkdownImportPluginValidation:
    """Tests for config validation."""

    def test_validate_valid_config(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("# Books\n")
        errors = plugin.validate_config(
            {"markdown_path": str(md_file), "content_type": "book"}
        )
        assert errors == []

    def test_validate_missing_markdown_path(self, plugin: MarkdownImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("markdown_path" in error for error in errors)

    def test_validate_nonexistent_file(self, plugin: MarkdownImportPlugin) -> None:
        errors = plugin.validate_config(
            {"markdown_path": "/nonexistent/path.md", "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_missing_content_type(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("# Books\n")
        errors = plugin.validate_config({"markdown_path": str(md_file)})
        assert any("content_type" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("# Books\n")
        errors = plugin.validate_config(
            {"markdown_path": str(md_file), "content_type": "podcast"}
        )
        assert any("Invalid content_type" in error for error in errors)


class TestMarkdownImportPluginFetch:
    """Tests for Markdown import fetch functionality."""

    def test_fetch_basic_entry(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text(
            "# My Books\n\n"
            "## Completed\n"
            "- **The Name of the Wind** by Patrick Rothfuss | Rating: 5 | Date: 2024-06-15\n"
        )

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Name of the Wind"
        assert item.author == "Patrick Rothfuss"
        assert item.content_type == ContentType.BOOK.value
        assert item.rating == 5
        assert item.status == ConsumptionStatus.COMPLETED.value
        assert item.date_completed == date(2024, 6, 15)
        assert item.source == "markdown_import"

    def test_fetch_multiple_sections(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text(
            "# My Books\n\n"
            "## Completed\n"
            "- **Book A** by Author A | Rating: 5\n"
            "- **Book B** by Author B | Rating: 4\n\n"
            "## In Progress\n"
            "- **Book C** by Author C\n\n"
            "## To Read\n"
            "- **Book D** by Author D\n"
        )

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 4
        assert items[0].title == "Book A"
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[0].rating == 5
        assert items[1].title == "Book B"
        assert items[1].status == ConsumptionStatus.COMPLETED.value
        assert items[1].rating == 4
        assert items[2].title == "Book C"
        assert items[2].status == ConsumptionStatus.CURRENTLY_CONSUMING.value
        assert items[2].rating is None
        assert items[3].title == "Book D"
        assert items[3].status == ConsumptionStatus.UNREAD.value

    def test_fetch_without_creator(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("## Completed\n" "- **Some Book** | Rating: 3\n")

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].title == "Some Book"
        assert items[0].author is None
        assert items[0].rating == 3

    def test_fetch_without_rating(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("## Completed\n" "- **Some Book** by Some Author\n")

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].rating is None

    def test_fetch_title_only(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("## To Read\n" "- **Minimal Book**\n")

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].title == "Minimal Book"
        assert items[0].author is None
        assert items[0].rating is None
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_items_before_any_section_default_to_unread(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("# My Books\n" "- **Orphaned Book** by Author\n")

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_fetch_asterisk_list_marker(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("## Completed\n" "* **Book A** by Author A | Rating: 5\n")

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].title == "Book A"

    def test_fetch_empty_file(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("")

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 0

    def test_non_matching_lines_skipped(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text(
            "# My Books\n\n"
            "Some descriptive paragraph.\n\n"
            "## Completed\n"
            "Random text here.\n"
            "- **Valid Book** by Author\n"
            "- Not a valid entry\n"
        )

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )

        assert len(items) == 1
        assert items[0].title == "Valid Book"


class TestMarkdownSectionMapping:
    """Tests for section heading to status mapping."""

    def _make_file_with_section(self, tmp_path: Path, section: str) -> Path:
        md_file = tmp_path / "data.md"
        md_file.write_text(f"## {section}\n- **Test** by Author\n")
        return md_file

    def test_section_completed(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "Completed")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].status == ConsumptionStatus.COMPLETED.value

    def test_section_in_progress(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "In Progress")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING.value

    def test_section_currently_reading(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "Currently Reading")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING.value

    def test_section_to_read(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "To Read")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_section_to_watch(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "To Watch")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "movie"})
        )
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_section_to_play(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "To Play")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "video_game"})
        )
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_section_wishlist(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "Wishlist")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_section_backlog(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "Backlog")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "video_game"})
        )
        assert items[0].status == ConsumptionStatus.UNREAD.value

    def test_unrecognized_section_keeps_previous_status(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text(
            "## Completed\n"
            "- **First** by Author\n\n"
            "## Random Section\n"
            "- **Second** by Author\n"
        )
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        # Unrecognized section keeps previous status
        assert items[1].status == ConsumptionStatus.COMPLETED.value


class TestMarkdownRating:
    """Tests for rating parsing."""

    def test_valid_rating(self, plugin: MarkdownImportPlugin, tmp_path: Path) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: 4\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].rating == 4

    def test_zero_rating_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: 0\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].rating is None

    def test_out_of_range_rating_clamped(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: 10\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].rating == 5

    def test_invalid_rating_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: abc\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].rating is None


class TestMarkdownDate:
    """Tests for date parsing."""

    def test_valid_date(self, plugin: MarkdownImportPlugin, tmp_path: Path) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Date: 2024-06-15\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].date_completed == date(2024, 6, 15)

    def test_invalid_date_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Date: not-a-date\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].date_completed is None

    def test_no_date_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** by Author\n")
        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "book"})
        )
        assert items[0].date_completed is None


class TestMarkdownErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(
        self, plugin: MarkdownImportPlugin
    ) -> None:
        with pytest.raises(SourceError, match="Markdown file not found"):
            list(
                plugin.fetch(
                    {
                        "markdown_path": "/nonexistent/file.md",
                        "content_type": "book",
                    }
                )
            )

    def test_invalid_content_type_raises_source_error(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("# Test\n")
        with pytest.raises(SourceError, match="Invalid content type"):
            list(
                plugin.fetch(
                    {
                        "markdown_path": str(md_file),
                        "content_type": "podcast",
                    }
                )
            )


class TestMarkdownContentTypes:
    """Tests for different content types."""

    def test_movie_content_type(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "movies.md"
        md_file.write_text(
            "## Completed\n" "- **Inception** by Christopher Nolan | Rating: 5\n"
        )

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "movie"})
        )

        assert len(items) == 1
        assert items[0].content_type == ContentType.MOVIE.value
        assert items[0].author == "Christopher Nolan"

    def test_tv_show_content_type(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "tv.md"
        md_file.write_text(
            "## Completed\n" "- **Breaking Bad** by Vince Gilligan | Rating: 5\n"
        )

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "tv_show"})
        )

        assert len(items) == 1
        assert items[0].content_type == ContentType.TV_SHOW.value

    def test_video_game_content_type(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "games.md"
        md_file.write_text(
            "## Completed\n" "- **The Witcher 3** by CD Projekt Red | Rating: 5\n"
        )

        items = list(
            plugin.fetch({"markdown_path": str(md_file), "content_type": "video_game"})
        )

        assert len(items) == 1
        assert items[0].content_type == ContentType.VIDEO_GAME.value


class TestMarkdownTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self) -> Path:
        return Path("templates")

    def test_books_template_parseable(
        self, plugin: MarkdownImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "markdown_path": str(templates_dir / "books.md"),
                    "content_type": "book",
                }
            )
        )
        assert len(items) == 3
        assert items[0].title == "The Name of the Wind"
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[1].status == ConsumptionStatus.CURRENTLY_CONSUMING.value
        assert items[2].status == ConsumptionStatus.UNREAD.value

    def test_movies_template_parseable(
        self, plugin: MarkdownImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "markdown_path": str(templates_dir / "movies.md"),
                    "content_type": "movie",
                }
            )
        )
        assert len(items) == 3
        assert items[0].title == "Inception"

    def test_tv_shows_template_parseable(
        self, plugin: MarkdownImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "markdown_path": str(templates_dir / "tv_shows.md"),
                    "content_type": "tv_show",
                }
            )
        )
        assert len(items) == 3
        assert items[0].title == "Breaking Bad"

    def test_video_games_template_parseable(
        self, plugin: MarkdownImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "markdown_path": str(templates_dir / "video_games.md"),
                    "content_type": "video_game",
                }
            )
        )
        assert len(items) == 3
        assert items[0].title == "The Witcher 3"
