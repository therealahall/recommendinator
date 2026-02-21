"""Tests for SQLite database manager."""

from datetime import date
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import (
    SQLiteDB,
    _resolve_status_forward,
    normalize_title_for_matching,
)


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


# ---------------------------------------------------------------------------
# Ignored on Insert Tests
# ---------------------------------------------------------------------------


def test_save_content_item_with_ignored_true(temp_db: SQLiteDB) -> None:
    """Test that ignored=True is persisted on INSERT."""
    item = ContentItem(
        id="ignored_1",
        title="Ignored Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        ignored=True,
    )

    db_id = temp_db.save_content_item(item)
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is True


def test_save_content_item_update_syncs_ignored(temp_db: SQLiteDB) -> None:
    """Test that UPDATE path updates the ignored field.

    When re-syncing, the ignored field should be updated like any other
    field so that import files with ignored: true take effect on existing items.
    """
    # First insert with ignored=False
    item = ContentItem(
        id="sync_1",
        title="A Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        ignored=False,
    )
    db_id = temp_db.save_content_item(item)

    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is False

    # Re-sync the same item with ignored=True
    updated_item = ContentItem(
        id="sync_1",
        title="A Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        ignored=True,
    )
    db_id_2 = temp_db.save_content_item(updated_item)
    assert db_id == db_id_2  # Same item

    # ignored should now be True
    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.ignored is True
    assert retrieved.status == ConsumptionStatus.COMPLETED
    assert retrieved.rating == 4


