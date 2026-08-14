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
from src.storage.manager import StorageManager
from src.utils.text import LINE_BREAKS
from tests.factories import make_item


class TestResolveMaxWorkers:
    """Unit tests for the shared max_workers resolution helper."""

    def test_override_wins_over_config(self) -> None:
        assert resolve_max_workers({"sync": {"max_workers": 9}}, override=2) == 2

    def test_override_clamps_to_floor(self) -> None:
        # Belt-and-braces: Click's IntRange already enforces this on the
        # CLI side, but the helper must remain safe if any future caller
        # passes a non-Click-validated value.
        assert resolve_max_workers({}, override=0) == 1
        assert resolve_max_workers({}, override=-5) == 1

    def test_override_clamps_to_ceiling(self) -> None:
        assert (
            resolve_max_workers({}, override=MAX_WORKERS_CEILING + 100)
            == MAX_WORKERS_CEILING
        )

    def test_config_value_used_when_no_override(self) -> None:
        assert resolve_max_workers({"sync": {"max_workers": 12}}, override=None) == 12

    def test_config_value_clamped_to_ceiling(self) -> None:
        assert (
            resolve_max_workers({"sync": {"max_workers": 9999}}, override=None)
            == MAX_WORKERS_CEILING
        )

    def test_config_value_clamped_to_floor(self) -> None:
        assert resolve_max_workers({"sync": {"max_workers": 0}}, override=None) == 1

    def test_default_used_when_config_missing(self) -> None:
        assert resolve_max_workers({}, override=None, default=6) == 6

    def test_default_used_when_config_is_none(self) -> None:
        assert resolve_max_workers(None, override=None, default=4) == 4

    def test_non_integer_config_falls_back_to_default(self) -> None:
        assert (
            resolve_max_workers(
                {"sync": {"max_workers": "banana"}}, override=None, default=4
            )
            == 4
        )

    def test_none_config_value_falls_back_to_default(self) -> None:
        assert (
            resolve_max_workers(
                {"sync": {"max_workers": None}}, override=None, default=4
            )
            == 4
        )

    def test_float_config_value_truncates(self) -> None:
        # int(7.9) = 7. Documents the cast behaviour rather than promises.
        assert (
            resolve_max_workers(
                {"sync": {"max_workers": 7.9}}, override=None, default=4
            )
            == 7
        )


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_defaults(self) -> None:
        result = SyncResult(source_name="Test")
        assert result.source_name == "Test"
        assert result.items_synced == 0
        assert result.total_items == 0
        assert result.errors == []

    def test_errors_not_shared(self) -> None:
        """Each SyncResult gets its own error list (no mutable default sharing)."""
        result_a = SyncResult(source_name="A")
        result_b = SyncResult(source_name="B")
        result_a.errors.append("oops")
        assert result_b.errors == []


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
        assert storage.save_content_item.call_count == 2

    def test_sync_records_save_errors(self) -> None:
        """Errors during save are recorded but don't stop the sync."""
        items = [make_item("Good"), make_item("Bad"), make_item("Also Good")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.side_effect = [None, ValueError("db error"), None]

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

    def test_progress_callback_called(self) -> None:
        """Progress callback receives updates during sync."""
        items = [make_item("Book 1")]
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

        # Should be called at least: initial, post-fetch, and per-item
        assert progress.call_count >= 3

    def test_fetch_error_propagates(self) -> None:
        """SourceError from plugin.fetch propagates to caller."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.side_effect = SourceError("test", "connection failed")

        storage = MagicMock(spec=StorageManager)

        with pytest.raises(SourceError, match="connection failed"):
            execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
            )

    def test_empty_source(self) -> None:
        """Sync with no items returns zero counts."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "EmptyPlugin"
        plugin.fetch.return_value = iter([])

        storage = MagicMock(spec=StorageManager)

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
        )

        assert result.items_synced == 0
        assert result.total_items == 0
        assert result.errors == []
        storage.save_content_item.assert_not_called()

    def test_plugin_config_passed_through(self) -> None:
        """Plugin receives the config dict (with injected credential callback)."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter([])

        storage = MagicMock(spec=StorageManager)
        config = {"url": "http://example.com", "api_key": "secret"}

        execute_sync(
            plugin=plugin,
            plugin_config=config,
            storage_manager=storage,
        )

        plugin.fetch.assert_called_once()
        call_args = plugin.fetch.call_args
        passed_config = call_args[0][0]
        # Original config keys are preserved
        assert passed_config["url"] == "http://example.com"
        assert passed_config["api_key"] == "secret"
        # Credential rotation callback is injected
        assert callable(passed_config["_on_credential_rotated"])


class TestExecuteMultiSourceSync:
    """Tests for execute_multi_source_sync function."""

    def test_multiple_sources(self) -> None:
        """Syncs multiple sources sequentially and returns results."""
        plugin_a = MagicMock(spec=SourcePlugin)
        plugin_a.name = "source_a"
        plugin_a.display_name = "Source A"
        plugin_a.fetch.return_value = iter([make_item("A1")])

        plugin_b = MagicMock(spec=SourcePlugin)
        plugin_b.name = "source_b"
        plugin_b.display_name = "Source B"
        plugin_b.fetch.return_value = iter([make_item("B1"), make_item("B2")])

        storage = MagicMock(spec=StorageManager)

        results = execute_multi_source_sync(
            sources=[(plugin_a, {"k": "v"}), (plugin_b, {"k": "v"})],
            storage_manager=storage,
        )

        assert len(results) == 2
        assert results[0].items_synced == 1
        assert results[1].items_synced == 2
        assert storage.save_content_item.call_count == 3

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
        error_callback = MagicMock()

        results = execute_multi_source_sync(
            sources=[(plugin_a, {}), (plugin_b, {})],
            storage_manager=storage,
            error_callback=error_callback,
        )

        assert len(results) == 2
        assert results[0].items_synced == 0
        assert results[0].errors == [remedy]
        assert results[1].items_synced == 1
        # Named, not just reported: one job covers every source, so a bare
        # message leaves the operator guessing which one to go and fix.
        error_callback.assert_called_once_with("Failing", remedy)

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

    def test_empty_sources(self) -> None:
        """Empty source list returns empty results."""
        storage = MagicMock(spec=StorageManager)

        results = execute_multi_source_sync(
            sources=[],
            storage_manager=storage,
        )

        assert results == []

    def test_max_workers_default_runs_sequentially(self) -> None:
        """Default max_workers=1 keeps the legacy sequential ordering."""
        order: list[str] = []

        def fetch_a(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
            order.append("a")
            return iter([make_item("A1")])

        def fetch_b(*_args: object, **_kwargs: object) -> Iterator[ContentItem]:
            order.append("b")
            return iter([make_item("B1")])

        plugin_a = MagicMock(spec=SourcePlugin)
        plugin_a.name = "source_a"
        plugin_a.display_name = "Source A"
        plugin_a.fetch.side_effect = fetch_a

        plugin_b = MagicMock(spec=SourcePlugin)
        plugin_b.name = "source_b"
        plugin_b.display_name = "Source B"
        plugin_b.fetch.side_effect = fetch_b

        storage = MagicMock(spec=StorageManager)

        results = execute_multi_source_sync(
            sources=[(plugin_a, {}), (plugin_b, {})],
            storage_manager=storage,
        )

        assert order == ["a", "b"]
        assert [result.source_name for result in results] == ["Source A", "Source B"]

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

    def test_max_workers_capped_to_source_count(self) -> None:
        """max_workers larger than len(sources) does not spawn extra threads."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "only"
        plugin.display_name = "Only"
        plugin.fetch.return_value = iter([make_item("Solo")])

        storage = MagicMock(spec=StorageManager)

        results = execute_multi_source_sync(
            sources=[(plugin, {})],
            storage_manager=storage,
            max_workers=99,
        )

        assert len(results) == 1
        assert results[0].items_synced == 1

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
        error_callback = MagicMock()

        results = execute_multi_source_sync(
            sources=[(plugin_a, {}), (plugin_b, {})],
            storage_manager=storage,
            error_callback=error_callback,
            max_workers=2,
        )

        assert len(results) == 2
        # Order preserved despite parallel execution
        assert results[0].source_name == "Failing"
        assert results[1].source_name == "Working"
        assert results[0].items_synced == 0
        assert results[0].errors == ["boom"]
        assert results[1].items_synced == 1
        error_callback.assert_called_once_with("Failing", "boom")

    def test_mark_for_enrichment_passed_through(self) -> None:
        """mark_for_enrichment flag is passed to execute_sync."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "test"
        plugin.display_name = "Test"
        plugin.fetch.return_value = iter([make_item("Book 1")])

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1

        results = execute_multi_source_sync(
            sources=[(plugin, {})],
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        assert len(results) == 1
        assert results[0].items_synced == 1
        # Should have called mark_item_needs_enrichment
        storage.mark_item_needs_enrichment.assert_called_once_with(1)


class TestCredentialRotationCallback:
    """Tests for credential rotation callback injection in execute_sync."""

    def test_credential_callback_injected_into_config(self) -> None:
        """Regression test: execute_sync injects _on_credential_rotated callback.

        Bug: Rotated OAuth refresh tokens from GOG/Epic were discarded during
        sync because plugins had no way to persist them.

        Fix: execute_sync creates a callback that wraps
        storage_manager.save_credential and injects it into the plugin_config
        as _on_credential_rotated. Plugins that rotate tokens call this
        callback to persist the new value.
        """
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "gog"
        plugin.display_name = "GOG"
        plugin.fetch.return_value = iter([])

        storage = MagicMock(spec=StorageManager)

        execute_sync(
            plugin=plugin,
            plugin_config={"refresh_token": "old"},
            storage_manager=storage,
        )

        # Verify the config passed to plugin.fetch has the callback
        call_args = plugin.fetch.call_args
        config_passed = call_args[0][0]
        assert "_on_credential_rotated" in config_passed
        assert callable(config_passed["_on_credential_rotated"])

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
        storage.save_credential.assert_called_once_with(
            1, "gog", "refresh_token", "new_rotated_value"
        )

    def test_credential_callback_defaults_to_user_id_1(self) -> None:
        """The callback uses user_id=1 when not explicitly passed."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "gog"
        plugin.display_name = "GOG"
        plugin.get_source_identifier.return_value = "gog"

        def capture_fetch(
            config: dict[str, Any], **kwargs: object
        ) -> Iterator[ContentItem]:
            callback = config.get("_on_credential_rotated")
            if callback:
                callback("refresh_token", "new_value")
            return iter([])

        plugin.fetch.side_effect = capture_fetch

        storage = MagicMock(spec=StorageManager)

        # Do NOT pass user_id — should default to 1
        execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
        )

        storage.save_credential.assert_called_once_with(
            1, "gog", "refresh_token", "new_value"
        )

    def test_credential_callback_uses_custom_user_id(self) -> None:
        """The callback uses the user_id parameter from execute_sync."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "epic_games"
        plugin.display_name = "Epic Games"
        plugin.get_source_identifier.return_value = "epic_games"

        def capture_fetch(
            config: dict[str, Any], **kwargs: object
        ) -> Iterator[ContentItem]:
            callback = config.get("_on_credential_rotated")
            if callback:
                callback("refresh_token", "new_value")
            return iter([])

        plugin.fetch.side_effect = capture_fetch

        storage = MagicMock(spec=StorageManager)

        execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            user_id=42,
        )

        storage.save_credential.assert_called_once_with(
            42, "epic_games", "refresh_token", "new_value"
        )

    def test_credential_callback_error_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Errors in save_credential are logged but don't crash sync."""
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
        storage.save_credential.side_effect = Exception("DB locked")

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

    def test_multi_source_sync_forwards_user_id(self) -> None:
        """execute_multi_source_sync forwards user_id to execute_sync."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "gog"
        plugin.display_name = "GOG"
        plugin.get_source_identifier.return_value = "gog"

        def capture_fetch(
            config: dict[str, Any], **kwargs: object
        ) -> Iterator[ContentItem]:
            callback = config.get("_on_credential_rotated")
            if callback:
                callback("refresh_token", "rotated_value")
            return iter([])

        plugin.fetch.side_effect = capture_fetch

        storage = MagicMock(spec=StorageManager)

        execute_multi_source_sync(
            sources=[(plugin, {"refresh_token": "old"})],
            storage_manager=storage,
            user_id=7,
        )

        storage.save_credential.assert_called_once_with(
            7, "gog", "refresh_token", "rotated_value"
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

        assert storage.get_credential(1, source_id, "refresh_token") == _ROTATED_TOKEN
        assert [item.source for item in storage.get_content_items(user_id=1)] == [
            source_id
        ]

    @pytest.mark.parametrize("source_id", ["", "0"])
    def test_a_falsy_id_does_not_fall_back_to_the_plugin_name(
        self, source_id: str, storage: StorageManager
    ) -> None:
        """The orphan the ``or`` produced: a token under ``rotating``."""
        self._sync({"_source_id": source_id, "refresh_token": "old"}, storage)

        assert storage.get_credential(1, "rotating", "refresh_token") is None

    def test_a_config_with_no_source_id_still_owns_its_token(
        self, storage: StorageManager
    ) -> None:
        """The surviving fallback, reached only without a ``_source_id`` key.

        A caller assembling its own config gets the plugin name, and the item
        agrees, because one method answers both.
        """
        self._sync({"refresh_token": "old"}, storage)

        assert storage.get_credential(1, "rotating", "refresh_token") == _ROTATED_TOKEN
        assert [item.source for item in storage.get_content_items(user_id=1)] == [
            "rotating"
        ]

    def test_an_explicit_none_source_id_is_the_same_as_no_key(
        self, storage: StorageManager
    ) -> None:
        """``get`` cannot tell the two apart, so nor may the stored owner."""
        self._sync({"_source_id": None, "refresh_token": "old"}, storage)

        assert storage.get_credential(1, "rotating", "refresh_token") == _ROTATED_TOKEN
        assert [item.source for item in storage.get_content_items(user_id=1)] == [
            "rotating"
        ]

    def test_two_ids_differing_only_in_case_keep_separate_tokens(
        self, storage: StorageManager
    ) -> None:
        """A NOCASE collation would hand one source the other's secret."""
        self._sync({"_source_id": "Work_Games", "refresh_token": "old"}, storage)

        assert storage.get_credential(1, "Work_Games", "refresh_token") == (
            _ROTATED_TOKEN
        )
        assert storage.get_credential(1, "work_games", "refresh_token") is None


