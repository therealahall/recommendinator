"""Tests for the enrichment manager."""

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.enrichment.manager import (
    EnrichmentJobStatus,
    EnrichmentManager,
    merge_enrichment,
)
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
)
from src.enrichment.registry import EnrichmentRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class MockProvider(EnrichmentProvider):
    """Mock provider for testing."""

    def __init__(
        self,
        name: str = "mock",
        content_types: list[ContentType] | None = None,
        should_fail: bool = False,
        should_not_find: bool = False,
    ) -> None:
        self._name = name
        self._content_types = content_types or [ContentType.MOVIE]
        self._should_fail = should_fail
        self._should_not_find = should_not_find
        self.enrich_calls: list[ContentItem] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return f"Mock Provider ({self._name})"

    @property
    def content_types(self) -> list[ContentType]:
        return self._content_types

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 100.0  # High limit for fast tests

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        self.enrich_calls.append(item)

        if self._should_fail:
            raise ProviderError(self._name, "Simulated failure")

        if self._should_not_find:
            return EnrichmentResult(match_quality="not_found", provider=self._name)

        return EnrichmentResult(
            external_id=f"{self._name}:{item.id}",
            genres=["Action", "Drama"],
            tags=["test-tag"],
            description="A test description.",
            extra_metadata={"source_rating": 8.5},
            match_quality="high",
            provider=self._name,
        )


class TestEnrichmentJobStatus:
    """Tests for EnrichmentJobStatus dataclass."""

    def test_default_values(self) -> None:
        """Test default status values."""
        status = EnrichmentJobStatus()

        assert status.running is False
        assert status.completed is False
        assert status.cancelled is False
        assert status.items_processed == 0
        assert status.total_items == 0
        assert status.errors == []

    def test_progress_percent_zero_total(self) -> None:
        """Test progress with zero total items."""
        status = EnrichmentJobStatus()
        assert status.progress_percent == 0.0

    def test_progress_percent_with_items(self) -> None:
        """Test progress calculation."""
        status = EnrichmentJobStatus(items_processed=50, total_items=100)
        assert status.progress_percent == 50.0

    def test_elapsed_seconds_not_started(self) -> None:
        """Test elapsed time when not started."""
        status = EnrichmentJobStatus()
        assert status.elapsed_seconds == 0.0

    def test_elapsed_seconds_running(self) -> None:
        """Test elapsed time while running."""
        status = EnrichmentJobStatus(started_at=time.time() - 5.0)
        assert 4.9 < status.elapsed_seconds < 5.5

    def test_elapsed_seconds_completed(self) -> None:
        """Test elapsed time when completed."""
        status = EnrichmentJobStatus(
            started_at=time.time() - 10.0,
            completed_at=time.time() - 5.0,
        )
        assert 4.9 < status.elapsed_seconds < 5.1


class TestMergeEnrichment:
    """Tests for the merge_enrichment function."""

    def test_merge_empty_metadata(self) -> None:
        """Test merging into empty metadata."""
        result = EnrichmentResult(
            external_id="tmdb:123",
            genres=["Action"],
            tags=["exciting"],
            description="A movie.",
            extra_metadata={"runtime": 120},
            provider="tmdb",
        )

        merged = merge_enrichment({}, result)

        assert merged["genres"] == ["Action"]
        assert merged["tags"] == ["exciting"]
        assert merged["description"] == "A movie."
        assert merged["runtime"] == 120
        assert merged["enrichment_id"] == "tmdb:123"

    def test_merge_preserves_existing_genres(self) -> None:
        """Test that existing genres are not overwritten."""
        existing = {"genres": ["Comedy"]}
        result = EnrichmentResult(
            genres=["Action"],
            provider="tmdb",
        )

        merged = merge_enrichment(existing, result)

        assert merged["genres"] == ["Comedy"]  # Preserved

    def test_merge_fills_missing_fields(self) -> None:
        """Test that only missing fields are filled."""
        existing = {
            "genres": ["Comedy"],
            "director": "Someone",
        }
        result = EnrichmentResult(
            genres=["Action"],
            tags=["funny"],
            description="A comedy.",
            extra_metadata={"director": "Someone Else", "runtime": 90},
            provider="tmdb",
        )

        merged = merge_enrichment(existing, result)

        assert merged["genres"] == ["Comedy"]  # Preserved
        assert merged["tags"] == ["funny"]  # Added
        assert merged["description"] == "A comedy."  # Added
        assert merged["director"] == "Someone"  # Preserved
        assert merged["runtime"] == 90  # Added

    def test_merge_handles_none_values(self) -> None:
        """Test merging when result has None values."""
        existing = {}
        result = EnrichmentResult(
            genres=None,
            tags=["tag"],
            description=None,
            provider="tmdb",
        )

        merged = merge_enrichment(existing, result)

        assert "genres" not in merged or merged.get("genres") is None
        assert merged["tags"] == ["tag"]
        assert "description" not in merged or merged.get("description") is None

    def test_merge_fills_empty_string_fields(self) -> None:
        """Test that empty string fields are filled."""
        existing = {"description": ""}
        result = EnrichmentResult(
            description="New description",
            provider="tmdb",
        )

        merged = merge_enrichment(existing, result)

        # Empty string should be treated as missing
        assert merged["description"] == "New description"


