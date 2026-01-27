"""Tests for conflict resolution module."""

from datetime import date

import pytest

from src.ingestion.conflict import ConflictStrategy, resolve_conflict
from src.models.content import ConsumptionStatus, ContentItem, ContentType


@pytest.fixture()
def existing_book() -> ContentItem:
    """Create an existing book content item."""
    return ContentItem(
        id="book_123",
        title="The Name of the Wind",
        author="Patrick Rothfuss",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        review="Excellent fantasy novel",
        date_completed=date(2024, 6, 15),
        source="goodreads",
        metadata={"isbn": "978-0756404741", "pages": "662"},
    )


@pytest.fixture()
def incoming_book() -> ContentItem:
    """Create an incoming book content item from a different source."""
    return ContentItem(
        id="book_123",
        title="The Name of the Wind",
        author="Patrick Rothfuss",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        review=None,
        date_completed=date(2024, 7, 1),
        source="csv_import",
        metadata={"genre": "Fantasy", "year_published": "2007"},
    )


class TestConflictStrategy:
    """Tests for ConflictStrategy enum."""

    def test_last_write_wins_value(self) -> None:
        assert ConflictStrategy.LAST_WRITE_WINS.value == "last_write_wins"

    def test_source_priority_value(self) -> None:
        assert ConflictStrategy.SOURCE_PRIORITY.value == "source_priority"

    def test_keep_existing_value(self) -> None:
        assert ConflictStrategy.KEEP_EXISTING.value == "keep_existing"

    def test_from_string(self) -> None:
        assert ConflictStrategy("last_write_wins") == ConflictStrategy.LAST_WRITE_WINS
        assert ConflictStrategy("source_priority") == ConflictStrategy.SOURCE_PRIORITY
        assert ConflictStrategy("keep_existing") == ConflictStrategy.KEEP_EXISTING


