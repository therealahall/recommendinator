"""Tests for the shared sync executor."""

import json
import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.ingestion.sources.generic_csv import CsvImportPlugin
from src.ingestion.sources.generic_json import JsonImportPlugin
from src.ingestion.sync import (
    MAX_WORKERS_CEILING,
    SyncResult,
    execute_multi_source_sync,
    execute_sync,
    resolve_max_workers,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import SavedItem, SaveOutcome, StorageManager
from src.utils.text import LINE_BREAKS
from tests.factories import make_item


class TestResolveMaxWorkers:
    """Unit tests for the shared max_workers resolution helper."""

    def test_override_wins_over_config(self) -> None:
        assert resolve_max_workers({"sync": {"max_workers": 9}}, override=2) == 2

    def test_config_value_clamped_to_ceiling(self) -> None:
        assert (
            resolve_max_workers({"sync": {"max_workers": 9999}}, override=None)
            == MAX_WORKERS_CEILING
        )

    def test_non_integer_config_falls_back_to_default(self) -> None:
        assert (
            resolve_max_workers(
                {"sync": {"max_workers": "banana"}}, override=None, default=4
            )
            == 4
        )


class TestExecuteSync:
    """Tests for execute_sync function."""

    def test_basic_sync(self) -> None:
        """Items are fetched, saved, and counted."""
        items = [make_item("Book 1"), make_item("Book 2")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)

        result = execute_sync(
            plugin=plugin,
            plugin_config={"key": "val"},
            storage_manager=storage,
        )

        assert result.source_name == "TestPlugin"
        assert result.items_synced == 2
        assert result.total_items == 2
        assert result.errors == []
        assert storage.save_content_item_outcome.call_count == 2

    def test_sync_records_save_errors(self) -> None:
        """Errors during save are recorded but don't stop the sync."""
        items = [make_item("Good"), make_item("Bad"), make_item("Also Good")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        saved = SavedItem(db_id=1, outcome=SaveOutcome.ADDED)
        storage.save_content_item_outcome.side_effect = [
            saved,
            ValueError("db error"),
            saved,
        ]

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
        )

        assert result.items_synced == 2
        assert result.total_items == 3
        assert len(result.errors) == 1
        # Item-identifying summary is safe to expose; raw exception text
        # ("db error") must NOT appear because plugin exceptions can carry
        # credential bytes.
        assert "Bad" in result.errors[0]
        assert "db error" not in result.errors[0]

    def test_progress_callback_reports_one_based_item_number(self) -> None:
        """Progress callback emits ``index + 1`` so the final iteration
        shows ``items_processed == total_items`` instead of N-1/N."""
        items = [make_item(f"Item {i}") for i in range(3)]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        progress = MagicMock()

        execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            progress_callback=progress,
        )

        # In-loop calls report (1, 3, ...), (2, 3, ...), (3, 3, ...).
        in_loop_counts = [
            call.args[0]
            for call in progress.call_args_list
            if len(call.args) >= 2 and call.args[1] == 3 and call.args[2] is not None
        ]
        assert in_loop_counts == [1, 2, 3]


