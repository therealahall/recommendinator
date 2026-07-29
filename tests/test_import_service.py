"""Tests for the one-shot file-import service."""

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.ingestion.import_service import (
    NO_ITEMS_WARNING,
    UNREADABLE_FILE_DETAIL,
    UNSUPPORTED_SOURCE_DETAIL,
    FileImportError,
    import_file,
    import_warning,
)
from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.ingestion.sync import SyncResult
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


class FakeFileImportPlugin(SourcePlugin):
    """File-import plugin that reads one title per line.

    Marker lines make ``fetch`` fail in a specific way so each branch of the
    service's error handling can be exercised: ``CORRUPT`` raises the
    ``SourceError`` a well-behaved plugin raises, ``DECODE`` / ``RECURSE`` /
    ``CSVFAIL`` raise the failures a plugin that misses a case lets escape, and
    ``EXPLODE`` / ``DISKFULL`` raise faults that are not about the file at all.

    Named ``fake_upload`` to match ``tests/fakes/source_plugins.py``, where
    ``fake_file`` already means the opposite thing: an ordinary syncable source
    that reads a ``path``.
    """

    @property
    def name(self) -> str:
        return "fake_upload"

    @property
    def display_name(self) -> str:
        return "Fake Upload"

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
            if title == "DECODE":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            if title == "RECURSE":
                raise RecursionError("maximum recursion depth exceeded")
            if title == "CSVFAIL":
                raise csv.Error("line contains NUL")
            if title == "EXPLODE":
                raise RuntimeError("not a file problem")
            if title == "DISKFULL":
                raise OSError(28, "No space left on device")
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
            plugin_name="fake_upload",
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
                plugin_name="fake_upload",
                file_path=tmp_path / "nope.txt",
                options={"content_type": "book"},
                storage_manager=storage,
            )

    def test_directory_rejected(self, storage: StorageManager, tmp_path: Path) -> None:
        """A directory is refused by the same gate, before any plugin runs.

        ``Path.is_file()`` is what keeps ``IsADirectoryError`` from escaping the
        pipeline as an unhandled 500 — the plugin never sees the path.
        """
        directory = tmp_path / "a_directory"
        directory.mkdir()

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=directory,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert excinfo.value.client_detail == UNREADABLE_FILE_DETAIL

    def test_invalid_options_rejected(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """Options that fail plugin validation raise FileImportError."""
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")

        with pytest.raises(FileImportError, match="content_type"):
            import_file(
                plugin_name="fake_upload",
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
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

    def test_progress_callback_reports_the_whole_import(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The pipeline's full progress sequence reaches the caller's callback.

        The exact sequence is what the web modal's progress bar and the CLI's
        "Processed n/total" line are drawn from: the initial "Fetching…" tick
        with no total, the total once the file is parsed, then one 1-based tick
        per item. Asserting only that *something* arrived would pass on the
        first tick alone, with the per-item updates silently dropped.
        """
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\nNeuromancer\n")

        calls: list[tuple[int, int | None, str | None, str | None]] = []

        def record(
            items_processed: int,
            total_items: int | None,
            current_item: str | None,
            current_source: str | None,
        ) -> None:
            calls.append((items_processed, total_items, current_item, current_source))

        import_file(
            plugin_name="fake_upload",
            file_path=data_file,
            options={"content_type": "book"},
            storage_manager=storage,
            progress_callback=record,
        )

        assert calls == [
            (0, None, "Fetching...", "Fake Upload"),
            (0, 2, None, "Fake Upload"),
            (1, 2, "Dune", "Fake Upload"),
            (2, 2, "Neuromancer", "Fake Upload"),
        ]

    def test_path_option_is_refused_rather_than_redirecting_the_import(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """``path`` is injected by the service, so a caller may never supply it.

        No file-import plugin declares ``path``, so it is refused by the same
        schema gate as any other undeclared key — an upload can never be
        pointed at an arbitrary server-side file.
        """
        real_file = tmp_path / "real.txt"
        real_file.write_text("Dune\n")

        with pytest.raises(FileImportError, match="Unknown import option") as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=real_file,
                options={"content_type": "book", "path": "/bogus/ignored.txt"},
                storage_manager=storage,
            )

        assert "path" in excinfo.value.client_detail

    def test_undeclared_option_key_is_refused_before_the_plugin_runs(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """Regression: the schema gate lived in each caller, and they disagreed.

        Reported by review: ``import_file`` passed ``options`` straight through,
        so the rule that only schema-declared keys may pass was implemented
        twice — the CLI aborted on an unknown key while the web handler silently
        dropped it, producing opposite outcomes for one request. The gate now
        lives here, so both interfaces get the refusal by construction.
        """
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book", "not_a_real_option": "x"},
                storage_manager=storage,
            )

        assert excinfo.value.client_detail == (
            "Unknown import option(s) for 'fake_upload': not_a_real_option. "
            "This source accepts: content_type."
        )

    def test_the_echoed_option_keys_are_bounded(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The refusal quotes the offending keys, but only so many and so long.

        Naming the keys is what makes the error actionable, so the reflection
        stays — but Starlette accepts up to 1000 form fields whose names may
        each be kilobytes long, and echoing all of them turned a 400 into a
        megabyte-scale amplifier for an unauthenticated caller. The rendering
        must not scale with what was sent.
        """
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")
        options = {f"{index:03d}_{'k' * 500}": "x" for index in range(40)}

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options=options,
                storage_manager=storage,
            )

        detail = excinfo.value.client_detail
        assert len(detail) < 500
        # The first key is still named — the user needs somewhere to start.
        assert "000_kkk" in detail
        assert "and 35 more" in detail

    def test_internal_pipeline_key_cannot_relabel_imported_items(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """``_source_id`` is the key the gate exists for.

        ``execute_sync`` reads ``_source_id`` from the plugin config as the
        source label for every imported item, so letting a caller set it would
        corrupt per-source attribution.
        """
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")

        with pytest.raises(FileImportError, match="_source_id"):
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book", "_source_id": "steam"},
                storage_manager=storage,
            )

    def test_file_with_no_rows_imports_cleanly_and_warns(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """A file that parses but holds no rows succeeds, carrying a warning."""
        data_file = tmp_path / "empty.txt"
        data_file.write_text("\n")

        result = import_file(
            plugin_name="fake_upload",
            file_path=data_file,
            options={"content_type": "book"},
            storage_manager=storage,
        )

        assert result.total_items == 0
        assert result.errors == []
        assert import_warning(result) == NO_ITEMS_WARNING


@pytest.mark.usefixtures("_registry_with_fakes")
class TestFileImportErrorClientDetail:
    """Every FileImportError carries a client-safe rendering of itself.

    Regression: the web handler returned ``detail=str(error)`` for everything
    except one exact message prefix, so the service's temp path and the
    plugin's raw parser text went out over HTTP. The full message is still the
    diagnostic one (the log and the CLI want it); ``client_detail`` is what may
    reach a client.
    """

    def test_missing_file_detail_omits_the_path(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        missing = tmp_path / "upload-secret.txt"

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=missing,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert str(missing) in str(excinfo.value)
        assert excinfo.value.client_detail == UNREADABLE_FILE_DETAIL
        assert str(missing) not in excinfo.value.client_detail

    def test_corrupt_file_detail_omits_the_plugin_text(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The plugin's own message describes runtime state, so it stays back."""
        data_file = tmp_path / "books.txt"
        data_file.write_text("CORRUPT\n")

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert "unparseable file" in str(excinfo.value)
        assert "unparseable file" not in excinfo.value.client_detail
        assert "fake_upload" in excinfo.value.client_detail

    def test_validation_detail_is_surfaced_verbatim(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """Validation errors describe the option schema the caller just filled in."""
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={},
                storage_manager=storage,
            )

        assert excinfo.value.client_detail == "'content_type' is required"

    @pytest.mark.parametrize(
        ("plugin_name", "expected"),
        [
            ("does_not_exist", UNSUPPORTED_SOURCE_DETAIL),
            ("fake_syncable", UNSUPPORTED_SOURCE_DETAIL),
        ],
    )
    def test_unusable_plugin_detail_is_generic(
        self,
        storage: StorageManager,
        tmp_path: Path,
        plugin_name: str,
        expected: str,
    ) -> None:
        data_file = tmp_path / "x.txt"
        data_file.write_text("ignored\n")

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name=plugin_name,
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert excinfo.value.client_detail == expected


@pytest.mark.usefixtures("_registry_with_fakes")
class TestNonSourceErrorFailures:
    """A plugin fault that is not a SourceError still yields a typed error.

    Regression: ``execute_sync`` calls ``list(plugin.fetch(...))`` bare and the
    service wrapped only ``SourceError``, so a plugin that missed a case —
    a non-UTF-8 file, deeply nested JSON, a csv module fault — produced an
    unhandled 500 with a stack trace instead of a 4xx the user can act on.
    """

    @pytest.mark.parametrize(
        "line",
        ["DECODE", "RECURSE", "CSVFAIL"],
    )
    def test_raw_failures_are_wrapped(
        self, storage: StorageManager, tmp_path: Path, line: str
    ) -> None:
        data_file = tmp_path / "books.txt"
        data_file.write_text(f"{line}\n")

        with pytest.raises(FileImportError) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert "Failed to import file with 'fake_upload'" in excinfo.value.client_detail

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("EXPLODE", RuntimeError),
            # Regression: ``OSError`` was in the wrapped tuple, so a full disk
            # or a permission fault during the storage write came back as HTTP
            # 400 "Check that the file is the export that importer expects".
            # Every importer reads through ``file_reading``, which converts a
            # read-side OSError to a SourceError at source, so nothing is lost
            # by letting the rest through as the 500 it is.
            ("DISKFULL", OSError),
        ],
    )
    def test_an_internal_fault_is_not_disguised_as_a_bad_file(
        self,
        storage: StorageManager,
        tmp_path: Path,
        line: str,
        expected: type[Exception],
    ) -> None:
        """The wrapper covers parse failures only; a real internal fault escapes.

        Reporting "check your file" for a database outage or a full disk would
        send the user chasing the wrong problem.
        """
        data_file = tmp_path / "books.txt"
        data_file.write_text(f"{line}\n")

        with pytest.raises(expected) as excinfo:
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert not isinstance(excinfo.value, FileImportError)


@pytest.mark.usefixtures("_registry_with_fakes")
class TestImportFileLeavesTheFileAlone:
    """The service never creates or deletes the file it is handed.

    The module contract says file lifecycle is the caller's: the web handler
    writes a temp file and removes it itself, while the CLI passes a real user
    path that must survive the run. Nothing else pinned that, so an ``unlink``
    added inside ``import_file`` would silently delete a user's export and the
    suite would stay green.
    """

    def test_the_file_survives_a_successful_import(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "books.txt"
        data_file.write_text("Dune\n")

        import_file(
            plugin_name="fake_upload",
            file_path=data_file,
            options={"content_type": "book"},
            storage_manager=storage,
        )

        assert data_file.read_text() == "Dune\n"

    def test_the_file_survives_a_failed_import(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        data_file = tmp_path / "books.txt"
        data_file.write_text("CORRUPT\n")

        with pytest.raises(FileImportError):
            import_file(
                plugin_name="fake_upload",
                file_path=data_file,
                options={"content_type": "book"},
                storage_manager=storage,
            )

        assert data_file.read_text() == "CORRUPT\n"

    def test_no_sibling_file_is_created(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """Nothing is written alongside the import — no cache, no copy."""
        import_dir = tmp_path / "import"
        import_dir.mkdir()
        data_file = import_dir / "books.txt"
        data_file.write_text("Dune\n")

        import_file(
            plugin_name="fake_upload",
            file_path=data_file,
            options={"content_type": "book"},
            storage_manager=storage,
        )

        assert list(import_dir.iterdir()) == [data_file]


class TestImportWarning:
    """Tests for import_warning."""

    def test_zero_items_and_no_errors_warns(self) -> None:
        """A clean parse that yielded nothing is the case the warning exists for."""
        result = SyncResult(source_name="fake_upload")

        assert import_warning(result) == NO_ITEMS_WARNING

    def test_imported_items_carry_no_warning(self) -> None:
        """A normal import that saved items is not warned about."""
        result = SyncResult(source_name="fake_upload", items_synced=2, total_items=2)

        assert import_warning(result) is None

    def test_every_row_failing_reports_errors_only(self) -> None:
        """Rows that all failed are explained by errors, so no warning is added.

        Warning here as well would double-report one outcome as both "nothing
        imported" and "every row failed", when only the latter is actionable.
        """
        result = SyncResult(
            source_name="fake_upload",
            items_synced=0,
            total_items=2,
            errors=["Failed to process 'Dune'", "Failed to process 'Neuromancer'"],
        )

        assert import_warning(result) is None