class TestAPluginCannotRedirectARotatedTokenRegression:
    """Reported: a plugin could write its token under another source's id.

    Bug: ``execute_sync`` takes the credential owner from
    ``get_source_identifier``, and a three-line override returning another
    source's id redirected the write. Fix: the override is refused outright.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_the_hijacking_subclass_never_gets_as_far_as_a_sync(self) -> None:
        with pytest.raises(TypeError, match="name property"):

            class HijackingPlugin(RotatingAttributingPlugin):
                def get_source_identifier(
                    self, config: dict[str, Any] | None = None
                ) -> str:
                    return "other_source"

    def test_the_token_lands_only_under_the_source_that_rotated_it(
        self, storage: StorageManager
    ) -> None:
        execute_sync(
            plugin=RotatingAttributingPlugin(),
            plugin_config={"_source_id": "my_source", "refresh_token": "old"},
            storage_manager=storage,
        )

        assert storage.get_credential(1, "my_source", "refresh_token") == _ROTATED_TOKEN
        assert storage.get_credential(1, "other_source", "refresh_token") is None


class TestASyncLeavesAStrandedTokenWhereItIs:
    """A wrongly-attributed refresh token fails where a reconnect works.

    Sources sharing a plugin cannot be told apart, so nothing may claim a row
    filed under the plugin's own name by a release before per-source ids.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_the_sync_neither_reads_nor_moves_it(self, storage: StorageManager) -> None:
        storage.save_credential(1, "rotating", "refresh_token", "stranded-by-upgrade")

        execute_sync(
            plugin=RotatingAttributingPlugin(),
            plugin_config={"_source_id": "my_source"},
            storage_manager=storage,
        )

        assert storage.get_credential(1, "rotating", "refresh_token") == (
            "stranded-by-upgrade"
        )
        # Anchored: the sync ran to the rotation, so it had every chance at the
        # row above.
        assert storage.get_credential(1, "my_source", "refresh_token") == _ROTATED_TOKEN


