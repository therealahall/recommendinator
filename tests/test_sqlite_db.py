"""Tests for SQLite database manager."""

from datetime import date
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import SQLiteDB, normalize_title_for_matching


@pytest.fixture
def temp_db(tmp_path: Path) -> SQLiteDB:
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    return SQLiteDB(db_path)


def test_save_and_get_content_item(temp_db: SQLiteDB) -> None:
    """Test saving and retrieving a content item."""
    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        review="Great book!",
        date_completed=date(2025, 1, 15),
        metadata={"pages": 300},
    )

    db_id = temp_db.save_content_item(item)
    assert db_id > 0

    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"
    assert retrieved.author == "Test Author"
    assert retrieved.rating == 4
    assert retrieved.status == ConsumptionStatus.COMPLETED
    assert retrieved.date_completed == date(2025, 1, 15)
    assert retrieved.metadata == {"pages": 300}


def test_merge_items_from_different_sources_by_title(temp_db: SQLiteDB) -> None:
    """Test that items from different sources merge based on normalized title.

    This ensures we have a single source of truth - if Steam imports "Crysis Remastered"
    and later the personal blog imports "Crysis: Remastered", they should be the same item.
    """
    # First source imports a game
    steam_item = ContentItem(
        id="steam_12345",
        title="Crysis Remastered",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        source="steam",
    )
    db_id_1 = temp_db.save_content_item(steam_item)

    # Second source imports the same game with slightly different title
    blog_item = ContentItem(
        id="crysis",  # Different external ID
        title="Crysis: Remastered",  # Slightly different title
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        source="personal_site_games",
    )
    db_id_2 = temp_db.save_content_item(blog_item)

    # Should be the same database entry
    assert db_id_1 == db_id_2

    # The item should be updated with the new data
    retrieved = temp_db.get_content_item(db_id_1)
    assert retrieved is not None
    assert retrieved.status == ConsumptionStatus.COMPLETED
    assert retrieved.rating == 4
    assert retrieved.source == "personal_site_games"  # Updated to latest source

    # Should only be one item in the database
    all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
    assert len(all_games) == 1


def test_different_titles_create_separate_items(temp_db: SQLiteDB) -> None:
    """Test that genuinely different titles create separate items."""
    item1 = ContentItem(
        id="game_1",
        title="Mass Effect",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
    )
    item2 = ContentItem(
        id="game_2",
        title="Mass Effect 2",
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
    )

    db_id_1 = temp_db.save_content_item(item1)
    db_id_2 = temp_db.save_content_item(item2)

    # Should be different entries
    assert db_id_1 != db_id_2

    all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
    assert len(all_games) == 2


def test_update_content_item(temp_db: SQLiteDB) -> None:
    """Test updating an existing content item."""
    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_db.save_content_item(item)

    # Update the item
    item.rating = 5
    item.status = ConsumptionStatus.COMPLETED
    item.review = "Updated review"

    temp_db.save_content_item(item)

    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.rating == 5
    assert retrieved.status == ConsumptionStatus.COMPLETED
    assert retrieved.review == "Updated review"


def test_get_content_items_with_filters(temp_db: SQLiteDB) -> None:
    """Test getting content items with filters."""
    # Create test items
    items = [
        ContentItem(
            id=f"book_{i}",
            title=f"Book {i}",
            author="Author",
            content_type=ContentType.BOOK,
            status=(
                ConsumptionStatus.COMPLETED if i % 2 == 0 else ConsumptionStatus.UNREAD
            ),
            rating=4 if i % 2 == 0 else None,
        )
        for i in range(5)
    ]

    for item in items:
        temp_db.save_content_item(item)

    # Test filters
    completed = temp_db.get_content_items(status=ConsumptionStatus.COMPLETED)
    assert len(completed) == 3

    unread = temp_db.get_content_items(status=ConsumptionStatus.UNREAD)
    assert len(unread) == 2

    high_rated = temp_db.get_content_items(min_rating=4)
    assert len(high_rated) == 3

    books = temp_db.get_content_items(content_type=ContentType.BOOK)
    assert len(books) == 5


