"""Tests for the generic JSON/JSONL import plugin.

Parsing is the importer's, and is tested next to it in
``src/ingestion/importers/generic_json/``. What is left here is the plugin's
own job: resolving a configured path and reporting what went wrong with it.
"""

import json
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
    """Tests for the file the plugin reads and the items it attributes."""

    def test_fetch_reads_the_configured_file(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        json_file = tmp_path / "books.json"
        json_file.write_text(
            json.dumps(
                [
                    {
                        "title": "The Name of the Wind",
                        "author": "Patrick Rothfuss",
                        "status": "completed",
                    }
                ]
            )
        )

        items = list(plugin.fetch({"path": str(json_file), "content_type": "book"}))

        assert len(items) == 1
        assert items[0].title == "The Name of the Wind"
        assert items[0].author == "Patrick Rothfuss"
        assert items[0].content_type == ContentType.BOOK.value
        assert items[0].status == ConsumptionStatus.COMPLETED.value
        assert items[0].source == "json_import"

    def test_fetch_attributes_items_to_the_source_id(
        self, plugin: JsonImportPlugin, tmp_path: Path
    ) -> None:
        """The id the user gave the source owns the items, not the plugin name."""
        json_file = tmp_path / "books.json"
        json_file.write_text(json.dumps([{"title": "Dune"}]))

        items = list(
            plugin.fetch(
                {
                    "path": str(json_file),
                    "content_type": "book",
                    "_source_id": "my_shelf",
                }
            )
        )

        assert [item.source for item in items] == ["my_shelf"]


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
        """What the importer refuses whole reaches the caller as a SourceError."""
        json_file = tmp_path / "bad.json"
        json_file.write_text("{not valid json")

        with pytest.raises(SourceError, match="Failed to parse JSON"):
            list(plugin.fetch({"path": str(json_file), "content_type": "book"}))


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
