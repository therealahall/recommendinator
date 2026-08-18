"""Tests for the Markdown import plugin.

Parsing is the importer's, and is tested next to it in
``src/ingestion/importers/markdown/``. What is left here is the plugin's own
job: resolving a configured path and reporting what went wrong with it.
"""

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
    """Tests for the file the plugin reads and the items it attributes."""

    def test_fetch_reads_the_configured_file(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "books.md"
        md_file.write_text(
            "## Completed\n- **The Name of the Wind** by Patrick Rothfuss\n"
        )

        items = list(plugin.fetch({"path": str(md_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"
        assert items[0].author == "Patrick Rothfuss"
        assert items[0].content_type == ContentType.BOOK.value
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[0].source == "markdown_import"

    def test_fetch_attributes_items_to_the_source_id(
        self, plugin: MarkdownImportPlugin, tmp_path: Path
    ) -> None:
        """The id the user gave the source owns the items, not the plugin name."""
        md_file = tmp_path / "books.md"
        md_file.write_text("- **Dune**\n")

        items = list(
            plugin.fetch(
                {"path": str(md_file), "content_type": "book", "_source_id": "my_list"}
            )
        )

        assert [item.source for item in items] == ["my_list"]


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
