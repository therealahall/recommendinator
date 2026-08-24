"""Tests for the shared sync executor."""

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ingestion import sync
from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.ingestion.sync import (
    MAX_REPORTED_ERRORS,
    MAX_WORKERS_CEILING,
    SyncResult,
    claim_sources,
    execute_multi_source_sync,
    execute_sync,
    resolve_max_workers,
    sync_run_recorder,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import SavedItem, SaveOutcome, StorageManager
from src.storage.schema import SyncRunDict
from src.storage.sync_runs import STALE_AFTER
from src.utils.dates import utc_now
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

    def test_a_source_failing_every_item_counts_the_omissions_outside_the_list(
        self,
    ) -> None:
        """The count is a number, not a line: a door rendering a shorter slice
        states one total instead of appending a tail under the producer's."""
        failures = MAX_REPORTED_ERRORS + 12
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(
            make_item(f"Book {index}") for index in range(failures)
        )
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item_outcome.side_effect = ValueError("db error")

        result = execute_sync(plugin=plugin, plugin_config={}, storage_manager=storage)

        assert len(result.errors) == MAX_REPORTED_ERRORS
        assert result.omitted_errors == 12
        # The list is bounded; what the run did to the library is not rounded.
        assert result.total_items == failures
        assert result.items_synced == 0

    def test_the_enrichment_advisory_is_not_crowded_out_by_item_misses(self) -> None:
        """It speaks for the whole run, so a full per-item list must not
        displace it, and it is not one of the misses the tally counts."""
        saved = SavedItem(db_id=1, outcome=SaveOutcome.ADDED)
        misses = MAX_REPORTED_ERRORS + 5
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(
            make_item(f"Book {index}") for index in range(misses + 1)
        )
        storage = MagicMock(spec=StorageManager)
        storage.save_content_item_outcome.side_effect = [
            *[ValueError("db error")] * misses,
            saved,
        ]
        storage.enrichment.mark_needed.side_effect = RuntimeError("queue down")

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        assert result.errors[-1] == (
            "Saved 1 item(s) but could not queue them for enrichment"
        )
        assert result.omitted_errors == 5

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


class TestAClaimOutlastsTheWaitAndTheSilence:
    """Reported: a claim expired before the sync it was taken for started.

    Only a plugin reporting progress used to beat, so a source queued behind
    ``max_workers`` or inside a silent fetch was reaped and taken over mid-run.
    """

    _SOURCES = ("slow", "queued")

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _stamp(moment: datetime) -> str:
        return moment.isoformat(timespec="microseconds")

    @staticmethod
    def _heartbeat_at(storage: StorageManager, source_id: str) -> str:
        with storage.connection() as conn:
            row = conn.execute(
                "SELECT heartbeat_at FROM sync_runs"
                " WHERE source_id = ? AND finished_at IS NULL",
                (source_id,),
            ).fetchone()
        return str(row["heartbeat_at"]) if row else ""

    def _strand(self, storage: StorageManager, source_id: str) -> str:
        """Age a claim past the window, as a run left unbeaten for 15 minutes."""
        gone = self._stamp(utc_now() - STALE_AFTER - timedelta(seconds=1))
        with storage.connection() as conn:
            conn.execute(
                "UPDATE sync_runs SET heartbeat_at = ? WHERE source_id = ?",
                (gone, source_id),
            )
            conn.commit()
        return gone

    def _await_beat(self, storage: StorageManager, source_id: str, aged: str) -> None:
        deadline = time.monotonic() + 5
        while (
            time.monotonic() < deadline
            and self._heartbeat_at(storage, source_id) == aged
        ):
            time.sleep(0.01)

    @staticmethod
    def _plugin(name: str, fetch: Any = None) -> MagicMock:
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = name
        plugin.display_name = name.title()
        plugin.fetch.side_effect = fetch
        plugin.fetch.return_value = iter([])
        return plugin

    def test_neither_source_can_be_taken_over_while_the_run_owns_it(
        self, storage: StorageManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sync, "HEARTBEAT_EVERY", timedelta(milliseconds=5))
        claim_sources(storage, list(self._SOURCES))
        aged = {source: self._strand(storage, source) for source in self._SOURCES}
        stolen: list[int | None] = []

        def silent_fetch(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
            """Reports no progress, and holds the only worker while it runs."""
            for source, stamp in aged.items():
                self._await_beat(storage, source, stamp)
            stolen.extend(
                storage.sync_runs.claim(1, source) for source in self._SOURCES
            )
            return iter([])

        execute_multi_source_sync(
            sources=[
                (self._plugin("slow", silent_fetch), {"_source_id": "slow"}),
                (self._plugin("queued"), {"_source_id": "queued"}),
            ],
            storage_manager=storage,
            max_workers=1,
        )

        assert stolen == [None, None]

    def test_one_heartbeat_write_that_raises_ends_no_other_sources_beat(
        self, storage: StorageManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sync, "HEARTBEAT_EVERY", timedelta(milliseconds=5))
        claim_sources(storage, list(self._SOURCES))
        aged = {source: self._strand(storage, source) for source in self._SOURCES}
        beat = storage.sync_runs.heartbeat
        refused = threading.Event()

        def refuse_first_write(user_id: int, source_id: str) -> None:
            if not refused.is_set():
                refused.set()
                raise sqlite3.OperationalError("database is locked")
            beat(user_id, source_id)

        monkeypatch.setattr(storage.sync_runs, "heartbeat", refuse_first_write)
        stolen: list[int | None] = []

        def silent_fetch(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
            for source, stamp in aged.items():
                self._await_beat(storage, source, stamp)
            stolen.extend(
                storage.sync_runs.claim(1, source) for source in self._SOURCES
            )
            return iter([])

        execute_multi_source_sync(
            sources=[
                (self._plugin("slow", silent_fetch), {"_source_id": "slow"}),
                (self._plugin("queued"), {"_source_id": "queued"}),
            ],
            storage_manager=storage,
            max_workers=1,
        )

        assert refused.is_set()
        assert stolen == [None, None]


class TestEverySyncLeavesARun:
    _SOURCE_ID = "goodreads_rss"

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def _sync(self, plugin: SourcePlugin, storage: StorageManager) -> None:
        execute_multi_source_sync(
            sources=[(plugin, {"_source_id": self._SOURCE_ID})],
            storage_manager=storage,
            result_callback=sync_run_recorder(storage),
        )

    @staticmethod
    def _plugin(items: int) -> MagicMock:
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "goodreads_rss"
        plugin.display_name = "Goodreads RSS"
        plugin.get_source_identifier.return_value = "goodreads_rss"
        plugin.fetch.return_value = iter(
            [make_item(f"Book {index}", item_id=f"b{index}") for index in range(items)]
        )
        return plugin

    def _run(self, storage: StorageManager) -> SyncRunDict:
        return storage.sync_runs.latest_per_source(1)[self._SOURCE_ID]

    def test_the_recorded_stamps_are_aware_utc(self, storage: StorageManager) -> None:
        self._sync(self._plugin(1), storage)

        run = self._run(storage)
        stamps = [
            datetime.fromisoformat(run["started_at"]),
            datetime.fromisoformat(str(run["finished_at"])),
        ]
        # Naive stamps land silently and blow up where the scheduler compares
        # them to an aware now.
        assert {stamp.utcoffset() for stamp in stamps} == {timedelta(0)}
        assert stamps[1] >= stamps[0]

    def test_each_source_of_one_run_lands_its_own_row(
        self, storage: StorageManager
    ) -> None:
        remedy = "Make the Goodreads profile public, then sync again."
        failing = self._plugin(0)
        failing.name = "calibre_web"
        failing.fetch.side_effect = SourceError("calibre_web", remedy)

        execute_multi_source_sync(
            sources=[
                (self._plugin(1), {"_source_id": self._SOURCE_ID}),
                (failing, {"_source_id": "calibre_web"}),
            ],
            storage_manager=storage,
            result_callback=sync_run_recorder(storage),
            max_workers=2,
        )

        assert {
            (run["source_id"], run["status"], tuple(run["errors"]))
            for run in storage.sync_runs.latest_per_source(1).values()
        } == {
            ("goodreads_rss", "completed", ()),
            ("calibre_web", "failed", (remedy,)),
        }

    def test_a_partial_failure_that_still_saved_items_is_recorded_completed(
        self, storage: StorageManager
    ) -> None:
        with patch.object(
            storage,
            "save_content_item_outcome",
            side_effect=[
                SavedItem(db_id=1, outcome=SaveOutcome.ADDED),
                ValueError("db"),
            ],
        ):
            self._sync(self._plugin(2), storage)

        run = self._run(storage)
        assert run["status"] == "completed"
        assert (run["items_added"], run["total_items"]) == (1, 2)
        assert run["errors"] == ["Failed to process 'Book 1'"]


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
    plugin.display_name = "CSV"
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
        plugin.display_name = "CSV"
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


class TestASyncSaysWhatItChangedRegression:
    """Reported: two runs of ``update --source roms`` read identically.

    Symptom: the second changed nothing and still said "Updated 40 items".
    Cause: the upsert reported only a row id.
    Fix: it compares stored values and reports added/updated/unchanged.
    """

    def _sync(self, storage: StorageManager) -> SyncResult:
        """Forty items, identical between the two runs."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "Roms"
        plugin.get_source_identifier.return_value = "roms"
        plugin.fetch.return_value = iter(
            [make_item(f"Game {number}", item_id=f"g{number}") for number in range(40)]
        )
        return execute_sync(plugin=plugin, plugin_config={}, storage_manager=storage)

    def test_running_the_same_sync_again_reports_forty_unchanged(
        self, tmp_path: Path
    ) -> None:
        """The acceptance criterion: a second identical run changed nothing."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._sync(storage)

        result = self._sync(storage)

        assert result.items_synced == 40
        assert (result.items_added, result.items_updated, result.items_unchanged) == (
            0,
            0,
            40,
        )

    def test_an_item_the_source_renamed_is_reported_updated(
        self, tmp_path: Path
    ) -> None:
        """The ``updated`` leg of the tally, and the id match under it. The
        plugin leaves ``source`` to the sync, and unstamped a rename lands as a
        second row beside the one holding the rating.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        def sync_titled(title: str) -> SyncResult:
            plugin = MagicMock(spec=SourcePlugin)
            plugin.display_name = "Roms"
            plugin.get_source_identifier.return_value = "roms"
            plugin.fetch.return_value = iter([make_item(title, item_id="g1")])
            return execute_sync(
                plugin=plugin, plugin_config={}, storage_manager=storage
            )

        assert sync_titled("Chrono Trigger").items_added == 1

        result = sync_titled("Chrono Trigger (USA)")

        assert (result.items_added, result.items_updated, result.items_unchanged) == (
            0,
            1,
            0,
        )
        assert [item.title for item in storage.get_content_items()] == [
            "Chrono Trigger (USA)"
        ]