class TestExecuteMultiSourceSync:
    """Tests for execute_multi_source_sync function."""

    def test_source_error_continues(self) -> None:
        """A failing source doesn't block subsequent sources.

        Its message is ours and names the setting to change, so it reaches the
        operator rather than the container log alone.
        """
        remedy = "Set verify_ssl to false if the certificate is self-signed."
        plugin_a = MagicMock(spec=SourcePlugin)
        plugin_a.name = "failing"
        plugin_a.display_name = "Failing"
        plugin_a.fetch.side_effect = SourceError("failing", remedy)

        plugin_b = MagicMock(spec=SourcePlugin)
        plugin_b.name = "working"
        plugin_b.display_name = "Working"
        plugin_b.fetch.return_value = iter([make_item("B1")])

        storage = MagicMock(spec=StorageManager)
        reported: list[SyncResult] = []

        results = execute_multi_source_sync(
            sources=[(plugin_a, {}), (plugin_b, {})],
            storage_manager=storage,
            result_callback=reported.append,
        )

        assert len(results) == 2
        assert results[0].items_synced == 0
        assert results[0].errors == [remedy]
        assert results[1].items_synced == 1
        # Named, not just reported: one job covers every source, so a bare
        # message leaves the operator guessing which one to go and fix.
        assert [(entry.source_name, entry.errors) for entry in reported] == [
            ("Failing", [remedy]),
            ("Working", []),
        ]

    def test_a_request_fault_quoting_a_key_is_still_swallowed(self) -> None:
        """The substitution exists for this: ``requests`` quotes the url.

        Steam's carries ``?key=``, so anything that is not our own wording
        stays in the log.
        """
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "steam"
        plugin.display_name = "Steam"
        plugin.fetch.side_effect = requests.ConnectionError(
            "HTTPSConnectionPool: /IPlayerService/GetOwnedGames?key=secret-key"
        )

        results = execute_multi_source_sync(
            sources=[(plugin, {})],
            storage_manager=MagicMock(spec=StorageManager),
        )

        assert results[0].errors == ["Sync failed for steam"]

    def test_max_workers_runs_sources_concurrently(self) -> None:
        """With max_workers>1, sources fetch in parallel via a thread pool."""
        thread_count = 3
        barrier = threading.Barrier(thread_count, timeout=5.0)

        def make_fetch(label: str) -> Any:
            def fetch(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
                # Each source blocks until ALL sources reach the barrier;
                # if execution were sequential, the second/third source
                # would never start and the barrier would time out.
                barrier.wait()
                return iter([make_item(f"{label}1")])

            return fetch

        plugins = []
        for index in range(thread_count):
            plugin = MagicMock(spec=SourcePlugin)
            plugin.name = f"src_{index}"
            plugin.display_name = f"Src {index}"
            plugin.fetch.side_effect = make_fetch(f"src_{index}")
            plugins.append(plugin)

        storage = MagicMock(spec=StorageManager)

        results = execute_multi_source_sync(
            sources=[(plugin, {}) for plugin in plugins],
            storage_manager=storage,
            max_workers=thread_count,
        )

        assert len(results) == thread_count
        # Result ordering matches input ordering even though fetches ran
        # concurrently and may have completed in any order.
        assert [result.source_name for result in results] == [
            f"Src {index}" for index in range(thread_count)
        ]
        assert all(result.items_synced == 1 for result in results)

    def test_parallel_isolates_per_source_failures(self) -> None:
        """A failing source under parallel execution does not break others."""
        # Both fetches block on the same barrier so we know they ran
        # concurrently — neither runs until both have started.
        barrier = threading.Barrier(2, timeout=5.0)

        def fetch_failing(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
            barrier.wait()
            raise SourceError("failing", "boom")

        def fetch_ok(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
            barrier.wait()
            return iter([make_item("ok")])

        plugin_a = MagicMock(spec=SourcePlugin)
        plugin_a.name = "failing"
        plugin_a.display_name = "Failing"
        plugin_a.fetch.side_effect = fetch_failing

        plugin_b = MagicMock(spec=SourcePlugin)
        plugin_b.name = "working"
        plugin_b.display_name = "Working"
        plugin_b.fetch.side_effect = fetch_ok

        storage = MagicMock(spec=StorageManager)
        reported: list[SyncResult] = []

        results = execute_multi_source_sync(
            sources=[(plugin_a, {}), (plugin_b, {})],
            storage_manager=storage,
            result_callback=reported.append,
            max_workers=2,
        )

        assert len(results) == 2
        # Order preserved despite parallel execution
        assert results[0].source_name == "Failing"
        assert results[1].source_name == "Working"
        assert results[0].items_synced == 0
        assert results[0].errors == ["boom"]
        assert results[1].items_synced == 1
        assert {(entry.source_name, tuple(entry.errors)) for entry in reported} == {
            ("Failing", ("boom",)),
            ("Working", ()),
        }


class TestCredentialRotationCallback:
    """Tests for credential rotation callback injection in execute_sync."""

    def test_credential_callback_calls_save_credential(self) -> None:
        """The injected callback persists credentials via storage_manager."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "gog"
        plugin.display_name = "GOG"
        # The owner is whatever the plugin says the source is called, so a
        # mocked plugin has to answer that question like a real one would.
        plugin.get_source_identifier.return_value = "gog"

        # Capture the callback by intercepting fetch
        captured_callback = None

        def capture_fetch(
            config: dict[str, Any], **kwargs: object
        ) -> Iterator[ContentItem]:
            nonlocal captured_callback
            captured_callback = config.get("_on_credential_rotated")
            if captured_callback:
                captured_callback("refresh_token", "new_rotated_value")
            return iter([])

        plugin.fetch.side_effect = capture_fetch

        storage = MagicMock(spec=StorageManager)

        execute_sync(
            plugin=plugin,
            plugin_config={"refresh_token": "old"},
            storage_manager=storage,
            user_id=1,
        )

        assert captured_callback is not None
        storage.credentials.save.assert_called_once_with(
            1, "gog", "refresh_token", "new_rotated_value"
        )

    def test_credential_callback_error_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Errors in the credential save are logged but don't crash sync."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "gog"
        plugin.display_name = "GOG"

        def capture_fetch(
            config: dict[str, Any], **kwargs: object
        ) -> Iterator[ContentItem]:
            callback = config.get("_on_credential_rotated")
            if callback:
                callback("refresh_token", "new_value")
            return iter([])

        plugin.fetch.side_effect = capture_fetch

        storage = MagicMock(spec=StorageManager)
        storage.credentials.save.side_effect = Exception("DB locked")

        with caplog.at_level(logging.WARNING, logger="src.ingestion.sync"):
            result = execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
            )

        # Sync should still succeed (0 items, no crash)
        assert result.items_synced == 0
        assert any(
            "Failed to persist rotated credential" in msg and "refresh_token" in msg
            for msg in caplog.messages
        )


_ROTATED_TOKEN = "rotated-mid-sync"


class RotatingAttributingPlugin(SourcePlugin):
    """Rotates a token and attributes an item off the same method.

    The rotating doubles in ``tests/sources/test_service.py`` yield nothing, so
    none of them can compare the owner against the id an item carries.
    """

    @property
    def name(self) -> str:
        return "rotating"

    @property
    def display_name(self) -> str:
        return "Rotating"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="refresh_token", field_type=str, required=True, sensitive=True
            )
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return []

    def fetch(
        self, config: dict[str, Any], progress_callback: Any = None
    ) -> Iterator[ContentItem]:
        config["_on_credential_rotated"]("refresh_token", _ROTATED_TOKEN)
        yield ContentItem(
            id="game_1",
            title="A Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class TestTheTokenOwnerIsTheIdTheItemsCarry:
    """Reported: a rotated token stored under an id no later lookup uses.

    Cause: ``execute_sync`` re-derived the owner as ``source_id or
    plugin.name``, so a falsy id diverged from attribution. Fix: it asks
    ``get_source_identifier``.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _sync(config: dict[str, Any], storage: StorageManager) -> None:
        execute_sync(
            plugin=RotatingAttributingPlugin(),
            plugin_config=config,
            storage_manager=storage,
        )

    @pytest.mark.parametrize("source_id", ["my-gog", "", "Wörk Games 📚", "x" * 200])
    def test_the_token_and_the_item_carry_the_same_id(
        self, source_id: str, storage: StorageManager
    ) -> None:
        self._sync({"_source_id": source_id, "refresh_token": "old"}, storage)

        assert storage.credentials.get(1, source_id, "refresh_token") == _ROTATED_TOKEN
        assert [item.source for item in storage.get_content_items(user_id=1)] == [
            source_id
        ]

    @pytest.mark.parametrize("source_id", ["", "0"])
    def test_a_falsy_id_does_not_fall_back_to_the_plugin_name(
        self, source_id: str, storage: StorageManager
    ) -> None:
        """The orphan the ``or`` produced: a token under ``rotating``."""
        self._sync({"_source_id": source_id, "refresh_token": "old"}, storage)

        assert storage.credentials.get(1, "rotating", "refresh_token") is None

    def test_a_config_with_no_source_id_still_owns_its_token(
        self, storage: StorageManager
    ) -> None:
        """The surviving fallback, reached only without a ``_source_id`` key.

        A caller assembling its own config gets the plugin name, and the item
        agrees, because one method answers both.
        """
        self._sync({"refresh_token": "old"}, storage)

        assert storage.credentials.get(1, "rotating", "refresh_token") == _ROTATED_TOKEN
        assert [item.source for item in storage.get_content_items(user_id=1)] == [
            "rotating"
        ]


class TestAPluginCannotRedirectARotatedTokenRegression:
    """Reported: a plugin could write its token under another source's id.

    Bug: ``execute_sync`` takes the credential owner from
    ``get_source_identifier``, and a three-line override returning another
    source's id redirected the write. Fix: the override is refused outright.
    """

    def test_the_hijacking_subclass_never_gets_as_far_as_a_sync(self) -> None:
        with pytest.raises(TypeError, match="name property"):

            class HijackingPlugin(RotatingAttributingPlugin):
                def get_source_identifier(
                    self, config: dict[str, Any] | None = None
                ) -> str:
                    return "other_source"


class TestASyncLeavesAStrandedTokenWhereItIs:
    """A wrongly-attributed refresh token fails where a reconnect works.

    Sources sharing a plugin cannot be told apart, so nothing may claim a row
    filed under the plugin's own name by a release before per-source ids.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_the_sync_neither_reads_nor_moves_it(self, storage: StorageManager) -> None:
        storage.credentials.save(1, "rotating", "refresh_token", "stranded-by-upgrade")

        execute_sync(
            plugin=RotatingAttributingPlugin(),
            plugin_config={"_source_id": "my_source"},
            storage_manager=storage,
        )

        assert storage.credentials.get(1, "rotating", "refresh_token") == (
            "stranded-by-upgrade"
        )
        # Anchored: the sync ran to the rotation, so it had every chance at the
        # row above.
        assert (
            storage.credentials.get(1, "my_source", "refresh_token") == _ROTATED_TOKEN
        )


class TestAutoEnrichmentHook:
    """Tests for auto-enrichment marking during sync."""

    def test_mark_for_enrichment_enabled(self) -> None:
        """Items are marked for enrichment when flag is True."""
        items = [make_item("Book 1"), make_item("Book 2")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item_outcome.side_effect = [
            SavedItem(db_id=1, outcome=SaveOutcome.ADDED),
            SavedItem(db_id=2, outcome=SaveOutcome.ADDED),
        ]

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        assert result.items_synced == 2
        assert storage.enrichment.mark_needed.call_count == 2
        storage.enrichment.mark_needed.assert_any_call(1)
        storage.enrichment.mark_needed.assert_any_call(2)

    def test_mark_for_enrichment_error_does_not_fail_sync(self) -> None:
        """Errors from marking for enrichment don't stop the sync."""
        items = [make_item("Book 1"), make_item("Book 2")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item_outcome.side_effect = [
            SavedItem(db_id=1, outcome=SaveOutcome.ADDED),
            SavedItem(db_id=2, outcome=SaveOutcome.ADDED),
        ]
        storage.enrichment.mark_needed.side_effect = [
            Exception("enrichment error"),
            None,
        ]

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        # Both items should be synced even though first enrichment marking failed
        assert result.items_synced == 2
        assert storage.enrichment.mark_needed.call_count == 2
        # The failure is reported, not just logged: nothing else shows the
        # operator that one item will never be enriched.
        assert result.errors == [
            "Saved 1 item(s) but could not queue them for enrichment"
        ]


_FORGED_TITLE = "Dune\nERROR    | src.ingestion.sync | forged"
_ESCAPED_TITLE = "Dune\\nERROR    | src.ingestion.sync | forged"


def _sync_one_forged_title(
    *,
    title: str = _FORGED_TITLE,
    save_error: Exception | None = None,
    enrich_error: Exception | None = None,
) -> SyncResult:
    """Sync one item whose title carries a whole second log entry."""
    plugin = MagicMock(spec=SourcePlugin)
    plugin.display_name = "CSV Import"
    plugin.fetch.return_value = iter([make_item(title, item_id="ext_1")])

    storage = MagicMock(spec=StorageManager)
    storage.save_content_item_outcome.return_value = SavedItem(
        db_id=1, outcome=SaveOutcome.ADDED
    )
    storage.save_content_item_outcome.side_effect = save_error
    storage.enrichment.mark_needed.side_effect = enrich_error

    return execute_sync(
        plugin=plugin,
        plugin_config={},
        storage_manager=storage,
        mark_for_enrichment=enrich_error is not None,
    )


_TITLE_SINKS = [
    pytest.param({}, "Syncing", id="syncing-the-item"),
    pytest.param(
        {"enrich_error": RuntimeError("enrichment queue is down")},
        "for enrichment",
        id="marking-for-enrichment",
    ),
    pytest.param(
        {"save_error": ValueError("db error")},
        "Failed to process",
        id="saving-the-item",
    ),
]


class TestAnImportedTitleCannotForgeALogLine:
    """Reported: every sink in the item loop interpolated a raw title.

    Bug: one CSV row forged extra log entries a sync.
    Fix: a single escaped copy per item, shared by every sink.
    """

    @pytest.mark.parametrize(("options", "wording"), _TITLE_SINKS)
    def test_the_sink_escapes_the_title_it_names(
        self,
        options: dict[str, Any],
        wording: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="src.ingestion.sync"):
            _sync_one_forged_title(**options)

        sink_lines = [message for message in caplog.messages if wording in message]
        assert len(sink_lines) == 1
        assert _ESCAPED_TITLE in sink_lines[0]
        assert _FORGED_TITLE not in caplog.text
        assert all(len(message.splitlines()) == 1 for message in caplog.messages)

    def test_the_client_facing_error_escapes_the_title_as_well(self) -> None:
        """``result.errors`` reaches a terminal, not only the web UI."""
        result = _sync_one_forged_title(save_error=ValueError("db error"))

        assert result.errors == [f"Failed to process '{_ESCAPED_TITLE}'"]


class TestTheOtherSyncSinksEscapeTheirValuesToo:
    """A title is not the only value this module logs from outside it.

    A source id is typed into ``config.yaml`` or the web source form, a
    plugin names itself, and a plugin picks the credential key it rotates.
    """

    def test_a_forged_source_id_cannot_forge_a_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "CSV Import"
        plugin.fetch.return_value = iter([])
        storage = MagicMock(spec=StorageManager)

        with caplog.at_level(logging.INFO, logger="src.ingestion.sync"):
            result = execute_sync(
                plugin=plugin,
                plugin_config={"_source_id": "books\nERROR | forged"},
                storage_manager=storage,
            )

        assert result.source_name == "Books\nerror | forged"
        assert (
            "[SYNC] Books\\nerror | forged: Found 0 items, saving..." in caplog.messages
        )
        assert all(len(message.splitlines()) == 1 for message in caplog.messages)


# ESC[2K erases the line an operator just read, so it rewrites an entry
# without breaking one.
_ANSI_ERASE_LINE = "\x1b[2K"


class TestEveryCharacterThatEndsAnEntryIsEscapedByTheSinks:
    """``\\n`` is what a forged title reaches for, not all that works.

    A reader ends an entry at any of ``LINE_BREAKS``, stops at NUL, and obeys
    a terminal control.
    """

    @pytest.mark.parametrize("breaker", [*LINE_BREAKS, "\0", _ANSI_ERASE_LINE])
    def test_a_title_carrying_it_still_reaches_the_sinks_as_one_entry(
        self, breaker: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="src.ingestion.sync"):
            _sync_one_forged_title(
                title=f"Dune{breaker}ERROR | forged",
                save_error=ValueError("db error"),
            )

        assert caplog.messages
        assert all(len(message.splitlines()) == 1 for message in caplog.messages)
        assert not any(breaker in message for message in caplog.messages)
        # Escaped, not stripped: an operator still has to be able to read it.
        assert all(
            "Dune" in message for message in caplog.messages if "forged" in message
        )


#: What ``os.fsdecode`` leaves of a ROM filename holding an undecodable byte.
_SURROGATE_TITLE = "Metr\udcffoid"
_ESCAPED_SURROGATE_TITLE = "Metr\\udcffoid"


class TestAnUndecodableTitleCannotAbortTheRunRegression:
    """Reported: a ROM named in invalid UTF-8 killed ``update``.

    Symptom: ``click.echo`` raised UnicodeEncodeError printing the warning.
    Cause: the reported error interpolated the raw title, lone surrogate and
    all. Fix: it escapes it, as the log sinks already did.
    """

    def test_the_reported_error_is_printable(self) -> None:
        result = _sync_one_forged_title(
            title=_SURROGATE_TITLE, save_error=ValueError("db error")
        )

        assert result.errors == [f"Failed to process '{_ESCAPED_SURROGATE_TITLE}'"]


class TestARefusedTextValueCostsOneRow:
    """A value no text column can hold fails its own row and nothing else.

    ``isbn`` is the one text column the import templates expose, and
    ``generic_json`` forwards whatever the file gives for it, so a
    hand-written file can hand storage an object. ``to_text`` refuses it
    rather than writing a Python repr into a fill-only column, and the
    executor's per-item guard is what keeps that refusal from taking the
    rest of the file down with it.
    """

    def test_the_rest_of_the_file_still_imports(self, tmp_path: Path) -> None:
        """The bad entry is reported, the good ones are stored."""
        json_path = tmp_path / "books.json"
        json_path.write_text(
            json.dumps(
                [
                    {"title": "Dune", "author": "Frank Herbert", "status": "read"},
                    {"title": "Neuromancer", "isbn": {"value": "9780441569595"}},
                    {"title": "Ubik", "author": "Philip K. Dick", "status": "read"},
                ]
            )
        )
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = execute_sync(
            plugin=JsonImportPlugin(),
            plugin_config={"path": str(json_path), "content_type": "book"},
            storage_manager=storage,
        )

        assert result.items_synced == 2
        assert result.errors == ["Failed to process 'Neuromancer'"]
        assert sorted(item.title for item in storage.get_content_items(user_id=1)) == [
            "Dune",
            "Ubik",
        ]

    def test_a_list_of_names_in_the_same_field_still_imports(
        self, tmp_path: Path
    ) -> None:
        """The refusal stops at containers with no name in them.

        A file listing two ISBNs is the shape a text column already flattens,
        and it has to keep doing so or the guard has cost a real import.
        """
        json_path = tmp_path / "books.json"
        json_path.write_text(
            json.dumps([{"title": "Dune", "isbn": ["9780441013593", "9780441172719"]}])
        )
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = execute_sync(
            plugin=JsonImportPlugin(),
            plugin_config={"path": str(json_path), "content_type": "book"},
            storage_manager=storage,
        )

        assert result.errors == []
        items = storage.get_content_items(user_id=1)
        assert items[0].metadata["isbn"] == "9780441013593, 9780441172719"


class TestIgnoreFlagSurvivesReimport:
    """Regression tests for a re-import un-ignoring items the user ignored.

    Bug reported: after ignoring an item in the app, re-running the sync for
    the CSV/JSON file it came from silently cleared the ignore flag, so the
    item came back as a recommendation candidate and back into the taste
    signal, even though the file said nothing about ignoring.
    Root cause: both generic importers called ``parse_boolean_field`` on a
    missing key, which returns False, so every item carried a concrete
    ``ignored=False`` that storage dutifully wrote over the user's flag.
    Fix: ``parse_ignored_field`` returns None when the column/field is absent,
    which is ContentItem's "not specified by this source" contract, and
    storage preserves the stored value.
    """

    def _sync(self, plugin: SourcePlugin, path: Path, storage: StorageManager) -> None:
        """Run one import of *path* as a book source through the real sync."""
        execute_sync(
            plugin=plugin,
            plugin_config={"path": str(path), "content_type": "book"},
            storage_manager=storage,
        )

    def _only_item(self, storage: StorageManager) -> ContentItem:
        """Return the single stored item, failing loudly if there is not one."""
        items = storage.get_content_items(user_id=1)
        assert len(items) == 1
        return items[0]

    def test_reimport_without_ignored_column_preserves_ignore_regression(
        self, tmp_path: Path
    ) -> None:
        """A CSV with no ignored column does not clear the user's ignore."""
        csv_path = tmp_path / "books.csv"
        csv_path.write_text("title,author,rating,status\nDune,Frank Herbert,5,read\n")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        plugin = CsvImportPlugin()

        self._sync(plugin, csv_path, storage)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        assert storage.set_item_ignored(db_id, True) is True

        self._sync(plugin, csv_path, storage)

        stored = self._only_item(storage)
        assert stored.db_id == db_id
        assert stored.ignored is True

    def test_explicit_ignored_false_still_clears_the_flag(self, tmp_path: Path) -> None:
        """The column is not inert: a file that says ignored=false still clears."""
        csv_path = tmp_path / "books.csv"
        csv_path.write_text("title,status,ignored\nDune,read,false\n")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        plugin = CsvImportPlugin()

        self._sync(plugin, csv_path, storage)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        storage.set_item_ignored(db_id, True)

        self._sync(plugin, csv_path, storage)

        stored = self._only_item(storage)
        assert stored.db_id == db_id
        assert stored.ignored is False

    def test_reimport_preserves_rating_review_and_status(self, tmp_path: Path) -> None:
        """A re-import never overwrites the other user-owned fields either.

        The file carries a weaker status and a different rating and review
        than the user set in the app; the stored values win.
        """
        csv_path = tmp_path / "books.csv"
        csv_path.write_text(
            "title,author,rating,status,review\n"
            "Dune,Frank Herbert,2,to-read,Imported note\n"
        )
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        plugin = CsvImportPlugin()

        self._sync(plugin, csv_path, storage)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        storage.update_item_from_ui(
            db_id=db_id, status="completed", rating=5, review="Loved it"
        )

        self._sync(plugin, csv_path, storage)

        stored = self._only_item(storage)
        assert stored.db_id == db_id
        assert stored.rating == 5
        assert stored.review == "Loved it"
        assert stored.status == ConsumptionStatus.COMPLETED


class TestASyncSaysWhatItChangedRegression:
    """Reported: two runs of ``update --source roms`` read identically.

    Symptom: the second changed nothing and still said "Updated 40 items".
    Cause: the upsert reported only a row id.
    Fix: it compares stored values and reports added/updated/unchanged.
    """

    def _import(self, csv_path: Path, storage: StorageManager) -> SyncResult:
        return execute_sync(
            plugin=CsvImportPlugin(),
            plugin_config={"path": str(csv_path), "content_type": "book"},
            storage_manager=storage,
        )

    def _library_csv(self, tmp_path: Path) -> Path:
        """Forty books, unchanged between the two runs."""
        books = [(f"Book {number}", "to-read") for number in range(1, 41)]
        rows = "".join(f"{title},{status}\n" for title, status in books)
        csv_path = tmp_path / "books.csv"
        csv_path.write_text(f"title,status\n{rows}", encoding="utf-8")
        return csv_path

    def test_running_the_same_import_again_reports_forty_unchanged(
        self, tmp_path: Path
    ) -> None:
        """The acceptance criterion: a second identical run changed nothing."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        csv_path = self._library_csv(tmp_path)
        self._import(csv_path, storage)

        result = self._import(csv_path, storage)

        assert result.items_synced == 40
        assert (result.items_added, result.items_updated, result.items_unchanged) == (
            0,
            0,
            40,
        )
