"""Tests for the one-shot file-import service."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.ingestion.import_service import FileImportError, import_file
from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


class FakeFileImportPlugin(SourcePlugin):
    """File-import plugin that reads one title per line.

    A line equal to ``CORRUPT`` makes ``fetch`` raise ``SourceError`` so the
    corrupt-file path can be exercised.
    """

    @property
    def name(self) -> str:
        return "fake_file"

    @property
    def display_name(self) -> str:
        return "Fake File"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def is_file_import(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [ConfigField(name="content_type", field_type=str, required=True)]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        if not config.get("content_type"):
            return ["'content_type' is required"]
        return []

    def fetch(self, config: dict[str, Any], **kwargs: Any) -> Iterator[ContentItem]:
        for line in Path(config["path"]).read_text(encoding="utf-8").splitlines():
            title = line.strip()
            if not title:
                continue
            if title == "CORRUPT":
                raise SourceError(self.name, "unparseable file")
            yield ContentItem(
                title=title,
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                source=self.get_source_identifier(config),
            )


class FakeSyncablePlugin(SourcePlugin):
    """A normal (non-file-import) syncable plugin."""

    @property
    def name(self) -> str:
        return "fake_syncable"

    @property
    def display_name(self) -> str:
        return "Fake Syncable"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return []

    def fetch(self, config: dict[str, Any], **kwargs: Any) -> Iterator[ContentItem]:
        yield ContentItem(
            title="Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


@pytest.fixture()
def _registry_with_fakes() -> Iterator[None]:
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(FakeFileImportPlugin())
    registry.register(FakeSyncablePlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.mark.usefixtures("_registry_with_fakes")
class TestImportFile:
    """Tests for import_file."""

    def test_valid_file_returns_expected_counts(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """A valid file is parsed, persisted, and counted."""
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\nNeuromancer\n")

        result = import_file(
            plugin_name="fake_file",
            file_path=data_file,
            options={"content_type": "book"},
            storage_manager=storage,
        )

        assert result.items_synced == 2
        assert result.total_items == 2
        assert result.errors == []

    def test_non_file_import_plugin_rejected(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """A syncable (non-file-import) plugin cannot be imported via a file."""
        data_file = tmp_path / "x.txt"
        data_file.write_text("ignored\n")

        with pytest.raises(FileImportError, match="does not support file import"):
            import_file(
                plugin_name="fake_syncable",
                file_path=data_file,
                options={},
                storage_manager=storage,
            )

    def test_unknown_plugin_rejected(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """An unregistered plugin name raises a clear error."""
        data_file = tmp_path / "x.txt"
        data_file.write_text("ignored\n")

        with pytest.raises(FileImportError, match="Unknown plugin"):
            import_file(
                plugin_name="does_not_exist",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

    def test_missing_file_rejected(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """A path that is not a readable file raises a clear error."""
        with pytest.raises(FileImportError, match="File not found or not readable"):
            import_file(
                plugin_name="fake_file",
                file_path=tmp_path / "nope.txt",
                options={"content_type": "book"},
                storage_manager=storage,
            )

    def test_invalid_options_rejected(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """Options that fail plugin validation raise FileImportError."""
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")

        with pytest.raises(FileImportError, match="content_type"):
            import_file(
                plugin_name="fake_file",
                file_path=data_file,
                options={},
                storage_manager=storage,
            )

    def test_corrupt_file_surfaces_typed_error(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """A SourceError from fetch is wrapped in a typed FileImportError."""
        data_file = tmp_path / "books.txt"
        data_file.write_text("CORRUPT\n")

        with pytest.raises(FileImportError, match="unparseable file"):
            import_file(
                plugin_name="fake_file",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

    def test_path_option_is_injected_not_taken_from_options(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The service injects the file path; the file argument wins."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("Dune\n")

        result = import_file(
            plugin_name="fake_file",
            file_path=real_file,
            options={"content_type": "book", "path": "/bogus/ignored.txt"},
            storage_manager=storage,
        )

        assert result.items_synced == 1