class TestAutoEnrichmentHook:
    """Tests for auto-enrichment marking during sync."""

    def test_mark_for_enrichment_enabled(self) -> None:
        """Items are marked for enrichment when flag is True."""
        items = [make_item("Book 1"), make_item("Book 2")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.side_effect = [1, 2]

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        assert result.items_synced == 2
        assert storage.mark_item_needs_enrichment.call_count == 2
        storage.mark_item_needs_enrichment.assert_any_call(1)
        storage.mark_item_needs_enrichment.assert_any_call(2)

    def test_mark_for_enrichment_disabled(self) -> None:
        """Items are not marked for enrichment when flag is False."""
        items = [make_item("Book 1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=False,
        )

        assert result.items_synced == 1
        storage.mark_item_needs_enrichment.assert_not_called()

    def test_mark_for_enrichment_default_disabled(self) -> None:
        """mark_for_enrichment defaults to False."""
        items = [make_item("Book 1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = 1

        # Don't pass mark_for_enrichment - should default to False
        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
        )

        assert result.items_synced == 1
        storage.mark_item_needs_enrichment.assert_not_called()

    def test_mark_for_enrichment_error_does_not_fail_sync(self) -> None:
        """Errors from marking for enrichment don't stop the sync."""
        items = [make_item("Book 1"), make_item("Book 2")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.side_effect = [1, 2]
        storage.mark_item_needs_enrichment.side_effect = [
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
        assert storage.mark_item_needs_enrichment.call_count == 2

    def test_mark_for_enrichment_skipped_when_no_db_id(self) -> None:
        """Enrichment marking is skipped when save returns None/0."""
        items = [make_item("Book 1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.save_content_item.return_value = None  # No DB ID

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        assert result.items_synced == 1
        storage.mark_item_needs_enrichment.assert_not_called()


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
    storage.save_content_item.side_effect = save_error
    storage.mark_item_needs_enrichment.side_effect = enrich_error

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

    def test_the_client_facing_error_keeps_the_title_raw(self) -> None:
        """``result.errors`` is a JSON body ``/api/sync/status`` serves.

        An escape would reach the UI as the literal backslashes it is.
        """
        result = _sync_one_forged_title(save_error=ValueError("db error"))

        assert result.errors == [f"Failed to process '{_FORGED_TITLE}'"]

    def test_a_message_less_save_fault_still_names_its_class(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``str(TimeoutError())`` is empty, so the sink diagnosed nothing."""
        with caplog.at_level(logging.WARNING, logger="src.ingestion.sync"):
            _sync_one_forged_title(save_error=TimeoutError())

        assert [
            message for message in caplog.messages if "Failed to process" in message
        ] == [
            f"[SYNC] CSV Import: Failed to process '{_ESCAPED_TITLE}': TimeoutError: "
        ]

    def test_a_message_less_enrichment_fault_still_names_its_class(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.ingestion.sync"):
            _sync_one_forged_title(enrich_error=TimeoutError())

        assert [
            message for message in caplog.messages if "for enrichment" in message
        ] == [
            f"[SYNC] Failed to mark '{_ESCAPED_TITLE}' for enrichment: TimeoutError: "
        ]


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

    def test_a_forged_credential_key_cannot_forge_a_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "GOG"
        plugin.get_source_identifier.return_value = "gog"

        def rotate(config: dict[str, Any], **kwargs: object) -> Iterator[ContentItem]:
            config["_on_credential_rotated"]("refresh_token\nERROR | forged", "new")
            return iter([])

        plugin.fetch.side_effect = rotate
        storage = MagicMock(spec=StorageManager)

        with caplog.at_level(logging.INFO, logger="src.ingestion.sync"):
            execute_sync(plugin=plugin, plugin_config={}, storage_manager=storage)

        assert [
            message for message in caplog.messages if "rotated credential" in message
        ] == [
            "[SYNC] GOG: Persisted rotated credential 'refresh_token\\nERROR | forged'"
        ]

    def test_a_failing_source_escapes_its_name_and_keeps_the_fault_class(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The multi-source wrapper logs the plugin's own name twice."""
        plugin = MagicMock(spec=SourcePlugin)
        plugin.name = "csv\nERROR | forged"
        plugin.display_name = "CSV Import"
        plugin.fetch.side_effect = TimeoutError()
        storage = MagicMock(spec=StorageManager)

        with caplog.at_level(logging.INFO, logger="src.ingestion.sync"):
            results = execute_multi_source_sync(
                sources=[(plugin, {})], storage_manager=storage
            )

        assert [message for message in caplog.messages if "forged" in message] == [
            "[SYNC] === Starting sync for source: csv\\nERROR | forged ===",
            "[SYNC] Sync failed for csv\\nERROR | forged: TimeoutError: ",
        ]
        assert results[0].errors == ["Sync failed for csv\nERROR | forged"]


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

    def test_the_log_names_the_key_the_report_withholds(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The operator's only route from a lost item back to the bad field.

        ``result.errors`` is served to clients, so it names the item and no
        field. Nothing else would say which key was refused if the
        server-side line did not carry the codec's message whole, and
        ``docs/PLUGIN_DEVELOPMENT.md`` sends plugin authors to it.
        """
        json_path = tmp_path / "books.json"
        json_path.write_text(
            json.dumps([{"title": "Neuromancer", "isbn": {"value": "9780441569595"}}])
        )
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        with caplog.at_level(logging.WARNING, logger="src.ingestion.sync"):
            result = execute_sync(
                plugin=JsonImportPlugin(),
                plugin_config={"path": str(json_path), "content_type": "book"},
                storage_manager=storage,
            )

        assert result.errors == ["Failed to process 'Neuromancer'"]
        assert [message for message in caplog.messages if "Neuromancer" in message] == [
            f"[SYNC] {JsonImportPlugin().display_name}: Failed to process"
            " 'Neuromancer': TypeError: 'isbn': a text column cannot hold a dict"
        ]


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

    def test_json_reimport_without_ignored_field_preserves_ignore_regression(
        self, tmp_path: Path
    ) -> None:
        """A JSON entry with no ignored field does not clear the user's ignore."""
        json_path = tmp_path / "books.json"
        json_path.write_text(
            json.dumps([{"title": "Dune", "author": "Frank Herbert", "status": "read"}])
        )
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        plugin = JsonImportPlugin()

        self._sync(plugin, json_path, storage)
        db_id = self._only_item(storage).db_id
        assert db_id is not None
        assert storage.set_item_ignored(db_id, True) is True

        self._sync(plugin, json_path, storage)

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
