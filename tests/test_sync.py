"""Tests for the shared sync executor."""

import logging
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sync import (
    MAX_WORKERS_CEILING,
    SyncResult,
    execute_multi_source_sync,
    execute_sync,
    resolve_max_workers,
)
from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ContentItem
from src.storage.manager import StorageManager
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

    def test_sync_with_embeddings(self) -> None:
        """Embeddings are generated when enabled."""
        items = [make_item("Book 1", item_id="ext_1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.has_embedding.return_value = False
        embedding_gen = MagicMock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding.return_value = [0.1, 0.2]

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            embedding_generator=embedding_gen,
            use_embeddings=True,
        )

        assert result.items_synced == 1
        storage.has_embedding.assert_called_once_with("ext_1")
        embedding_gen.generate_content_embedding.assert_called_once()
        storage.save_content_item.assert_called_once_with(
            items[0], embedding=[0.1, 0.2]
        )

    def test_sync_skips_existing_embeddings(self) -> None:
        """Embeddings are not regenerated for items that already have one."""
        items = [make_item("Book 1", item_id="ext_1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.has_embedding.return_value = True
        embedding_gen = MagicMock(spec=EmbeddingGenerator)

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            embedding_generator=embedding_gen,
            use_embeddings=True,
        )

        assert result.items_synced == 1
        storage.has_embedding.assert_called_once_with("ext_1")
        embedding_gen.generate_content_embedding.assert_not_called()
        storage.save_content_item.assert_called_once_with(items[0], embedding=None)

    def test_sync_generates_embedding_for_items_without_external_id(self) -> None:
        """Items without external IDs always get embeddings (can't check existence)."""
        items = [make_item("Book 1")]  # id=None
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        embedding_gen = MagicMock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding.return_value = [0.1, 0.2]

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            embedding_generator=embedding_gen,
            use_embeddings=True,
        )

        assert result.items_synced == 1
        storage.has_embedding.assert_not_called()
        embedding_gen.generate_content_embedding.assert_called_once()
        storage.save_content_item.assert_called_once_with(
            items[0], embedding=[0.1, 0.2]
        )

    def test_sync_without_embeddings(self) -> None:
        """Embedding generator is not called when use_embeddings is False."""
        items = [make_item("Book 1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        embedding_gen = MagicMock(spec=EmbeddingGenerator)

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            embedding_generator=embedding_gen,
            use_embeddings=False,
        )

        assert result.items_synced == 1
        embedding_gen.generate_content_embedding.assert_not_called()
        storage.save_content_item.assert_called_once_with(items[0], embedding=None)

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
        assert "Bad" in result.errors[0]

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
        """A failing source doesn't block subsequent sources."""
        plugin_a = MagicMock(spec=SourcePlugin)
        plugin_a.name = "failing"
        plugin_a.display_name = "Failing"
        plugin_a.fetch.side_effect = SourceError("failing", "boom")

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
        assert len(results[0].errors) == 1
        assert "boom" in results[0].errors[0]
        assert results[1].items_synced == 1
        error_callback.assert_called_once()
        (callback_message,), _ = error_callback.call_args
        assert "failing" in callback_message
        assert "boom" in callback_message

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
        assert len(results[0].errors) == 1
        assert "boom" in results[0].errors[0]
        assert results[1].items_synced == 1
        # Exactly one error was reported, and its message is the failing one.
        error_callback.assert_called_once()
        (callback_message,), _ = error_callback.call_args
        assert "failing" in callback_message
        assert "boom" in callback_message

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


class TestSyncEmbeddingLogging:
    """Tests for embedding progress logging during sync."""

    def test_logs_generating_embedding(self, caplog: pytest.LogCaptureFixture) -> None:
        """Generating an embedding logs an INFO message with item title."""
        items = [make_item("The Name of the Wind", item_id="ext_1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.has_embedding.return_value = False
        embedding_gen = MagicMock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding.return_value = [0.1, 0.2]

        with caplog.at_level(logging.INFO, logger="src.ingestion.sync"):
            execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
                embedding_generator=embedding_gen,
                use_embeddings=True,
            )

        assert any(
            "Generating embedding" in message
            and "The Name of the Wind" in message
            and "1/1" in message
            for message in caplog.messages
        )

    def test_logs_skipping_existing_embedding(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Skipping an existing embedding logs a DEBUG message."""
        items = [make_item("Dune", item_id="ext_1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.has_embedding.return_value = True
        embedding_gen = MagicMock(spec=EmbeddingGenerator)

        with caplog.at_level(logging.DEBUG, logger="src.ingestion.sync"):
            execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
                embedding_generator=embedding_gen,
                use_embeddings=True,
            )

        assert any(
            "Embedding exists, skipping" in message and "Dune" in message
            for message in caplog.messages
        )

    def test_completion_log_includes_embedding_summary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Completion log includes counts of generated and skipped embeddings."""
        items = [
            make_item("New Book", item_id="new_1"),
            make_item("Old Book", item_id="old_1"),
            make_item("Another New", item_id="new_2"),
        ]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)
        storage.has_embedding.side_effect = [False, True, False]
        embedding_gen = MagicMock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding.return_value = [0.1, 0.2]

        with caplog.at_level(logging.INFO, logger="src.ingestion.sync"):
            execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
                embedding_generator=embedding_gen,
                use_embeddings=True,
            )

        assert any(
            "Completed" in message
            and "2 generated" in message
            and "1 skipped" in message
            for message in caplog.messages
        )

    def test_completion_log_excludes_embedding_summary_when_disabled(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Completion log has no embedding summary when embeddings are disabled."""
        items = [make_item("Book 1")]
        plugin = MagicMock(spec=SourcePlugin)
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock(spec=StorageManager)

        with caplog.at_level(logging.INFO, logger="src.ingestion.sync"):
            execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
            )

        completed_messages = [
            message for message in caplog.messages if "Completed" in message
        ]
        assert len(completed_messages) == 1
        assert "Embeddings" not in completed_messages[0]