def test_get_content_items_include_ignored_true(temp_db: SQLiteDB) -> None:
    """Test that get_content_items returns ignored items when include_ignored=True."""
    items = [
        ContentItem(
            id="normal_1",
            title="Normal Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        ),
        ContentItem(
            id="ignored_1",
            title="Ignored Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            ignored=True,
        ),
    ]
    for item in items:
        temp_db.save_content_item(item)

    # Default (include_ignored=True) returns all items
    all_items = temp_db.get_content_items(include_ignored=True)
    assert len(all_items) == 2

    titles = {item.title for item in all_items}
    assert "Normal Book" in titles
    assert "Ignored Book" in titles


def test_get_content_items_include_ignored_false(temp_db: SQLiteDB) -> None:
    """Test that get_content_items excludes ignored items when include_ignored=False."""
    items = [
        ContentItem(
            id="normal_1",
            title="Normal Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        ),
        ContentItem(
            id="ignored_1",
            title="Ignored Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            ignored=True,
        ),
    ]
    for item in items:
        temp_db.save_content_item(item)

    filtered_items = temp_db.get_content_items(include_ignored=False)
    assert len(filtered_items) == 1
    assert filtered_items[0].title == "Normal Book"


class TestToJsonArrayRegression:
    """Regression tests for _to_json_array() bare string handling.

    Bug reported: TV show genres stored as bare strings like ``"Drama"``
    instead of JSON arrays ``'["Drama"]'``.  Downstream code expecting
    JSON arrays would fail to parse them, resulting in single-genre
    items that weakly matched everything via broad Jaccard overlap.

    Root cause: ``_to_json_array()`` returned bare strings unchanged
    (``if isinstance(val, str): return val``).

    Fix: Bare strings are now wrapped in a JSON array; only strings
    that already start with ``[`` are passed through.
    """

    def test_bare_string_wrapped_in_json_array_regression(self) -> None:
        """A bare string like 'Drama' should become '["Drama"]'."""
        result = SQLiteDB._to_json_array("Drama")
        assert result == '["Drama"]'

    def test_existing_json_array_unchanged(self) -> None:
        """A string that is already a JSON array should be returned as-is."""
        result = SQLiteDB._to_json_array('["Drama", "Action"]')
        assert result == '["Drama", "Action"]'

    def test_list_converted_to_json(self) -> None:
        """A Python list should be serialized to JSON."""
        result = SQLiteDB._to_json_array(["Drama"])
        assert result == '["Drama"]'

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        result = SQLiteDB._to_json_array(None)
        assert result is None

    def test_multi_element_list(self) -> None:
        """A multi-element list should serialize correctly."""
        result = SQLiteDB._to_json_array(["Drama", "Action", "Comedy"])
        assert result == '["Drama", "Action", "Comedy"]'


class TestAdditiveGenreSaves:
    """Tests for additive genre/tag saving in detail tables.

    Bug reported: Re-importing items from a source would overwrite genres
    and tags that had been added by enrichment, destroying richer data.

    Root cause: ``INSERT OR REPLACE`` replaced the entire row, including
    genres and tags, instead of merging new values with existing ones.

    Fix: ``_save_detail_table()`` now queries for an existing row and
    merges genres/tags using ``merge_string_lists()`` before writing.
    """

    def test_reimport_merges_genres(self, temp_db: SQLiteDB) -> None:
        """Re-saving an item should merge genres, not replace them."""
        item_v1 = ContentItem(
            id="tv_1",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["Drama"]},
        )
        db_id = temp_db.save_content_item(item_v1)

        # Simulate enrichment adding more genres
        item_v2 = ContentItem(
            id="tv_1",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["Comedy", "Action"]},
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        genres = retrieved.metadata.get("genres", [])
        # All three genres should be present
        assert "Drama" in genres
        assert "Comedy" in genres
        assert "Action" in genres

    def test_reimport_deduplicates_genres_case_insensitive(
        self, temp_db: SQLiteDB
    ) -> None:
        """Re-saving should not create duplicate genres (case-insensitive)."""
        item_v1 = ContentItem(
            id="tv_2",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["Drama"]},
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="tv_2",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["Drama", "Action"]},
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        genres = retrieved.metadata.get("genres", [])
        # Drama should appear only once
        drama_count = sum(1 for genre in genres if genre.lower() == "drama")
        assert drama_count == 1
        assert "Action" in genres

    def test_reimport_merges_tags(self, temp_db: SQLiteDB) -> None:
        """Re-saving should merge tags additively."""
        item_v1 = ContentItem(
            id="game_1",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["RPG"], "tags": ["space", "story rich"]},
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="game_1",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["RPG"], "tags": ["open world", "space"]},
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        tags = retrieved.metadata.get("tags", [])
        assert "space" in tags
        assert "story rich" in tags
        assert "open world" in tags
        # "space" should not be duplicated
        space_count = sum(1 for tag in tags if tag.lower() == "space")
        assert space_count == 1

    def test_first_save_works_without_existing_row(self, temp_db: SQLiteDB) -> None:
        """First save should work normally via INSERT."""
        item = ContentItem(
            id="book_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={"genres": ["Science Fiction"], "tags": ["space", "politics"]},
        )
        db_id = temp_db.save_content_item(item)
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert "Science Fiction" in retrieved.metadata.get("genres", [])
        assert "space" in retrieved.metadata.get("tags", [])


# ---------------------------------------------------------------------------
# Non-destructive update tests
# ---------------------------------------------------------------------------


class TestResolveStatusForward:
    """Unit tests for the forward-only status resolution helper."""

    def test_none_existing_uses_incoming(self) -> None:
        """When no existing status, any incoming status is accepted."""
        assert _resolve_status_forward(None, "completed") == "completed"
        assert _resolve_status_forward(None, "unread") == "unread"

    def test_forward_progression_unread_to_consuming(self) -> None:
        assert _resolve_status_forward("unread", "currently_consuming") == (
            "currently_consuming"
        )

    def test_forward_progression_consuming_to_completed(self) -> None:
        assert (
            _resolve_status_forward("currently_consuming", "completed") == "completed"
        )

    def test_forward_progression_unread_to_completed(self) -> None:
        assert _resolve_status_forward("unread", "completed") == "completed"

    def test_same_status_keeps_same(self) -> None:
        assert _resolve_status_forward("completed", "completed") == "completed"

    def test_backward_blocked_completed_to_unread(self) -> None:
        """Completed status should never regress to unread."""
        assert _resolve_status_forward("completed", "unread") == "completed"

    def test_backward_blocked_completed_to_consuming(self) -> None:
        """Completed status should never regress to currently_consuming."""
        assert (
            _resolve_status_forward("completed", "currently_consuming") == "completed"
        )

    def test_backward_blocked_consuming_to_unread(self) -> None:
        """Currently_consuming should never regress to unread."""
        assert (
            _resolve_status_forward("currently_consuming", "unread")
            == "currently_consuming"
        )