class TestLastWriteWins:
    """Tests for LAST_WRITE_WINS conflict strategy."""

    def test_incoming_overwrites_existing(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Incoming item should completely replace existing."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.LAST_WRITE_WINS,
        )

        assert result.rating == 4
        assert result.review is None
        assert result.source == "csv_import"
        assert result.date_completed == date(2024, 7, 1)

    def test_is_default_strategy(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """LAST_WRITE_WINS should be the default strategy."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
        )

        assert result.source == "csv_import"
        assert result.rating == 4


class TestSourcePriority:
    """Tests for SOURCE_PRIORITY conflict strategy."""

    def test_higher_priority_existing_wins(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Existing item from higher-priority source should win."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=["goodreads", "csv_import"],
        )

        # Existing (goodreads) has higher priority
        assert result.rating == 5
        assert result.review == "Excellent fantasy novel"
        assert result.source == "goodreads"

    def test_higher_priority_incoming_wins(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Incoming item from higher-priority source should win."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=["csv_import", "goodreads"],
        )

        # Incoming (csv_import) has higher priority
        assert result.rating == 4
        assert result.source == "csv_import"

    def test_same_priority_incoming_wins(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """When sources have equal priority, incoming wins."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=[],
        )

        # Both not in list = equal priority, incoming wins
        assert result.source == "csv_import"

    def test_unlisted_source_has_lowest_priority(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Sources not in priority list should have lowest priority."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=["goodreads"],
        )

        # goodreads is in list (high priority), csv_import is not (lowest)
        assert result.source == "goodreads"
        assert result.rating == 5

    def test_fills_none_fields_from_loser(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Winner should get None fields filled from the loser."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=["csv_import", "goodreads"],
        )

        # Incoming wins but has review=None, should be filled from existing
        assert result.source == "csv_import"
        assert result.review == "Excellent fantasy novel"

    def test_merges_metadata(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Metadata should be merged with winner's taking precedence."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=["csv_import", "goodreads"],
        )

        # Incoming wins — its metadata takes precedence, existing fills gaps
        assert result.metadata["genre"] == "Fantasy"
        assert result.metadata["year_published"] == "2007"
        # Existing metadata fills gaps
        assert result.metadata["isbn"] == "978-0756404741"
        assert result.metadata["pages"] == "662"

    def test_none_source_priority_treated_as_empty(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """None source_priority should be treated as empty list."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=None,
        )

        # Both have lowest priority, incoming wins
        assert result.source == "csv_import"


class TestKeepExisting:
    """Tests for KEEP_EXISTING conflict strategy."""

    def test_existing_values_preserved(
        self, existing_book: ContentItem, incoming_book: ContentItem
    ) -> None:
        """Existing non-None values should never be overwritten."""
        result = resolve_conflict(
            existing=existing_book,
            incoming=incoming_book,
            strategy=ConflictStrategy.KEEP_EXISTING,
        )

        assert result.rating == 5
        assert result.review == "Excellent fantasy novel"
        assert result.source == "goodreads"
        assert result.date_completed == date(2024, 6, 15)

    def test_fills_none_fields_from_incoming(self) -> None:
        """None fields on existing should be filled from incoming."""
        existing = ContentItem(
            id="game_1",
            title="Test Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            review=None,
            source="steam",
            metadata={"playtime_hours": 50},
        )
        incoming = ContentItem(
            id="game_1",
            title="Test Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            review="Great game",
            source="csv_import",
            metadata={"genre": "RPG"},
        )

        result = resolve_conflict(
            existing=existing,
            incoming=incoming,
            strategy=ConflictStrategy.KEEP_EXISTING,
        )

        # Existing None fields filled from incoming
        assert result.rating == 4
        assert result.review == "Great game"
        # Non-None fields preserved from existing
        assert result.source == "steam"
        # Metadata merged
        assert result.metadata["playtime_hours"] == 50
        assert result.metadata["genre"] == "RPG"

    def test_metadata_merge_existing_takes_precedence(self) -> None:
        """Existing metadata keys should take precedence over incoming."""
        existing = ContentItem(
            id="book_1",
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source="goodreads",
            metadata={"genre": "Sci-Fi", "pages": "300"},
        )
        incoming = ContentItem(
            id="book_1",
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source="csv_import",
            metadata={"genre": "Science Fiction", "year": "2020"},
        )

        result = resolve_conflict(
            existing=existing,
            incoming=incoming,
            strategy=ConflictStrategy.KEEP_EXISTING,
        )

        # Existing metadata takes precedence
        assert result.metadata["genre"] == "Sci-Fi"
        assert result.metadata["pages"] == "300"
        # New keys from incoming are added
        assert result.metadata["year"] == "2020"


class TestEdgeCases:
    """Tests for edge cases in conflict resolution."""

    def test_identical_items(self) -> None:
        """Resolving identical items should work cleanly."""
        item = ContentItem(
            id="book_1",
            title="Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            source="goodreads",
        )

        result = resolve_conflict(
            existing=item,
            incoming=item,
            strategy=ConflictStrategy.LAST_WRITE_WINS,
        )

        assert result.title == "Test Book"
        assert result.rating == 5

    def test_no_source_on_items(self) -> None:
        """Items without source should work with source_priority."""
        existing = ContentItem(
            id="x",
            title="Test",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            source=None,
        )
        incoming = ContentItem(
            id="x",
            title="Test Updated",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            source=None,
        )

        result = resolve_conflict(
            existing=existing,
            incoming=incoming,
            strategy=ConflictStrategy.SOURCE_PRIORITY,
            source_priority=["goodreads"],
        )

        # Both have no source (not in list), equal priority, incoming wins
        assert result.title == "Test Updated"

    def test_empty_metadata_merge(self) -> None:
        """Merging empty metadata dicts should produce empty dict."""
        existing = ContentItem(
            id="x",
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={},
        )
        incoming = ContentItem(
            id="x",
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={},
        )

        result = resolve_conflict(
            existing=existing,
            incoming=incoming,
            strategy=ConflictStrategy.KEEP_EXISTING,
        )

        assert result.metadata == {}