def test_get_unconsumed_items(temp_db: SQLiteDB) -> None:
    """Test getting unconsumed items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD if i < 3 else ConsumptionStatus.COMPLETED,
        )
        for i in range(5)
    ]

    for item in items:
        temp_db.save_content_item(item)

    unconsumed = temp_db.get_unconsumed_items()
    assert len(unconsumed) == 3
    assert all(item.status == ConsumptionStatus.UNREAD for item in unconsumed)


def test_get_completed_items(temp_db: SQLiteDB) -> None:
    """Test getting completed items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3 + i,  # Ratings 3, 4, 5
        )
        for i in range(3)
    ]

    for item in items:
        temp_db.save_content_item(item)

    completed = temp_db.get_completed_items(min_rating=4)
    assert len(completed) == 2
    assert all(item.rating >= 4 for item in completed)


def test_delete_content_item(temp_db: SQLiteDB) -> None:
    """Test deleting a content item."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_db.save_content_item(item)
    assert temp_db.get_content_item(db_id) is not None

    deleted = temp_db.delete_content_item(db_id)
    assert deleted is True

    assert temp_db.get_content_item(db_id) is None


def test_count_items(temp_db: SQLiteDB) -> None:
    """Test counting items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK if i < 3 else ContentType.MOVIE,
            status=(
                ConsumptionStatus.COMPLETED if i % 2 == 0 else ConsumptionStatus.UNREAD
            ),
        )
        for i in range(5)
    ]

    for item in items:
        temp_db.save_content_item(item)

    assert temp_db.count_items() == 5
    assert temp_db.count_items(content_type=ContentType.BOOK) == 3
    assert temp_db.count_items(status=ConsumptionStatus.COMPLETED) == 3


# ---------------------------------------------------------------------------
# Ignore Item Tests
# ---------------------------------------------------------------------------


def test_set_item_ignored(temp_db: SQLiteDB) -> None:
    """Test setting item ignored status."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_db.save_content_item(item)

    # Verify item is not ignored initially
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is False

    # Set ignored to True
    success = temp_db.set_item_ignored(db_id, True)
    assert success is True

    # Verify item is now ignored
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is True

    # Set ignored back to False
    success = temp_db.set_item_ignored(db_id, False)
    assert success is True

    # Verify item is no longer ignored
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is False


def test_set_item_ignored_not_found(temp_db: SQLiteDB) -> None:
    """Test setting ignored status on non-existent item."""
    success = temp_db.set_item_ignored(9999, True)
    assert success is False


def test_set_item_ignored_with_user_id(temp_db: SQLiteDB) -> None:
    """Test setting ignored status with user_id filter."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        user_id=1,
    )

    db_id = temp_db.save_content_item(item)

    # Try to ignore with wrong user_id (should fail)
    success = temp_db.set_item_ignored(db_id, True, user_id=2)
    assert success is False

    # Verify item is still not ignored
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is False

    # Ignore with correct user_id
    success = temp_db.set_item_ignored(db_id, True, user_id=1)
    assert success is True

    # Verify item is now ignored
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is True


