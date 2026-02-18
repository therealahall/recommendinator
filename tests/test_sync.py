"""Tests for the shared sync executor."""

from unittest.mock import MagicMock

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sync import SyncResult, execute_multi_source_sync, execute_sync
from src.models.content import ConsumptionStatus, ContentItem, ContentType


def _make_item(title: str, external_id: str | None = None) -> ContentItem:
    """Create a minimal ContentItem for testing."""
    return ContentItem(
        id=external_id,
        title=title,
        author=None,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
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
        items = [_make_item("Book 1"), _make_item("Book 2")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()

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
        items = [_make_item("Book 1", external_id="ext_1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
        storage.has_embedding.return_value = False
        embedding_gen = MagicMock()
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
        items = [_make_item("Book 1", external_id="ext_1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
        storage.has_embedding.return_value = True
        embedding_gen = MagicMock()

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
        storage.save_content_item.assert_called_once_with(
            items[0], embedding=None
        )

    def test_sync_generates_embedding_for_items_without_external_id(self) -> None:
        """Items without external IDs always get embeddings (can't check existence)."""
        items = [_make_item("Book 1")]  # id=None
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
        embedding_gen = MagicMock()
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
        items = [_make_item("Book 1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
        embedding_gen = MagicMock()

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
        items = [_make_item("Good"), _make_item("Bad"), _make_item("Also Good")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
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
        items = [_make_item("Book 1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
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
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.side_effect = SourceError("test", "connection failed")

        storage = MagicMock()

        with pytest.raises(SourceError, match="connection failed"):
            execute_sync(
                plugin=plugin,
                plugin_config={},
                storage_manager=storage,
            )

    def test_empty_source(self) -> None:
        """Sync with no items returns zero counts."""
        plugin = MagicMock()
        plugin.display_name = "EmptyPlugin"
        plugin.fetch.return_value = iter([])

        storage = MagicMock()

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
        """Plugin receives the exact config dict."""
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter([])

        storage = MagicMock()
        config = {"url": "http://example.com", "api_key": "secret"}

        execute_sync(
            plugin=plugin,
            plugin_config=config,
            storage_manager=storage,
        )

        plugin.fetch.assert_called_once()
        call_args = plugin.fetch.call_args
        assert call_args[0][0] == config


class TestExecuteMultiSourceSync:
    """Tests for execute_multi_source_sync function."""

    def test_multiple_sources(self) -> None:
        """Syncs multiple sources sequentially and returns results."""
        plugin_a = MagicMock()
        plugin_a.name = "source_a"
        plugin_a.display_name = "Source A"
        plugin_a.fetch.return_value = iter([_make_item("A1")])

        plugin_b = MagicMock()
        plugin_b.name = "source_b"
        plugin_b.display_name = "Source B"
        plugin_b.fetch.return_value = iter([_make_item("B1"), _make_item("B2")])

        storage = MagicMock()

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
        plugin_a = MagicMock()
        plugin_a.name = "failing"
        plugin_a.display_name = "Failing"
        plugin_a.fetch.side_effect = SourceError("failing", "boom")

        plugin_b = MagicMock()
        plugin_b.name = "working"
        plugin_b.display_name = "Working"
        plugin_b.fetch.return_value = iter([_make_item("B1")])

        storage = MagicMock()
        error_callback = MagicMock()

        results = execute_multi_source_sync(
            sources=[(plugin_a, {}), (plugin_b, {})],
            storage_manager=storage,
            error_callback=error_callback,
        )

        assert len(results) == 2
        assert results[0].items_synced == 0
        assert len(results[0].errors) == 1
        assert results[1].items_synced == 1
        error_callback.assert_called()

    def test_empty_sources(self) -> None:
        """Empty source list returns empty results."""
        storage = MagicMock()

        results = execute_multi_source_sync(
            sources=[],
            storage_manager=storage,
        )

        assert results == []

    def test_mark_for_enrichment_passed_through(self) -> None:
        """mark_for_enrichment flag is passed to execute_sync."""
        plugin = MagicMock()
        plugin.name = "test"
        plugin.display_name = "Test"
        plugin.fetch.return_value = iter([_make_item("Book 1")])

        storage = MagicMock()
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


class TestAutoEnrichmentHook:
    """Tests for auto-enrichment marking during sync."""

    def test_mark_for_enrichment_enabled(self) -> None:
        """Items are marked for enrichment when flag is True."""
        items = [_make_item("Book 1"), _make_item("Book 2")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
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
        items = [_make_item("Book 1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
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
        items = [_make_item("Book 1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
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
        items = [_make_item("Book 1"), _make_item("Book 2")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
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
        items = [_make_item("Book 1")]
        plugin = MagicMock()
        plugin.display_name = "TestPlugin"
        plugin.fetch.return_value = iter(items)

        storage = MagicMock()
        storage.save_content_item.return_value = None  # No DB ID

        result = execute_sync(
            plugin=plugin,
            plugin_config={},
            storage_manager=storage,
            mark_for_enrichment=True,
        )

        assert result.items_synced == 1
        storage.mark_item_needs_enrichment.assert_not_called()