class TestEnrichmentManager:
    """Tests for the EnrichmentManager class."""

    @pytest.fixture
    def mock_storage(self) -> MagicMock:
        """Create a mock storage manager."""
        storage = MagicMock()
        storage.get_items_needing_enrichment.return_value = []
        return storage

    @pytest.fixture
    def mock_registry(self) -> EnrichmentRegistry:
        """Create a mock registry with test providers."""
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        """Create test configuration."""
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {
                    "mock": {"enabled": True},
                },
            }
        }

    def test_start_enrichment_returns_true(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test starting enrichment returns True."""
        manager = EnrichmentManager(mock_storage, config, mock_registry)

        result = manager.start_enrichment()

        assert result is True

    def test_start_enrichment_when_running_returns_false(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test starting enrichment when already running returns False."""
        manager = EnrichmentManager(mock_storage, config, mock_registry)

        manager.start_enrichment()
        result = manager.start_enrichment()

        assert result is False

    def test_stop_enrichment(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test stopping enrichment."""
        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        manager.stop_enrichment()

        # Wait for thread to stop
        time.sleep(0.1)
        status = manager.get_status()
        assert status.running is False

    def test_get_status(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test getting job status."""
        manager = EnrichmentManager(mock_storage, config, mock_registry)

        status = manager.get_status()

        assert isinstance(status, EnrichmentJobStatus)
        assert status.running is False

    def test_enrichment_processes_items(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test that enrichment processes items from storage."""
        # Setup items to enrich
        items = [
            (
                1,
                ContentItem(
                    id="movie1",
                    title="Movie 1",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                ),
            ),
            (
                2,
                ContentItem(
                    id="movie2",
                    title="Movie 2",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                ),
            ),
        ]

        # Return items on first call, empty on second
        mock_storage.get_items_needing_enrichment.side_effect = [items, []]

        # Setup provider
        provider = MockProvider()
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        # Wait for completion
        time.sleep(0.2)

        status = manager.get_status()
        assert status.completed is True
        assert status.items_processed == 2
        assert status.items_enriched == 2

    def test_enrichment_marks_not_found(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test that items not found are marked appropriately."""
        items = [
            (
                1,
                ContentItem(
                    id="movie1",
                    title="Unknown Movie",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                ),
            ),
        ]
        mock_storage.get_items_needing_enrichment.side_effect = [items, []]

        # Provider returns not_found
        provider = MockProvider(should_not_find=True)
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        time.sleep(0.2)

        status = manager.get_status()
        assert status.items_not_found == 1

    def test_enrichment_handles_provider_errors(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test that provider errors are handled gracefully."""
        items = [
            (
                1,
                ContentItem(
                    id="movie1",
                    title="Movie 1",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                ),
            ),
        ]
        mock_storage.get_items_needing_enrichment.side_effect = [items, []]

        # Provider always fails
        provider = MockProvider(should_fail=True)
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        time.sleep(0.2)

        status = manager.get_status()
        assert status.completed is True
        assert len(status.errors) > 0

    def test_enrichment_filters_by_content_type(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test that enrichment can filter by content type."""
        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)

        time.sleep(0.1)

        # Verify storage was called with content_type filter
        mock_storage.get_items_needing_enrichment.assert_called_with(
            content_type=ContentType.MOVIE,
            user_id=None,
            limit=10,
        )

    def test_enrichment_applies_gap_filling(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test that enrichment uses gap-filling merge strategy."""
        # Item with existing genres
        item = ContentItem(
            id="movie1",
            title="Movie 1",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Comedy"]},  # Existing genre
        )
        items = [(1, item)]
        mock_storage.get_items_needing_enrichment.side_effect = [items, []]

        provider = MockProvider()
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        time.sleep(0.2)

        # Verify save was called
        assert mock_storage.save_content_item.called
        saved_item = mock_storage.save_content_item.call_args[0][0]

        # Existing genres should be preserved
        assert saved_item.metadata["genres"] == ["Comedy"]
        # New fields should be added
        assert saved_item.metadata.get("tags") == ["test-tag"]
        assert saved_item.metadata.get("description") == "A test description."

    def test_no_providers_for_content_type(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test handling when no providers match content type."""
        # Book item but only movie provider registered
        items = [
            (
                1,
                ContentItem(
                    id="book1",
                    title="Book 1",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
            ),
        ]
        mock_storage.get_items_needing_enrichment.side_effect = [items, []]

        # Only movie provider
        provider = MockProvider(content_types=[ContentType.MOVIE])
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        time.sleep(0.2)

        status = manager.get_status()
        assert status.items_not_found == 1