def test_item_has_db_id(temp_db: SQLiteDB) -> None:
    """Test that retrieved items have their db_id set."""
    item = ContentItem(
        id="external_123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_db.save_content_item(item)
    assert db_id > 0

    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.db_id == db_id


def test_get_content_items_with_db_ids(temp_db: SQLiteDB) -> None:
    """Test that items from get_content_items have db_ids set."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        for i in range(3)
    ]

    for item in items:
        temp_db.save_content_item(item)

    retrieved = temp_db.get_content_items()
    assert len(retrieved) == 3
    for item in retrieved:
        assert item.db_id is not None
        assert item.db_id > 0


# ---------------------------------------------------------------------------
# Title Normalization Tests
# ---------------------------------------------------------------------------


class TestNormalizeTitleForMatching:
    """Tests for the normalize_title_for_matching function."""

    def test_basic_normalization(self) -> None:
        """Test basic lowercase and whitespace handling."""
        assert normalize_title_for_matching("  The Matrix  ") == "matrix"
        assert (
            normalize_title_for_matching("A Tale of Two Cities") == "tale of two cities"
        )

    def test_trademark_symbols_removed(self) -> None:
        """Regression test: Trademark symbols should be removed for matching.

        Bug reported: "The Last of Us™ Part I" was not matching
        "The Last of Us Part I" from another source.

        Fix: Remove trademark (™), registered (®), and copyright (©) symbols.
        """
        assert (
            normalize_title_for_matching("The Last of Us™ Part I")
            == "last of us part 1"
        )
        assert normalize_title_for_matching("Windows®") == "windows"
        assert normalize_title_for_matching("Copyright© Test") == "copyright test"

    def test_hyphen_to_space_conversion(self) -> None:
        """Regression test: Hyphens should be converted to spaces.

        Bug reported: "State of Decay: Year-One" was not matching
        "State of Decay: Year One" from another source.

        Fix: Convert hyphens to spaces before removing punctuation.
        """
        assert normalize_title_for_matching("Year-One") == "year one"
        # "Survival Edition" is part of the game name, not removed
        assert normalize_title_for_matching(
            "State of Decay: Year-One Survival Edition"
        ) == ("state of decay year one survival edition")

    def test_roman_numeral_conversion(self) -> None:
        """Regression test: Roman numerals should convert to Arabic.

        Bug reported: "The Last of Us Part I" was not matching
        "The Last of Us Part 1" from another source.

        Fix: Convert Roman numerals (I, II, III, etc.) to Arabic (1, 2, 3, etc.).
        """
        assert normalize_title_for_matching("Part I") == "part 1"
        assert normalize_title_for_matching("Part II") == "part 2"
        assert normalize_title_for_matching("Part III") == "part 3"
        assert normalize_title_for_matching("Part IV") == "part 4"
        assert normalize_title_for_matching("Part V") == "part 5"
        assert normalize_title_for_matching("Part VI") == "part 6"
        assert normalize_title_for_matching("Part VII") == "part 7"
        assert normalize_title_for_matching("Part VIII") == "part 8"
        assert normalize_title_for_matching("Part IX") == "part 9"
        assert normalize_title_for_matching("Part X") == "part 10"

    def test_last_of_us_variants_match(self) -> None:
        """Test that Last of Us variants all normalize to the same value."""
        variants = [
            "The Last of Us™ Part I",
            "The Last of Us Part I",
            "The Last of Us Part 1",
            "The Last Of Us: Part I",
        ]
        normalized = [normalize_title_for_matching(variant) for variant in variants]
        # All should be the same
        assert len(set(normalized)) == 1
        assert normalized[0] == "last of us part 1"

    def test_state_of_decay_variants_match(self) -> None:
        """Test that State of Decay variants all normalize to the same value."""
        # Test the core issue: hyphenated vs non-hyphenated
        variants = [
            "State of Decay: Year-One Survival Edition",
            "State of Decay: Year One Survival Edition",
        ]
        normalized = [normalize_title_for_matching(variant) for variant in variants]
        # Both should be the same
        assert len(set(normalized)) == 1
        assert normalized[0] == "state of decay year one survival edition"

    def test_remaster_suffix_removal(self) -> None:
        """Test that remaster/edition suffixes are removed."""
        assert normalize_title_for_matching("Crysis Remastered") == "crysis"
        assert normalize_title_for_matching("Crysis: Remastered") == "crysis"
        assert normalize_title_for_matching("Skyrim Special Edition") == "skyrim"
        assert normalize_title_for_matching("Skyrim: Anniversary Edition") == "skyrim"

    def test_empty_and_none_handling(self) -> None:
        """Test handling of empty strings."""
        assert normalize_title_for_matching("") == ""
        assert normalize_title_for_matching("   ") == ""

    def test_roman_numerals_only_at_word_boundaries(self) -> None:
        """Test that Roman numerals are only converted at word boundaries.

        This prevents false conversions like "Civil" -> "C1v1l".
        """
        # "I" inside a word should NOT be converted
        assert "c1v1l" not in normalize_title_for_matching("Civil War")
        # Should contain "civil" not "c1v1l"
        normalized = normalize_title_for_matching("Civil War")
        assert "civil" in normalized