class TestRatingSetOnce:
    """Tests that rating is set once and never overwritten.

    Bug reported: Re-syncing from a source without ratings would overwrite
    existing ratings with None. Even syncing a different rating would
    clobber user-curated data.

    Fix: Rating is only written when the existing rating is None and the
    incoming rating is not None.
    """

    def test_initial_save_sets_rating(self, temp_db: SQLiteDB) -> None:
        """First save should set the rating normally."""
        item = ContentItem(
            id="book_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        db_id = temp_db.save_content_item(item)
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 5

    def test_resync_does_not_overwrite_existing_rating(self, temp_db: SQLiteDB) -> None:
        """Re-syncing with a different rating should not overwrite the original."""
        item_v1 = ContentItem(
            id="book_2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="book_2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 5  # Original rating preserved

    def test_resync_with_none_does_not_clear_rating(self, temp_db: SQLiteDB) -> None:
        """Re-syncing with None rating should not clear existing rating."""
        item_v1 = ContentItem(
            id="book_3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="book_3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=None,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 4  # Original rating preserved

    def test_set_rating_when_existing_is_none(self, temp_db: SQLiteDB) -> None:
        """Setting rating on item that initially had None should succeed."""
        item_v1 = ContentItem(
            id="book_4",
            title="Snow Crash",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            rating=None,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="book_4",
            title="Snow Crash",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 4


class TestReviewSetOnce:
    """Tests that review is set once and never overwritten."""

    def test_initial_save_sets_review(self, temp_db: SQLiteDB) -> None:
        item = ContentItem(
            id="book_r1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review="Amazing book",
        )
        db_id = temp_db.save_content_item(item)
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Amazing book"

    def test_resync_does_not_overwrite_existing_review(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="book_r2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review="Classic sci-fi",
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="book_r2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review="Different opinion",
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Classic sci-fi"

    def test_resync_with_none_does_not_clear_review(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="book_r3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review="Cyberpunk classic",
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="book_r3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review=None,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Cyberpunk classic"

    def test_set_review_when_existing_is_none(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="book_r4",
            title="Snow Crash",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            review=None,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="book_r4",
            title="Snow Crash",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review="Great read!",
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Great read!"


class TestStatusForwardOnly:
    """Integration tests that status only advances forward in save_content_item.

    Bug reported: Re-syncing from a source that reports "unread" would
    revert a "completed" item back to "unread", losing completion history.

    Fix: Status uses forward-only progression: unread → currently_consuming
    → completed. A re-sync with an earlier status does not revert.
    """

    def test_status_advances_unread_to_completed(self, temp_db: SQLiteDB) -> None:
        item = ContentItem(
            id="s1",
            title="Book A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = temp_db.save_content_item(item)

        item.status = ConsumptionStatus.COMPLETED
        temp_db.save_content_item(item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_status_does_not_regress_completed_to_unread(
        self, temp_db: SQLiteDB
    ) -> None:
        """Completed items should not be reverted to unread by re-sync."""
        item_v1 = ContentItem(
            id="s2",
            title="Book B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="s2",
            title="Book B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_status_does_not_regress_consuming_to_unread(
        self, temp_db: SQLiteDB
    ) -> None:
        item_v1 = ContentItem(
            id="s3",
            title="Book C",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="s3",
            title="Book C",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.CURRENTLY_CONSUMING

    def test_multi_source_sync_order_independent(self, temp_db: SQLiteDB) -> None:
        """Status should settle at highest value regardless of sync order.

        Source A reports "unread", Source B reports "completed".
        Result should be "completed" regardless of which syncs first.
        """
        # Source B syncs first (completed)
        item_b = ContentItem(
            id="s4",
            title="Book D",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source="source_b",
        )
        db_id = temp_db.save_content_item(item_b)

        # Source A syncs second (unread)
        item_a = ContentItem(
            id="s4",
            title="Book D",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source="source_a",
        )
        temp_db.save_content_item(item_a)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED


class TestDateCompletedProtection:
    """Tests that date_completed only advances forward.

    Rule: date_completed is only updated when the incoming value is not None
    AND it is later than the existing value.
    """

    def test_initial_save_sets_date(self, temp_db: SQLiteDB) -> None:
        item = ContentItem(
            id="d1",
            title="Book E",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2025, 6, 15),
        )
        db_id = temp_db.save_content_item(item)
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2025, 6, 15)

    def test_later_date_replaces_earlier(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="d2",
            title="Book F",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2025, 1, 1),
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="d2",
            title="Book F",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2025, 6, 15),
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2025, 6, 15)

    def test_earlier_date_does_not_replace_later(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="d3",
            title="Book G",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2025, 6, 15),
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="d3",
            title="Book G",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2024, 1, 1),
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2025, 6, 15)

    def test_none_date_does_not_clear_existing(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="d4",
            title="Book H",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2025, 3, 10),
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="d4",
            title="Book H",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=None,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2025, 3, 10)

    def test_set_date_when_existing_is_none(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="d5",
            title="Book I",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            date_completed=None,
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="d5",
            title="Book I",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2025, 6, 15),
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2025, 6, 15)


class TestNoneNeverOverwrites:
    """Tests that None values never overwrite existing data (universal rule).

    This is a cross-cutting concern: if an incoming sync lacks data for a
    field that already has a value, the existing value must be preserved.
    """

    def test_none_source_does_not_overwrite(self, temp_db: SQLiteDB) -> None:
        item_v1 = ContentItem(
            id="n1",
            title="Book J",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source="goodreads",
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="n1",
            title="Book J",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source=None,
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.source == "goodreads"


class TestDetailTableFillOnly:
    """Tests that detail table scalar fields are fill-only.

    Enrichment is the source of truth for detail fields. Once a value
    is set (by ingestion or enrichment), subsequent syncs should not
    overwrite it. Only empty (None) fields get filled.
    """

    def test_description_not_overwritten(self, temp_db: SQLiteDB) -> None:
        """Existing description should not be replaced by new sync."""
        item_v1 = ContentItem(
            id="detail_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={"description": "A classic sci-fi novel about Arrakis."},
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="detail_1",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={"description": "Different description from another source."},
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("description") == (
            "A classic sci-fi novel about Arrakis."
        )

    def test_author_not_overwritten(self, temp_db: SQLiteDB) -> None:
        """Existing author should not be replaced by new sync."""
        item_v1 = ContentItem(
            id="detail_2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            author="Isaac Asimov",
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="detail_2",
            title="Foundation",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            author="I. Asimov",
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.author == "Isaac Asimov"

    def test_empty_field_gets_filled(self, temp_db: SQLiteDB) -> None:
        """Fields that are None should be filled on subsequent sync."""
        item_v1 = ContentItem(
            id="detail_3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={},
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="detail_3",
            title="Neuromancer",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={
                "description": "A cyberpunk novel.",
                "pages": 271,
                "publisher": "Ace Books",
            },
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("description") == "A cyberpunk novel."
        assert retrieved.metadata.get("pages") == 271
        assert retrieved.metadata.get("publisher") == "Ace Books"

    def test_year_published_not_overwritten(self, temp_db: SQLiteDB) -> None:
        """Numeric detail fields should also be fill-only."""
        item_v1 = ContentItem(
            id="detail_4",
            title="Snow Crash",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={"year_published": 1992},
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="detail_4",
            title="Snow Crash",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={"year_published": 2000},
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("year_published") == 1992

    def test_genres_still_merge_additively(self, temp_db: SQLiteDB) -> None:
        """Genres should still merge (not fill-only) even with fill-only scalars."""
        item_v1 = ContentItem(
            id="detail_5",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            metadata={
                "genres": ["RPG"],
                "description": "Space RPG.",
            },
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="detail_5",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            metadata={
                "genres": ["Action"],
                "description": "Different description.",
            },
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        genres = retrieved.metadata.get("genres", [])
        assert "RPG" in genres
        assert "Action" in genres
        # Description should be preserved (fill-only)
        assert retrieved.metadata.get("description") == "Space RPG."

    def test_remaining_metadata_json_merges_additively(self, temp_db: SQLiteDB) -> None:
        """Remaining metadata (non-column keys) should merge with existing taking precedence."""
        item_v1 = ContentItem(
            id="detail_6",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={
                "genres": ["Drama"],
                "custom_key_1": "original_value",
            },
        )
        db_id = temp_db.save_content_item(item_v1)

        item_v2 = ContentItem(
            id="detail_6",
            title="Firefly",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={
                "genres": ["Comedy"],
                "custom_key_1": "overwrite_attempt",
                "custom_key_2": "new_value",
            },
        )
        temp_db.save_content_item(item_v2)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        # Existing key should be preserved
        assert retrieved.metadata.get("custom_key_1") == "original_value"
        # New key should be filled
        assert retrieved.metadata.get("custom_key_2") == "new_value"


class TestUpdateItemFromUi:
    """Tests for update_item_from_ui (unrestricted UI editing)."""

    def test_update_status_backward(self, temp_db: SQLiteDB) -> None:
        """Status can go backward (completed -> unread) via UI edit."""
        item = ContentItem(
            id="ui_1",
            title="Completed Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        )
        db_id = temp_db.save_content_item(item)

        result = temp_db.update_item_from_ui(db_id=db_id, status="unread")
        assert result is True

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.UNREAD

    def test_update_rating_overwrite(self, temp_db: SQLiteDB) -> None:
        """Existing rating can be overwritten via UI edit."""
        item = ContentItem(
            id="ui_2",
            title="Rated Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, status="completed", rating=5)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 5

    def test_update_rating_clear(self, temp_db: SQLiteDB) -> None:
        """Setting rating to None clears it via UI edit."""
        item = ContentItem(
            id="ui_3",
            title="Rated Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, status="completed", rating=None)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating is None

    def test_update_review_overwrite(self, temp_db: SQLiteDB) -> None:
        """Existing review can be overwritten via UI edit."""
        item = ContentItem(
            id="ui_4",
            title="Reviewed Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            review="Old review",
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="completed", review="New review"
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "New review"

    def test_update_seasons_watched(self, temp_db: SQLiteDB) -> None:
        """Seasons watched is persisted in tv_show_details metadata."""
        item = ContentItem(
            id="ui_5",
            title="Test Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[1, 2, 3]
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons_watched") == [1, 2, 3]

    def test_update_auto_derive_status_all_watched(self, temp_db: SQLiteDB) -> None:
        """All seasons watched auto-derives status to completed."""
        item = ContentItem(
            id="ui_6",
            title="Short Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 3},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="unread", seasons_watched=[1, 2, 3]
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_update_auto_derive_status_some_watched(self, temp_db: SQLiteDB) -> None:
        """Partial seasons watched auto-derives status to currently_consuming."""
        item = ContentItem(
            id="ui_7",
            title="Long Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 10},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="unread", seasons_watched=[1, 2]
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.CURRENTLY_CONSUMING

    def test_update_auto_derive_status_none_watched(self, temp_db: SQLiteDB) -> None:
        """Empty seasons watched auto-derives status to unread."""
        item = ContentItem(
            id="ui_8",
            title="Unwatched Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, status="completed", seasons_watched=[])

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.UNREAD

    def test_update_nonexistent_item(self, temp_db: SQLiteDB) -> None:
        """Updating a nonexistent item returns False."""
        result = temp_db.update_item_from_ui(db_id=99999, status="unread")
        assert result is False

    def test_update_wrong_user(self, temp_db: SQLiteDB) -> None:
        """Updating with wrong user_id returns False."""
        item = ContentItem(
            id="ui_9",
            title="User 1 Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = temp_db.save_content_item(item, user_id=1)

        result = temp_db.update_item_from_ui(
            db_id=db_id, status="completed", user_id=999
        )
        assert result is False

        # Verify item unchanged
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.UNREAD

    def test_sync_still_forward_only_after_ui_update(self, temp_db: SQLiteDB) -> None:
        """save_content_item still enforces forward-only after UI edit.

        UI sets status backward to unread, then sync tries to set completed.
        Sync should advance forward. Separately, sync should not overwrite
        the rating that was set via UI and then cleared by another UI edit.
        """
        item = ContentItem(
            id="ui_10",
            title="Sync Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        )
        db_id = temp_db.save_content_item(item)

        # UI edit: go backward to unread, clear rating
        temp_db.update_item_from_ui(db_id=db_id, status="unread", rating=None)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.UNREAD
        assert retrieved.rating is None

        # Re-sync with completed status — should advance forward
        resync_item = ContentItem(
            id="ui_10",
            title="Sync Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        temp_db.save_content_item(resync_item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED


class TestTvSeasonSyncRegression:
    """Tests for TV show status regression when new seasons arrive via sync."""

    def test_sync_new_season_regresses_completed_to_consuming(
        self, temp_db: SQLiteDB
    ) -> None:
        """Completed TV show regresses to consuming when new season synced.

        Bug scenario: User watches all 50 seasons of Survivor, marks
        completed. Sonarr syncs season 51. Status should go back to
        currently_consuming since there's unwatched content.
        """
        item = ContentItem(
            id="tv_sync_1",
            title="Survivor",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 50},
        )
        db_id = temp_db.save_content_item(item)

        # User marks all 50 seasons watched via UI
        temp_db.update_item_from_ui(
            db_id=db_id,
            status="completed",
            seasons_watched=list(range(1, 51)),
        )

        # Verify completed
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

        # Sonarr syncs with season 51
        resync_item = ContentItem(
            id="tv_sync_1",
            title="Survivor",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"seasons": 51},
        )
        temp_db.save_content_item(resync_item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.CURRENTLY_CONSUMING
        # Total seasons should have increased
        assert retrieved.metadata is not None
        assert str(retrieved.metadata.get("seasons")) == "51"

    def test_sync_new_season_does_not_regress_when_ignored(
        self, temp_db: SQLiteDB
    ) -> None:
        """Ignored TV show stays completed when new season arrives.

        User completed and ignored the show — new season shouldn't
        change status.
        """
        item = ContentItem(
            id="tv_sync_2",
            title="Ignored Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        # User marks all seasons watched and ignores
        temp_db.update_item_from_ui(
            db_id=db_id,
            status="completed",
            seasons_watched=[1, 2, 3, 4, 5],
        )
        temp_db.set_item_ignored(db_id, ignored=True)

        # Sync with new season
        resync_item = ContentItem(
            id="tv_sync_2",
            title="Ignored Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"seasons": 6},
        )
        temp_db.save_content_item(resync_item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        # Status stays completed because item is ignored
        assert retrieved.status == ConsumptionStatus.COMPLETED
        # Season count still updated
        assert retrieved.metadata is not None
        assert str(retrieved.metadata.get("seasons")) == "6"

    def test_sync_no_seasons_watched_no_regression(self, temp_db: SQLiteDB) -> None:
        """No regression when user never used the season checklist.

        If there's no seasons_watched metadata, the sync should not
        change behavior — forward-only still applies.
        """
        item = ContentItem(
            id="tv_sync_3",
            title="No Checklist Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 3},
        )
        db_id = temp_db.save_content_item(item)

        # Sync with new season (no seasons_watched in metadata)
        resync_item = ContentItem(
            id="tv_sync_3",
            title="No Checklist Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            metadata={"seasons": 4},
        )
        temp_db.save_content_item(resync_item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        # Status stays completed — forward-only and no checklist data
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_sync_same_season_count_no_regression(self, temp_db: SQLiteDB) -> None:
        """No regression when season count hasn't changed."""
        item = ContentItem(
            id="tv_sync_4",
            title="Stable Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        # User marks all seasons watched
        temp_db.update_item_from_ui(
            db_id=db_id,
            status="completed",
            seasons_watched=[1, 2, 3, 4, 5],
        )

        # Re-sync with same season count
        resync_item = ContentItem(
            id="tv_sync_4",
            title="Stable Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 5},
        )
        temp_db.save_content_item(resync_item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        # Still completed — no new seasons
        assert retrieved.status == ConsumptionStatus.COMPLETED
