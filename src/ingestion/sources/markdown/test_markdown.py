"""Tests for Markdown import plugin."""

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.markdown.markdown import MarkdownImportPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> MarkdownImportPlugin:
    """Create a MarkdownImportPlugin instance."""
    return MarkdownImportPlugin()


class TestMarkdownImportPluginValidation:
    """Tests for config validation."""

    def test_validate_missing_markdown_path(self, plugin: MarkdownImportPlugin) -> None:
        errors = plugin.validate_config({"content_type": "book"})
        assert any("path" in error for error in errors)

    def test_validate_nonexistent_file(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        errors = plugin.validate_config(
            {"path": str(tmp_path / "missing.md"), "content_type": "book"}
        )
        assert any("not found" in error for error in errors)

    def test_validate_invalid_content_type(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("# Books\n")
        errors = plugin.validate_config(
            {"path": str(md_file), "content_type": "podcast"}
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

        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))

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

        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))

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

    def test_fetch_title_only(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text("## To Read\n" "- **Minimal Book**\n")

        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Minimal Book"
        assert items[0].author is None
        assert items[0].rating is None
        assert items[0].status == ConsumptionStatus.UNREAD.value

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

        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "Valid Book"


class TestMarkdownSectionMapping:
    """Tests for section heading to status mapping."""

    def _make_file_with_section(self, tmp_path: Path, section: str) -> Path:
        md_file = tmp_path / "data.md"
        md_file.write_text(f"## {section}\n- **Test** by Author\n")
        return md_file

    def test_section_currently_reading(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "Currently Reading")
        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING.value

    def test_section_backlog(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = self._make_file_with_section(tmp_path, "Backlog")
        items = list(plugin.fetch({"path": str(md_file), "content_type": "video_game"}))
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
        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        # Unrecognized section keeps previous status
        assert items[1].status == ConsumptionStatus.COMPLETED.value


class TestMarkdownRating:
    """Tests for rating parsing."""

    def test_zero_rating_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: 0\n")
        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))
        assert items[0].rating is None

    def test_out_of_range_rating_clamped(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: 10\n")
        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))
        assert items[0].rating == 5

    def test_invalid_rating_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Rating: abc\n")
        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))
        assert items[0].rating is None


class TestMarkdownDate:
    """Tests for date parsing."""

    def test_invalid_date_is_none(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "data.md"
        md_file.write_text("## Completed\n- **Test** | Date: not-a-date\n")
        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))
        assert items[0].date_completed is None


class TestMarkdownErrors:
    """Tests for error handling."""

    def test_file_not_found_raises_source_error(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match="Markdown file not found"):
            list(
                plugin.fetch(
                    {
                        "path": str(tmp_path / "missing.md"),
                        "content_type": "book",
                    }
                )
            )


class TestMarkdownTemplates:
    """Tests that template files are valid and can be parsed."""

    @pytest.fixture()
    def templates_dir(self, allowed_source_roots: Callable[[Path], None]) -> Path:
        """The repository templates, added to the file-import allowlist."""
        directory = Path("templates")
        allowed_source_roots(directory)
        return directory

    def test_books_template_parseable(
        self, plugin: MarkdownImportPlugin, templates_dir: Path
    ) -> None:
        items = list(
            plugin.fetch(
                {
                    "path": str(templates_dir / "books.md"),
                    "content_type": "book",
                }
            )
        )
        assert len(items) == 3
        assert items[0].title == "The Name of the Wind"
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[1].status == ConsumptionStatus.CURRENTLY_CONSUMING.value
        assert items[2].status == ConsumptionStatus.UNREAD.value


class TestMarkdownImportPathContainmentRegression:
    """Regression: source config as an arbitrary-file reader.

    Bug: ``path`` came straight from HTTP-writable source config, so any host
    file could be imported. Cause: no containment. Fix: validate and fetch
    resolve it against ``security.allowed_source_roots``.
    """

    def test_validate_refuses_a_path_outside_every_root(
        self, plugin: MarkdownImportPlugin
    ) -> None:
        errors = plugin.validate_config(
            {"path": "/etc/hostname", "content_type": "book"}
        )
        assert errors == [
            "Path is outside the allowed source roots: /etc/hostname. "
            "Add its directory to security.allowed_source_roots in config.yaml."
        ]

    def test_fetch_refuses_and_yields_nothing(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text("- **Leaked**\n")

        collected = []
        with pytest.raises(SourceError, match="outside the allowed source roots"):
            for item in plugin.fetch({"path": str(secret), "content_type": "book"}):
                collected.append(item)

        # list() would discard these, leaving the leak half of the name unproven.
        assert collected == []


MARKDOWN_LOGGER = "src.ingestion.sources.markdown.markdown"

# Parsing is line-based, so a break never survives into a title here. The
# terminal control that erases the line an operator just read does.
FORGED_TITLE = "Dune\x1b[2KImported 9999 items"
ESCAPED_TITLE = "Dune\\u001b[2KImported 9999 items"


class TestMarkdownImportLogInjectionRegression:
    """Regression: an imported list item rewrote log entries.

    Bug: the title and the date were logged raw, and neither is restricted to
    printable text. Cause: no sanitiser on this path. Fix: ``sanitize_for_log``
    at every sink.
    """

    @staticmethod
    def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == MARKDOWN_LOGGER
        ]

    def test_a_control_character_in_a_title_cannot_rewrite_a_log_entry(
        self,
        plugin: MarkdownImportPlugin,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        markdown_file = tmp_path / "books.md"
        markdown_file.write_text(f"- **{FORGED_TITLE}** | Date: yesterday\n")

        with caplog.at_level(logging.WARNING, logger=MARKDOWN_LOGGER):
            items = list(
                plugin.fetch({"path": str(markdown_file), "content_type": "book"})
            )

        # The item keeps the title it was given; only the log line is escaped.
        assert [item.title for item in items] == [FORGED_TITLE]
        assert self._messages(caplog) == [
            f"Invalid date format for '{ESCAPED_TITLE}': yesterday. "
            "Expected YYYY-MM-DD."
        ]
