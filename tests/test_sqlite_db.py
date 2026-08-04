"""Tests for SQLite database manager."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ingestion.sources.radarr.radarr import RadarrPlugin
from src.ingestion.sources.sonarr.sonarr import SonarrPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import (
    DETAIL_FIELDS,
    ContentTypeFields,
    DetailField,
    FieldKind,
    to_json_array,
)
from src.storage.merge import (
    _DETAIL_TABLE_COLUMNS,
    assert_safe_identifier,
    normalize_title_for_matching,
    parse_json_list,
    resolve_status_forward,
)
from src.storage.schema import create_schema, write_enrichment_complete
from src.storage.sqlite_db import SQLiteDB
from src.utils.item_serialization import item_to_dict
from tests.test_interface_parity import BLANK_REVIEWS

# The instant the completion-stamping tests freeze the clock at, so each names
# the date it expects instead of re-deriving it from the helper under test, and
# so none of them straddles midnight between the write and the read back.
FROZEN_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
FROZEN_TODAY = date(2026, 3, 15)

# Every spelling of a blank review the doors must refuse, on the invariant the
# module docstring of src/storage/sqlite_db.py states across all three of them.
# A door is reached by callers no request model stands in front of — a source
# plugin, a private one — so its list is deliberately wider than the surfaces'.
# Spelled as a superset of that list rather than repeated, it cannot fall
# behind if the surfaces come to refuse another spelling.
BLANK_REVIEWS_AT_THE_DOOR = [*BLANK_REVIEWS, "\n"]


@pytest.fixture
def temp_db(tmp_path: Path) -> SQLiteDB:
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    return SQLiteDB(db_path)


def _insert_raw_item(
    temp_db: SQLiteDB,
    external_id: str,
    title: str,
    normalized_title: str,
    *,
    rating: int | None = None,
    review: str | None = None,
    date_completed: str | None = None,
    source: str = "test",
    status: str = "completed",
    ignored: bool = False,
) -> int:
    """Insert a video-game content_items row, bypassing save_content_item.

    Lets a test build the exact pair of duplicate rows a merge has to
    reconcile, including states save_content_item would never write.

    Returns the database ID of the inserted row.
    """
    with temp_db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, rating, review, date_completed, source, ignored)
               VALUES (1, ?, ?, ?, 'video_game', ?, ?, ?, ?, ?, ?)""",
            (
                external_id,
                title,
                normalized_title,
                status,
                rating,
                review,
                date_completed,
                source,
                1 if ignored else 0,
            ),
        )
        conn.commit()
        db_id = cursor.lastrowid
        assert db_id is not None
        return db_id


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
    items.append(
        ContentItem(
            id="book_in_progress",
            title="Book In Progress",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=3,
        )
    )

    for item in items:
        temp_db.save_content_item(item)

    # Single-status filters must only match the requested status
    completed = temp_db.get_content_items(status=ConsumptionStatus.COMPLETED)
    assert len(completed) == 3

    unread = temp_db.get_content_items(status=ConsumptionStatus.UNREAD)
    assert len(unread) == 2

    high_rated = temp_db.get_content_items(min_rating=4)
    assert len(high_rated) == 3

    books = temp_db.get_content_items(content_type=ContentType.BOOK)
    assert len(books) == 6


def test_get_content_items_unrated_only(temp_db: SQLiteDB) -> None:
    """unrated_only should return only items with rating IS NULL."""
    items = [
        ContentItem(
            id="completed_rated",
            title="Completed Rated",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="completed_unrated",
            title="Completed Unrated",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=None,
        ),
        ContentItem(
            id="unread_unrated",
            title="Unread Unrated",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            rating=None,
        ),
    ]
    for item in items:
        temp_db.save_content_item(item)

    # rating IS NULL across all statuses when status is not constrained
    unrated = temp_db.get_content_items(unrated_only=True)
    assert {item.id for item in unrated} == {"completed_unrated", "unread_unrated"}

    # Composes with status: only completed + unrated
    completed_unrated = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED, unrated_only=True
    )
    assert {item.id for item in completed_unrated} == {"completed_unrated"}

    # Composes with content_type
    movie_unrated = temp_db.get_content_items(
        content_type=ContentType.MOVIE, unrated_only=True
    )
    assert {item.id for item in movie_unrated} == {"unread_unrated"}


def test_get_content_items_unrated_only_respects_ignored(temp_db: SQLiteDB) -> None:
    """unrated_only must still honor the ignored filter, not bypass it.

    The web/CLI needs-rating path passes include_ignored=False by default, so an
    ignored completed+unrated item must not leak into that set; it only surfaces
    when ignored items are explicitly requested. unrated_only composes with the
    ignored filter rather than overriding it.
    """
    visible = ContentItem(
        id="visible_unrated",
        title="Visible Unrated",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=None,
        ignored=False,
    )
    hidden = ContentItem(
        id="ignored_unrated",
        title="Ignored Unrated",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=None,
        ignored=True,
    )
    temp_db.save_content_item(visible)
    temp_db.save_content_item(hidden)

    # include_ignored=False (what the web/CLI default to): ignored item excluded.
    visible_only = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED, unrated_only=True, include_ignored=False
    )
    assert {item.id for item in visible_only} == {"visible_unrated"}

    # include_ignored=True: ignored item is surfaced alongside the visible one.
    with_ignored = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED, unrated_only=True, include_ignored=True
    )
    assert {item.id for item in with_ignored} == {
        "visible_unrated",
        "ignored_unrated",
    }


def test_get_content_items_unrated_only_pagination(temp_db: SQLiteDB) -> None:
    """unrated_only composes with limit/offset for paginated needs-rating views."""
    for letter in ("A", "B", "C"):
        temp_db.save_content_item(
            ContentItem(
                id=f"unrated_{letter}",
                title=f"Title {letter}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=None,
            )
        )

    first_page = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED, unrated_only=True, limit=2, offset=0
    )
    assert [item.id for item in first_page] == ["unrated_A", "unrated_B"]

    second_page = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED, unrated_only=True, limit=2, offset=2
    )
    assert [item.id for item in second_page] == ["unrated_C"]


def test_get_content_items_unrated_only_sql_paginated_sort(temp_db: SQLiteDB) -> None:
    """unrated_only composes with SQL LIMIT/OFFSET under a non-title sort.

    The default "title" sort paginates in Python; "updated_at"/"created_at"/
    "rating" apply LIMIT/OFFSET in SQL, a separate code path. This pins that
    path: rated completed items must never leak across pages, and the unrated
    items must paginate in the requested updated_at DESC order. updated_at is
    set explicitly per row so the ordering is deterministic (the schema default
    CURRENT_TIMESTAMP has only second granularity and would tie within a test).
    """
    # external_id -> (rating, updated_at). Newest updated_at sorts first.
    # Rated rows are interleaved by timestamp to prove they are filtered, not
    # merely paginated off the end.
    rows = {
        "unrated_1": (None, "2025-01-06 00:00:00"),
        "rated_1": (4, "2025-01-05 00:00:00"),
        "unrated_2": (None, "2025-01-04 00:00:00"),
        "unrated_3": (None, "2025-01-03 00:00:00"),
        "rated_2": (5, "2025-01-02 00:00:00"),
        "unrated_4": (None, "2025-01-01 00:00:00"),
    }
    for external_id, (rating, _) in rows.items():
        temp_db.save_content_item(
            ContentItem(
                id=external_id,
                title=external_id.replace("_", " ").title(),
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=rating,
            )
        )

    with temp_db.connection() as conn:
        cursor = conn.cursor()
        for external_id, (_, updated_at) in rows.items():
            cursor.execute(
                "UPDATE content_items SET updated_at = ? WHERE external_id = ?",
                (updated_at, external_id),
            )
        conn.commit()

    first_page = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED,
        unrated_only=True,
        sort_by="updated_at",
        limit=2,
        offset=0,
    )
    assert [item.id for item in first_page] == ["unrated_1", "unrated_2"]

    second_page = temp_db.get_content_items(
        status=ConsumptionStatus.COMPLETED,
        unrated_only=True,
        sort_by="updated_at",
        limit=2,
        offset=2,
    )
    assert [item.id for item in second_page] == ["unrated_3", "unrated_4"]


def test_get_unconsumed_items(temp_db: SQLiteDB) -> None:
    """Test getting unconsumed items (UNREAD + CURRENTLY_CONSUMING)."""
    items = [
        ContentItem(
            id="item_0",
            title="Item 0",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="item_1",
            title="Item 1",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="item_2",
            title="Item 2",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
        ),
        ContentItem(
            id="item_3",
            title="Item 3",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        ),
        ContentItem(
            id="item_4",
            title="Item 4",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        ),
    ]

    for item in items:
        temp_db.save_content_item(item)

    unconsumed = temp_db.get_unconsumed_items()
    assert len(unconsumed) == 3
    assert all(
        item.status in {ConsumptionStatus.UNREAD, ConsumptionStatus.CURRENTLY_CONSUMING}
        for item in unconsumed
    )


def test_get_completed_items(temp_db: SQLiteDB) -> None:
    """Test getting completed items (COMPLETED + CURRENTLY_CONSUMING)."""
    items = [
        ContentItem(
            id="item_0",
            title="Item 0",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
        ),
        ContentItem(
            id="item_1",
            title="Item 1",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
        ContentItem(
            id="item_2",
            title="Item 2",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="item_3",
            title="Item 3",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            rating=5,
        ),
        ContentItem(
            id="item_4",
            title="Item 4",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    for item in items:
        temp_db.save_content_item(item)

    # All completed + currently consuming
    all_completed = temp_db.get_completed_items()
    assert len(all_completed) == 4

    # With min_rating filter
    completed = temp_db.get_completed_items(min_rating=4)
    assert len(completed) == 3
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
# Batch ID Lookup Tests
# ---------------------------------------------------------------------------


class TestGetContentItemsByDbIds:
    """Tests for SQLiteDB.get_content_items_by_db_ids batch lookup."""

    def test_returns_empty_for_empty_input(self, temp_db: SQLiteDB) -> None:
        """Empty db_ids list returns empty result without hitting the DB."""
        assert temp_db.get_content_items_by_db_ids([]) == []

    def test_returns_items_for_valid_ids(self, temp_db: SQLiteDB) -> None:
        """Returns ContentItem objects for all valid database IDs."""
        item1 = ContentItem(
            id="game-1",
            title="Portal",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        item2 = ContentItem(
            id="game-2",
            title="Portal 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        id1 = temp_db.save_content_item(item1)
        id2 = temp_db.save_content_item(item2)

        results = temp_db.get_content_items_by_db_ids([id1, id2])
        assert len(results) == 2
        titles = {r.title for r in results}
        assert titles == {"Portal", "Portal 2"}

    def test_silently_skips_missing_ids(self, temp_db: SQLiteDB) -> None:
        """Returns only found items; missing IDs are silently skipped."""
        item = ContentItem(
            id="game-1",
            title="Portal",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id = temp_db.save_content_item(item)

        results = temp_db.get_content_items_by_db_ids([db_id, 99999])
        assert len(results) == 1
        assert results[0].title == "Portal"

    def test_populates_db_id_on_returned_items(self, temp_db: SQLiteDB) -> None:
        """Each returned ContentItem has its db_id field set."""
        item = ContentItem(
            id="game-1",
            title="Portal",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id = temp_db.save_content_item(item)

        results = temp_db.get_content_items_by_db_ids([db_id])
        assert len(results) == 1
        assert results[0].db_id == db_id

    def test_handles_chunk_boundary(self, temp_db: SQLiteDB) -> None:
        """Items spanning multiple IN-clause chunks are all returned."""
        # Insert 502 items via raw SQL (crosses the 500-item chunk boundary)
        db_ids: list[int] = []
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            for i in range(502):
                cursor.execute(
                    """INSERT INTO content_items
                       (user_id, external_id, title, normalized_title,
                        content_type, status, source)
                       VALUES (1, ?, ?, ?, 'video_game', 'completed', 'test')""",
                    (f"chunk-{i}", f"Game {i}", f"game {i}"),
                )
                assert cursor.lastrowid is not None
                db_ids.append(cursor.lastrowid)
            conn.commit()

        results = temp_db.get_content_items_by_db_ids(db_ids)
        assert len(results) == 502


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
# Normalized Title Indexed Lookup Tests
# ---------------------------------------------------------------------------


class TestNormalizedTitleLookup:
    """Tests for the indexed normalized_title column used during save."""

    def test_insert_populates_normalized_title(self, temp_db: SQLiteDB) -> None:
        """Test that INSERT populates the normalized_title column."""
        item = ContentItem(
            id="nt_1",
            title="The Matrix™",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id = temp_db.save_content_item(item)

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT normalized_title FROM content_items WHERE id = ?",
                (db_id,),
            ).fetchone()
        assert row is not None
        assert row["normalized_title"] == normalize_title_for_matching("The Matrix™")

    def test_update_syncs_normalized_title(self, temp_db: SQLiteDB) -> None:
        """Test that UPDATE keeps normalized_title in sync with title."""
        item = ContentItem(
            id="nt_2",
            title="Old Title",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = temp_db.save_content_item(item)

        # Re-save with a new title
        updated = ContentItem(
            id="nt_2",
            title="New Title: Remastered",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id_2 = temp_db.save_content_item(updated)
        assert db_id == db_id_2

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT normalized_title FROM content_items WHERE id = ?",
                (db_id,),
            ).fetchone()
        assert row is not None
        assert row["normalized_title"] == normalize_title_for_matching(
            "New Title: Remastered"
        )

    def test_title_fallback_uses_indexed_lookup(self, temp_db: SQLiteDB) -> None:
        """Test that title-based dedup uses the indexed normalized_title column.

        Items from different sources with no external_id should still merge
        when their normalized titles match.
        """
        # Insert an item from source A (no external_id)
        item_a = ContentItem(
            title="The Last of Us™ Part I",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            source="steam",
        )
        db_id_a = temp_db.save_content_item(item_a)

        # Insert the same game from source B with different formatting
        item_b = ContentItem(
            title="The Last of Us Part I",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="gog",
        )
        db_id_b = temp_db.save_content_item(item_b)

        # Should merge into the same row
        assert db_id_a == db_id_b

    def test_title_fallback_different_types_no_merge(self, temp_db: SQLiteDB) -> None:
        """Test that title-based dedup respects content_type boundaries."""
        item_book = ContentItem(
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        item_movie = ContentItem(
            title="Dune",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id_book = temp_db.save_content_item(item_book)
        db_id_movie = temp_db.save_content_item(item_movie)

        # Different content types — should NOT merge
        assert db_id_book != db_id_movie

    def test_edition_variants_merge(self, temp_db: SQLiteDB) -> None:
        """Test that edition variants merge via normalized_title."""
        item_original = ContentItem(
            title="Skyrim",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        item_special = ContentItem(
            title="Skyrim Special Edition",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id_1 = temp_db.save_content_item(item_original)
        db_id_2 = temp_db.save_content_item(item_special)

        assert db_id_1 == db_id_2


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


class TestGetContentItemsSearch:
    """Tests for the search capability of get_content_items."""

    @staticmethod
    def _seed_movies(temp_db: SQLiteDB) -> None:
        """Seed a small movie library used by title-matching tests."""
        movies = [
            ContentItem(
                id="movie_die_hard",
                title="Die Hard (1988)",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
            ),
            ContentItem(
                id="movie_matrix",
                title="The Matrix",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
            ),
            ContentItem(
                id="movie_star_wars",
                title="Star Wars",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        for movie in movies:
            temp_db.save_content_item(movie)

    def test_search_none_is_noop(self, temp_db: SQLiteDB) -> None:
        """search=None returns the full unfiltered set (unchanged behavior)."""
        self._seed_movies(temp_db)
        assert len(temp_db.get_content_items(search=None)) == 3

    def test_search_empty_string_is_noop(self, temp_db: SQLiteDB) -> None:
        """An empty or whitespace search term does not filter."""
        self._seed_movies(temp_db)
        assert len(temp_db.get_content_items(search="")) == 3
        assert len(temp_db.get_content_items(search="   ")) == 3

    def test_search_exact_match(self, temp_db: SQLiteDB) -> None:
        """An exact (article/case-insensitive) title matches."""
        self._seed_movies(temp_db)
        results = temp_db.get_content_items(search="the matrix")
        assert [item.title for item in results] == ["The Matrix"]

    def test_search_partial_match(self, temp_db: SQLiteDB) -> None:
        """A substring term matches the longer title."""
        self._seed_movies(temp_db)
        results = temp_db.get_content_items(search="Die Hard")
        assert [item.title for item in results] == ["Die Hard (1988)"]

    def test_search_fuzzy_match(self, temp_db: SQLiteDB) -> None:
        """A typo'd term still matches via fuzzy matching."""
        self._seed_movies(temp_db)
        results = temp_db.get_content_items(search="Die Heard")
        assert [item.title for item in results] == ["Die Hard (1988)"]

    def test_search_no_results(self, temp_db: SQLiteDB) -> None:
        """An unrelated term returns nothing."""
        self._seed_movies(temp_db)
        assert temp_db.get_content_items(search="nonexistent zzz") == []

    def test_search_matches_book_author(self, temp_db: SQLiteDB) -> None:
        """Search matches a book's creator (author)."""
        temp_db.save_content_item(
            ContentItem(
                id="book_1",
                title="The Hobbit",
                author="J.R.R. Tolkien",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        results = temp_db.get_content_items(search="Tolkien")
        assert [item.title for item in results] == ["The Hobbit"]

    def test_search_matches_movie_director(self, temp_db: SQLiteDB) -> None:
        """Search matches a movie's creator (director)."""
        temp_db.save_content_item(
            ContentItem(
                id="movie_1",
                title="Jurassic Park",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                metadata={"director": "Steven Spielberg"},
            )
        )
        results = temp_db.get_content_items(search="Spielberg")
        assert [item.title for item in results] == ["Jurassic Park"]

    def test_search_matches_tv_creator(self, temp_db: SQLiteDB) -> None:
        """Search matches a TV show's creator (creators)."""
        temp_db.save_content_item(
            ContentItem(
                id="tv_1",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                metadata={"creators": "Vince Gilligan"},
            )
        )
        results = temp_db.get_content_items(search="Gilligan")
        assert [item.title for item in results] == ["Breaking Bad"]

    def test_search_matches_game_developer(self, temp_db: SQLiteDB) -> None:
        """Search matches a video game's creator (developer)."""
        temp_db.save_content_item(
            ContentItem(
                id="game_1",
                title="Half-Life",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                metadata={"developer": "Valve"},
            )
        )
        results = temp_db.get_content_items(search="Valve")
        assert [item.title for item in results] == ["Half-Life"]

    def test_search_ands_with_type_filter(self, temp_db: SQLiteDB) -> None:
        """Search combines with a content_type filter (AND)."""
        temp_db.save_content_item(
            ContentItem(
                id="movie_avatar",
                title="Avatar",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        temp_db.save_content_item(
            ContentItem(
                id="game_avatar",
                title="Avatar: Frontiers of Pandora",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            )
        )
        results = temp_db.get_content_items(
            search="Avatar", content_type=ContentType.MOVIE
        )
        assert [item.title for item in results] == ["Avatar"]

    def test_search_ands_with_status_filter(self, temp_db: SQLiteDB) -> None:
        """Search combines with a status filter (AND)."""
        temp_db.save_content_item(
            ContentItem(
                id="alien_done",
                title="Alien",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        temp_db.save_content_item(
            ContentItem(
                id="aliens_unread",
                title="Aliens",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        results = temp_db.get_content_items(
            search="Alien", status=ConsumptionStatus.UNREAD
        )
        assert [item.title for item in results] == ["Aliens"]

    def test_search_pagination_pages_over_filtered_set(self, temp_db: SQLiteDB) -> None:
        """limit/offset page over the full searched+sorted set, not one DB page.

        Twelve "Hero" movies match the search alongside non-matching noise.
        Paging with limit=5 must walk the matching set in title order, so
        offset=5 returns the next five matches (not five raw rows from the
        unfiltered query that happen to follow).
        """
        for i in range(12):
            temp_db.save_content_item(
                ContentItem(
                    id=f"hero_{i:02d}",
                    title=f"Hero {i:02d}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                )
            )
        # Non-matching noise interleaved in the table.
        for i in range(8):
            temp_db.save_content_item(
                ContentItem(
                    id=f"noise_{i:02d}",
                    title=f"Villain {i:02d}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                )
            )

        page1 = temp_db.get_content_items(search="Hero", limit=5, offset=0)
        page2 = temp_db.get_content_items(search="Hero", limit=5, offset=5)
        page3 = temp_db.get_content_items(search="Hero", limit=5, offset=10)

        assert [item.title for item in page1] == [f"Hero {i:02d}" for i in range(5)]
        assert [item.title for item in page2] == [f"Hero {i:02d}" for i in range(5, 10)]
        assert [item.title for item in page3] == [
            f"Hero {i:02d}" for i in range(10, 12)
        ]

        all_matches = temp_db.get_content_items(search="Hero")
        assert len(all_matches) == 12

    def test_search_book_without_author_does_not_match_creator(
        self, temp_db: SQLiteDB
    ) -> None:
        """A book with author=None must not raise or spuriously creator-match.

        The creator lookup falls back to ``item.author`` for books; when that
        is None the bool(creator) guard must short-circuit so a creator-style
        search neither errors nor returns the authorless book.
        """
        temp_db.save_content_item(
            ContentItem(
                id="book_no_author",
                title="Untitled Manuscript",
                author=None,
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        # Searching for an author name does not match the authorless book.
        assert temp_db.get_content_items(search="Tolkien") == []
        # The same book still matches on its title.
        title_results = temp_db.get_content_items(search="Untitled Manuscript")
        assert [item.title for item in title_results] == ["Untitled Manuscript"]

    def test_search_item_missing_creator_metadata_key(self, temp_db: SQLiteDB) -> None:
        """Items whose metadata lacks the creator key match on title only.

        A movie with no "director" key must not raise (the metadata.get returns
        None) and must not match a creator-style search, while still matching
        its own title.
        """
        temp_db.save_content_item(
            ContentItem(
                id="movie_no_director",
                title="Mystery Film",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                metadata={},
            )
        )
        assert temp_db.get_content_items(search="Spielberg") == []
        title_results = temp_db.get_content_items(search="Mystery Film")
        assert [item.title for item in title_results] == ["Mystery Film"]

    def test_search_with_rating_sort_orders_and_paginates(
        self, temp_db: SQLiteDB
    ) -> None:
        """Search combined with a non-title sort preserves SQL ordering.

        With sort_by="rating", the SQL ORDER BY (rating DESC, title ASC) drives
        ordering; Python search filtering must keep that order, and limit/offset
        must page over the matched set. Three "Quest" movies with distinct
        ratings (plus non-matching noise) verify both ordering and pagination.
        """
        temp_db.save_content_item(
            ContentItem(
                id="quest_low",
                title="Quest Alpha",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=2,
            )
        )
        temp_db.save_content_item(
            ContentItem(
                id="quest_high",
                title="Quest Bravo",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        )
        temp_db.save_content_item(
            ContentItem(
                id="quest_mid",
                title="Quest Charlie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            )
        )
        temp_db.save_content_item(
            ContentItem(
                id="noise_high",
                title="Unrelated Film",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        )

        ordered = temp_db.get_content_items(search="Quest", sort_by="rating")
        assert [item.title for item in ordered] == [
            "Quest Bravo",
            "Quest Charlie",
            "Quest Alpha",
        ]

        page1 = temp_db.get_content_items(
            search="Quest", sort_by="rating", limit=2, offset=0
        )
        page2 = temp_db.get_content_items(
            search="Quest", sort_by="rating", limit=2, offset=2
        )
        assert [item.title for item in page1] == ["Quest Bravo", "Quest Charlie"]
        assert [item.title for item in page2] == ["Quest Alpha"]

    def test_search_empty_library(self, temp_db: SQLiteDB) -> None:
        """Searching an empty library returns an empty list, not an error."""
        assert temp_db.get_content_items(search="anything") == []

    def test_search_pagination_exact_multiple_boundary(self, temp_db: SQLiteDB) -> None:
        """When matched count is an exact multiple of the page size.

        Edge probed by QA: ten matches with limit=5 yields two full pages and
        an empty third page (offset past the end), with no dropped or
        duplicated matches at the boundary.
        """
        for i in range(10):
            temp_db.save_content_item(
                ContentItem(
                    id=f"page_match_{i:02d}",
                    title=f"Boundary {i:02d}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                )
            )
        page1 = temp_db.get_content_items(search="Boundary", limit=5, offset=0)
        page2 = temp_db.get_content_items(search="Boundary", limit=5, offset=5)
        page3 = temp_db.get_content_items(search="Boundary", limit=5, offset=10)

        assert [item.title for item in page1] == [f"Boundary {i:02d}" for i in range(5)]
        assert [item.title for item in page2] == [
            f"Boundary {i:02d}" for i in range(5, 10)
        ]
        assert page3 == []
        # No overlap or gaps across the two full pages.
        combined = [item.id for item in page1] + [item.id for item in page2]
        assert len(set(combined)) == 10

    def test_search_offset_beyond_matched_set(self, temp_db: SQLiteDB) -> None:
        """An offset past the end of the matched set returns an empty list."""
        temp_db.save_content_item(
            ContentItem(
                id="solo_match",
                title="Solitary",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        assert temp_db.get_content_items(search="Solitary", offset=10) == []

    def test_search_case_and_punctuation_insensitive(self, temp_db: SQLiteDB) -> None:
        """Mixed case and punctuation differences still match.

        Edge probed by QA: a loud, punctuation-heavy query must match a title
        whose only difference is case and surrounding punctuation.
        """
        temp_db.save_content_item(
            ContentItem(
                id="spiderman",
                title="Spider-Man: Homecoming",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        results = temp_db.get_content_items(search="  SPIDER MAN homecoming!!  ")
        assert [item.title for item in results] == ["Spider-Man: Homecoming"]

    def test_search_matches_one_of_multiple_tv_creators(
        self, temp_db: SQLiteDB
    ) -> None:
        """A TV show with several comma-joined creators matches on any one.

        TMDB stores the plural ``creators`` field as a comma-joined string
        (e.g. "Vince Gilligan, Peter Gould"); searching for the second name
        must still surface the show.
        """
        temp_db.save_content_item(
            ContentItem(
                id="bcs",
                title="Better Call Saul",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                metadata={"creators": "Vince Gilligan, Peter Gould"},
            )
        )
        results = temp_db.get_content_items(search="Peter Gould")
        assert [item.title for item in results] == ["Better Call Saul"]


class TestPaginationWithoutLimit:
    """Regression tests for an offset requested without a limit.

    Bug reported: ``get_content_items(sort_by="updated_at", limit=None,
    offset=10)`` raised ``sqlite3.OperationalError: near "OFFSET": syntax
    error``, while the same call under the default title sort succeeded, so
    the failure depended on an unrelated parameter.

    Root cause: on the SQL-slicing path the LIMIT clause was appended only for
    a truthy limit while the OFFSET clause was appended independently, and
    SQLite's grammar accepts OFFSET only as a suffix of LIMIT.

    Fix: OFFSET is emitted only alongside LIMIT, using SQLite's unbounded
    ``LIMIT -1`` when an offset is requested with no limit. A falsy limit
    still means "no limit" on both the SQL and the Python slicing paths.
    """

    # external_id -> (title, updated_at, created_at, rating). Every column is
    # deliberately out of step with the others so each sort returns a
    # genuinely different ordering and a wrong ORDER BY cannot pass by luck.
    _ROWS = {
        "pager_1": ("Echo", "2025-01-05 00:00:00", "2024-02-01 00:00:00", 3),
        "pager_2": ("Alpha", "2025-01-04 00:00:00", "2024-02-05 00:00:00", 5),
        "pager_3": ("Delta", "2025-01-03 00:00:00", "2024-02-03 00:00:00", 1),
        "pager_4": ("Bravo", "2025-01-02 00:00:00", "2024-02-04 00:00:00", 4),
        "pager_5": ("Charlie", "2025-01-01 00:00:00", "2024-02-02 00:00:00", 2),
    }

    # The full, unsliced id order each sort must produce.
    _EXPECTED_ID_ORDER = {
        "title": ["pager_2", "pager_4", "pager_5", "pager_3", "pager_1"],
        "updated_at": ["pager_1", "pager_2", "pager_3", "pager_4", "pager_5"],
        "created_at": ["pager_2", "pager_4", "pager_3", "pager_5", "pager_1"],
        "rating": ["pager_2", "pager_4", "pager_1", "pager_5", "pager_3"],
    }

    @classmethod
    def _seed(cls, temp_db: SQLiteDB) -> None:
        """Seed five books, each sort column distinct and distinctly ordered.

        The timestamps are set explicitly because the schema defaults them to
        CURRENT_TIMESTAMP, which has only second granularity and would tie
        across all five rows, leaving the non-title sorts undetermined.
        """
        for external_id, (title, _, _, rating) in cls._ROWS.items():
            temp_db.save_content_item(
                ContentItem(
                    id=external_id,
                    title=title,
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=rating,
                )
            )
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            for external_id, (_, updated_at, created_at, _) in cls._ROWS.items():
                cursor.execute(
                    "UPDATE content_items SET updated_at = ?, created_at = ? "
                    "WHERE external_id = ?",
                    (updated_at, created_at, external_id),
                )
            conn.commit()

    def test_offset_without_limit_on_non_title_sort_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """An offset with no limit skips rows instead of raising a syntax error."""
        self._seed(temp_db)

        results = temp_db.get_content_items(sort_by="updated_at", limit=None, offset=2)

        assert [item.id for item in results] == ["pager_3", "pager_4", "pager_5"]

    @pytest.mark.parametrize("sort_by", ["title", "updated_at", "created_at", "rating"])
    @pytest.mark.parametrize(
        "limit, offset",
        [
            (None, 0),
            (None, 2),
            (None, 5),
            (None, 99),
            (0, 0),
            (0, 2),
            (2, 0),
            (2, 2),
            (2, 10),
            (100, 0),
            (100, 2),
        ],
    )
    def test_sql_and_python_slicing_agree_regression(
        self,
        temp_db: SQLiteDB,
        sort_by: str,
        limit: int | None,
        offset: int,
    ) -> None:
        """SQL slicing (non-title sorts) and Python slicing agree on every pair.

        The title sort slices in Python and the other three slice in SQL, so
        the same (limit, offset) must select the same window of each sort's
        full ordering. The pairs cover an absent limit, a zero limit, an
        offset landing exactly on the end of the set, an offset past the end,
        and a limit wider than the set, on both slicing paths.
        """
        self._seed(temp_db)
        expected = self._EXPECTED_ID_ORDER[sort_by][offset:]
        if limit:
            expected = expected[:limit]

        results = temp_db.get_content_items(sort_by=sort_by, limit=limit, offset=offset)

        assert [item.id for item in results] == expected

    @pytest.mark.parametrize("sort_by", ["updated_at", "created_at", "rating"])
    def test_offset_without_limit_on_every_sql_sort_regression(
        self, temp_db: SQLiteDB, sort_by: str
    ) -> None:
        """Every sort that slices in SQL survives an offset with no limit.

        The reported crash was found on sort_by="updated_at", but the broken
        clause was shared by all three SQL-slicing sorts, so fixing only the
        reported one would have left the same syntax error reachable through
        the other two.
        """
        self._seed(temp_db)

        results = temp_db.get_content_items(sort_by=sort_by, limit=None, offset=2)

        assert [item.id for item in results] == self._EXPECTED_ID_ORDER[sort_by][2:]

    def test_offset_without_limit_on_empty_library_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The unbounded-limit clause is still valid SQL with no rows to skip."""
        results = temp_db.get_content_items(sort_by="updated_at", limit=None, offset=2)

        assert results == []

    @pytest.mark.parametrize("sort_by", ["title", "updated_at"])
    @pytest.mark.parametrize("limit", [None, 2])
    def test_negative_offset_is_ignored_on_both_paths_regression(
        self, temp_db: SQLiteDB, sort_by: str, limit: int | None
    ) -> None:
        """A negative offset skips nothing rather than slicing from the end.

        Both paths gate on ``offset > 0``, so a negative offset must be inert;
        the Python path would otherwise slice a tail off the list and the two
        paths would disagree on a caller's typo.
        """
        self._seed(temp_db)
        expected = self._EXPECTED_ID_ORDER[sort_by]
        if limit:
            expected = expected[:limit]

        results = temp_db.get_content_items(sort_by=sort_by, limit=limit, offset=-1)

        assert [item.id for item in results] == expected

    def test_search_with_offset_and_no_limit_returns_the_tail_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A search term forces the Python path, which must page the same way.

        Search moves slicing into Python whatever the sort, so the
        offset-without-limit combination has to hold there too; it is the
        branch a caller lands on the moment they pass a search term. The term
        "a" matches every seeded title but Echo, so the offset has a matched
        set larger than itself to skip into.
        """
        self._seed(temp_db)

        matched = temp_db.get_content_items(
            sort_by="updated_at", search="a", limit=None
        )
        assert [item.id for item in matched] == [
            "pager_2",
            "pager_3",
            "pager_4",
            "pager_5",
        ]

        tail = temp_db.get_content_items(
            sort_by="updated_at", search="a", limit=None, offset=2
        )
        assert [item.id for item in tail] == ["pager_4", "pager_5"]


class TestToJsonArrayRegression:
    """Regression tests for to_json_array() bare string handling.

    Bug reported: TV show genres stored as bare strings like ``"Drama"``
    instead of JSON arrays ``'["Drama"]'``.  Downstream code expecting
    JSON arrays would fail to parse them, resulting in single-genre
    items that weakly matched everything via broad Jaccard overlap.

    Root cause: ``to_json_array()`` returned bare strings unchanged
    (``if isinstance(val, str): return val``).

    Fix: Bare strings are now wrapped in a JSON array; only strings
    that already start with ``[`` are passed through.
    """

    def test_bare_string_wrapped_in_json_array_regression(self) -> None:
        """A bare string like 'Drama' should become '["Drama"]'."""
        result = to_json_array("Drama")
        assert result == '["Drama"]'

    def test_existing_json_array_unchanged(self) -> None:
        """A string that is already a JSON array should be returned as-is."""
        result = to_json_array('["Drama", "Action"]')
        assert result == '["Drama", "Action"]'

    def test_list_converted_to_json(self) -> None:
        """A Python list should be serialized to JSON."""
        result = to_json_array(["Drama"])
        assert result == '["Drama"]'

    def test_none_returns_none(self) -> None:
        """None input should return None."""
        result = to_json_array(None)
        assert result is None

    def test_multi_element_list(self) -> None:
        """A multi-element list should serialize correctly."""
        result = to_json_array(["Drama", "Action", "Comedy"])
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
        assert resolve_status_forward(None, "completed") == "completed"
        assert resolve_status_forward(None, "unread") == "unread"

    def test_forward_progression_unread_to_consuming(self) -> None:
        assert resolve_status_forward("unread", "currently_consuming") == (
            "currently_consuming"
        )

    def test_forward_progression_consuming_to_completed(self) -> None:
        assert resolve_status_forward("currently_consuming", "completed") == "completed"

    def test_forward_progression_unread_to_completed(self) -> None:
        assert resolve_status_forward("unread", "completed") == "completed"

    def test_same_status_keeps_same(self) -> None:
        assert resolve_status_forward("completed", "completed") == "completed"

    def test_backward_blocked_completed_to_unread(self) -> None:
        """Completed status should never regress to unread."""
        assert resolve_status_forward("completed", "unread") == "completed"

    def test_backward_blocked_completed_to_consuming(self) -> None:
        """Completed status should never regress to currently_consuming."""
        assert resolve_status_forward("completed", "currently_consuming") == "completed"

    def test_backward_blocked_consuming_to_unread(self) -> None:
        """Currently_consuming should never regress to unread."""
        assert (
            resolve_status_forward("currently_consuming", "unread")
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


class TestBlankReviewNeverFillsTheColumn:
    """Regression tests for a blank review reaching the fill-only leg.

    Bug reported: ``_upsert_content_item`` filled an empty ``review`` column
    from any incoming value that was not ``None``, whitespace included. A
    blank is then indistinguishable from a review the user wrote, so the
    fill-only rule refuses every later value and the field is blocked for
    good. ``complete_content_item`` hit this first: the upsert runs before
    ``_write_completion``, so the blank was already stored by the time that
    method's own guard was reached.
    Root cause: the guard lived on ``_write_completion`` and on each surface's
    request validation, never on the leg that does the filling — so a source
    plugin yielding a whitespace CSV cell poisoned the column just as a chat
    completion did.
    Fix: the upsert treats a blank incoming review as no review at all, on
    both the fill and the insert leg, so nothing a door writes can block the
    column.
    """

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS_AT_THE_DOOR)
    def test_insert_stores_null_for_a_blank_review_regression(
        self, temp_db: SQLiteDB, blank_review: str
    ) -> None:
        """A new row gets NULL, not the whitespace the source sent."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="book_blank_new",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review=blank_review,
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review is None

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS_AT_THE_DOOR)
    def test_a_blank_review_does_not_block_a_later_fill_regression(
        self, temp_db: SQLiteDB, blank_review: str
    ) -> None:
        """A real review still lands after a blank one was synced first."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="book_blank_fill",
                title="Foundation",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                review=blank_review,
            )
        )

        temp_db.save_content_item(
            ContentItem(
                id="book_blank_fill",
                title="Foundation",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review="Classic sci-fi",
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Classic sci-fi"

    def test_a_blank_review_does_not_overwrite_a_stored_one(
        self, temp_db: SQLiteDB
    ) -> None:
        """Guard: the fill-only rule is unchanged for a review that exists."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="book_blank_keep",
                title="Neuromancer",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review="Cyberpunk classic",
            )
        )

        temp_db.save_content_item(
            ContentItem(
                id="book_blank_keep",
                title="Neuromancer",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review="   ",
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Cyberpunk classic"


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

    def test_an_empty_text_value_leaves_the_column_open(
        self, temp_db: SQLiteDB
    ) -> None:
        """A blank value stores as NULL, so a later sync still fills it.

        The fill-only rule tests ``is not None``, so a column holding ``""``
        would refuse every value after it, with nothing in the app to clear
        it. ``to_text`` answering an empty value with None is the whole of
        what keeps the column open, and this is the pair it is claimed for.
        """
        blank = ContentItem(
            id="detail_blank_isbn",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            metadata={"isbn": ""},
        )
        db_id = temp_db.save_content_item(blank)

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT isbn FROM book_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()
        assert row["isbn"] is None

        temp_db.save_content_item(
            ContentItem(
                id="detail_blank_isbn",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                metadata={"isbn": "9780441013593"},
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata["isbn"] == "9780441013593"

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

    def test_update_seasons_watched_stamps_dates(self, temp_db: SQLiteDB) -> None:
        """Newly checked-off seasons are stamped with an ISO timestamp."""
        item = ContentItem(
            id="ui_5a",
            title="Test Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[1]
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        dates = retrieved.metadata.get("seasons_watched_dates")
        assert set(dates.keys()) == {"1"}
        datetime.fromisoformat(dates["1"].replace("Z", "+00:00"))

    def test_update_seasons_watched_preserves_and_drops_dates(
        self, temp_db: SQLiteDB
    ) -> None:
        """Existing stamps survive re-saves; unchecked seasons lose their stamp."""
        item = ContentItem(
            id="ui_5b",
            title="Test Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[1]
        )
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        first_stamp = retrieved.metadata["seasons_watched_dates"]["1"]

        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[1, 2]
        )
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        dates = retrieved.metadata["seasons_watched_dates"]
        assert dates["1"] == first_stamp
        assert "2" in dates

        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[2]
        )
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        dates = retrieved.metadata["seasons_watched_dates"]
        assert set(dates.keys()) == {"2"}

        # Unchecking every season empties the dates map entirely, not just
        # the season that was dropped.
        temp_db.update_item_from_ui(db_id=db_id, status="unread", seasons_watched=[])
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata["seasons_watched_dates"] == {}

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


class TestEditDoorNeverStoresABlankReview:
    """Regression tests for a blank review reaching the edit door's write.

    Bug: ``update_item_from_ui`` wrote whatever string it was handed, so a
    blank ``review`` landed in the column as ``""``. No user hit it — every
    caller today either refuses a blank (``PATCH /api/items/{id}`` and
    ``library edit``) or drops it before the call (chat's
    ``_supplied_review``), and the edit dialog sends null once the box is
    empty. The defect is that the door depends on that and says nothing about
    it: the next caller — a bulk-edit endpoint, a new subcommand, a private
    plugin — inherits the poison with no test failing, and a stored blank
    reads as a review the user wrote and refuses every later import for that
    column, permanently.
    Root cause: the guards added to the other two write legs
    (``_upsert_content_item``'s fill and insert legs, ``_write_completion``)
    left this third one relying on its callers, where nothing states the
    requirement and nothing checks it.
    Fix: the door normalises a blank to NULL itself. It clears rather than
    ignores because that is what the surfaces in front already decide — an
    emptied review box is a clear, the instruction ``library edit`` spells
    ``--clear-review``.
    """

    @staticmethod
    def _reviewed(temp_db: SQLiteDB) -> int:
        """One completed book carrying a review the user wrote."""
        return temp_db.save_content_item(
            ContentItem(
                id="ui_blank_review",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review="Loved it",
            )
        )

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS_AT_THE_DOOR)
    def test_a_blank_review_clears_the_column_regression(
        self, temp_db: SQLiteDB, blank_review: str
    ) -> None:
        """The column holds NULL, not the whitespace the caller passed."""
        db_id = self._reviewed(temp_db)

        temp_db.update_item_from_ui(
            db_id=db_id, status="completed", review=blank_review
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review is None

    def test_a_review_cleared_this_way_can_still_be_filled_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The harm the NULL prevents: a later import can still fill it."""
        db_id = self._reviewed(temp_db)
        temp_db.update_item_from_ui(db_id=db_id, status="completed", review="   ")

        temp_db.save_content_item(
            ContentItem(
                id="ui_blank_review",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review="Imported from Goodreads",
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Imported from Goodreads"

    def test_a_written_review_is_stored_exactly_as_given(
        self, temp_db: SQLiteDB
    ) -> None:
        """Guard: the blank check reads the value, it does not rewrite it.

        Only emptiness is decided by stripping. Trimming what the user typed
        would be this door editing their words.
        """
        db_id = self._reviewed(temp_db)

        temp_db.update_item_from_ui(
            db_id=db_id, status="completed", review="  Still thinking  "
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "  Still thinking  "


class TestUpdateItemFromUiRegression:
    """Regression tests for update_item_from_ui bugs."""

    def test_update_seasons_watched_stamps_only_newly_checked_season_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Only a season newly ticked in this edit is stamped with `now`.

        Bug reported: checking one more season on a show that already had
        undated watched seasons stamped every undated-but-watched season
        with the current time, inventing dates for seasons watched long
        before dates were tracked.
        Root cause: the dates map was rebuilt as
        ``{season: existing_dates.get(season, now) for season in
        seasons_watched}``, which defaults to `now` for *any* undated
        season in the incoming list — not just ones newly added in this
        edit.
        Fix: capture the previous ``seasons_watched`` before overwriting it,
        and only stamp `now` for a season that is both new to the incoming
        list and not already dated.
        """
        item = ContentItem(
            id="ui_5c",
            title="Test Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 5},
        )
        db_id = temp_db.save_content_item(item)

        # Season 1 has an existing date; season 3 was watched but is
        # undated (e.g. imported before date tracking existed).
        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[1]
        )
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        season_1_stamp = retrieved.metadata["seasons_watched_dates"]["1"]

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM tv_show_details WHERE content_item_id = ?",
                (db_id,),
            )
            metadata = json.loads(cursor.fetchone()["metadata"])
            metadata["seasons_watched"] = [1, 3]
            metadata["seasons_watched_dates"] = {"1": season_1_stamp}
            cursor.execute(
                "UPDATE tv_show_details SET metadata = ?" " WHERE content_item_id = ?",
                (json.dumps(metadata), db_id),
            )
            conn.commit()

        # User checks off season 2, leaving season 3 as it was: watched but
        # undated.
        temp_db.update_item_from_ui(
            db_id=db_id, status="currently_consuming", seasons_watched=[1, 2, 3]
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        dates = retrieved.metadata["seasons_watched_dates"]
        assert dates["1"] == season_1_stamp  # preserved
        datetime.fromisoformat(dates["2"].replace("Z", "+00:00"))  # newly stamped
        assert "3" not in dates  # previously watched but undated: not invented


class TestPartialEditPreservesUnsentFields:
    """Regression tests for partial edits erasing fields they never mentioned.

    Bug reported: editing an item without passing a rating or review — a
    status-only edit from the library UI, or ``library edit --genre X`` —
    silently nulled both. The rating is the taste signal, so the item also
    stopped contributing to preference analysis, and the value was
    unrecoverable.
    Root cause: ``update_item_from_ui`` always wrote ``rating = ?, review = ?``
    with whatever the parameters held, and both defaulted to None, so "not
    supplied" and "clear it" were the same value.
    Fix: the parameters default to the UNSET sentinel and the SET clause is
    built from the fields actually supplied. None still means "clear it".
    """

    def test_status_only_edit_preserves_rating_and_review_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A status-only edit leaves the stored rating and review alone."""
        item = ContentItem(
            id="partial_1",
            title="Rated Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            review="Loved it",
        )
        db_id = temp_db.save_content_item(item)

        assert temp_db.update_item_from_ui(db_id=db_id, status="completed") is True

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 5
        assert retrieved.review == "Loved it"

    def test_rating_edit_preserves_review_regression(self, temp_db: SQLiteDB) -> None:
        """Changing only the rating leaves the review alone (and vice versa)."""
        item = ContentItem(
            id="partial_2",
            title="Reviewed Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            rating=2,
            review="Worth a look",
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, status="completed", rating=4)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 4
        assert retrieved.review == "Worth a look"

    def test_explicit_none_still_clears_rating_and_review(
        self, temp_db: SQLiteDB
    ) -> None:
        """Passing None explicitly still clears the field — UNSET is not None."""
        item = ContentItem(
            id="partial_3",
            title="Clearable Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
            review="Fine",
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(
            db_id=db_id, status="completed", rating=None, review=None
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating is None
        assert retrieved.review is None


class TestCompletionDateStamping:
    """Regression tests for in-app completions not recording a date.

    Bug reported: every in-app "mark complete" path set status=completed but
    left ``date_completed`` NULL, so the variety ladder — which orders
    completion events by that date — sank a just-finished item to its weakest
    rung and demoted the wrong genre.
    Root cause: ``update_item_from_ui`` never touched ``date_completed``, and
    the CLI/web completion paths built a ContentItem without one.
    Fix: an edit that moves an item into completed stamps today's date in the
    host's zone when the row has none, leaving an existing date (from an
    import, say) untouched.

    *Which* calendar day the stamp lands on is ``TestCompletionDateTimezone``'s
    subject, below; these tests only establish that a date is stamped at all.
    """

    def test_ui_completion_stamps_date_completed_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Marking an undated item complete records today's date."""
        item = ContentItem(
            id="stamp_1",
            title="Unread Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = temp_db.save_content_item(item)

        with patch("src.utils.dates.utc_now", return_value=FROZEN_NOW):
            temp_db.update_item_from_ui(db_id=db_id, status="completed")

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == FROZEN_TODAY

    def test_existing_completion_date_is_not_overwritten(
        self, temp_db: SQLiteDB
    ) -> None:
        """A date the user or an import already recorded survives the edit."""
        item = ContentItem(
            id="stamp_2",
            title="Imported Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2020, 1, 1),
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, status="completed", rating=4)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2020, 1, 1)

    def test_non_completed_edit_does_not_stamp_a_date(self, temp_db: SQLiteDB) -> None:
        """An edit that does not resolve to completed records no date."""
        item = ContentItem(
            id="stamp_3",
            title="In Progress Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, status="currently_consuming")

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed is None

    def test_unrelated_edit_does_not_invent_a_completion_date_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Editing a genre on an already-completed, undated item invents a date.

        Bug reported: an import that carries no completion date (a hand-made
        CSV, most Goodreads exports) leaves completed items undated. Editing
        one of them — a genre fix, a tag, a review — stamps today's date, so
        the app now believes the user finished a years-old book today. The
        variety ladder is ordered by that date, so a metadata edit silently
        moves that item to the top rung and demotes its genre hardest.
        Root cause: ``update_item_from_ui`` stamps whenever the *resolved*
        status is completed, rather than when the status actually transitions
        to completed, and it never reads the row's existing status to tell the
        two apart.
        Fix: stamp only on a real transition into completed. This is the rule
        the same method already applies to season dates — "a season that was
        already watched but has no date is left undated rather than inventing
        one" — applied to the item's own completion date.
        """
        item = ContentItem(
            id="stamp_5",
            title="Undated Import",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id = temp_db.save_content_item(item)
        seeded = temp_db.get_content_item(db_id)
        assert seeded is not None
        assert seeded.date_completed is None

        temp_db.update_item_from_ui(
            db_id=db_id, status="completed", genres=["Science Fiction"]
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed is None


class TestCompletionDateTimezone:
    """Regression tests for in-app completions dated by the UTC calendar day.

    Bug reported: a user in America/Los_Angeles marking something complete at
    21:00 got tomorrow's date. Every in-app stamping site read
    ``datetime.now(UTC).date()``, so west of UTC an evening completion was
    dated a day ahead — while an imported timestamp was narrowed to the host's
    zone. Two calendars fed the same variety-ladder ordering, and the ``TZ`` a
    Docker operator sets was honoured for one and ignored for the other.
    Root cause: the UTC instant was narrowed to a date without converting it
    to the host's zone first.
    Fix: every site stamps ``local_today()``, the counterpart of the helper
    that narrows imported timestamps.

    The clock is frozen at an instant whose UTC day differs from the host's,
    which is the only way to tell the implementations apart — under the
    suite's default UTC they agree — and which also stops these assertions
    disagreeing with a live clock across UTC midnight.
    """

    LOCAL_EVENING = datetime(2026, 3, 15, 4, 0, tzinfo=UTC)  # 21:00 on the 14th in LA

    def test_ui_completion_stamps_the_host_calendar_day_regression(
        self, temp_db: SQLiteDB, host_timezone
    ) -> None:
        """An edit that completes an item dates it by the day the user lived."""
        host_timezone("America/Los_Angeles")
        db_id = temp_db.save_content_item(
            ContentItem(
                id="tz_1",
                title="Evening Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        with patch("src.utils.dates.utc_now", return_value=self.LOCAL_EVENING):
            temp_db.update_item_from_ui(db_id=db_id, status="completed")

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2026, 3, 14)

    def test_explicit_completion_stamps_the_host_calendar_day_regression(
        self, temp_db: SQLiteDB, host_timezone
    ) -> None:
        """The `complete` door dates a new item by the day the user lived."""
        host_timezone("America/Los_Angeles")

        with patch("src.utils.dates.utc_now", return_value=self.LOCAL_EVENING):
            db_id = temp_db.complete_content_item(
                ContentItem(
                    id=None,
                    title="Evening Film",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                )
            )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2026, 3, 14)

    def test_tv_season_checklist_completion_stamps_the_host_day(
        self, temp_db: SQLiteDB, host_timezone
    ) -> None:
        """Completing a show by ticking its last season dates it the same way.

        The TV path derives the status from the season checklist rather than
        taking the caller's, so it reaches the stamp by a different route than
        the plain edit above.
        """
        host_timezone("America/Los_Angeles")
        db_id = temp_db.save_content_item(
            ContentItem(
                id="tz_tv_1",
                title="Evening Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={"seasons": 2},
            )
        )

        with patch("src.utils.dates.utc_now", return_value=self.LOCAL_EVENING):
            temp_db.update_item_from_ui(
                db_id=db_id, status="currently_consuming", seasons_watched=[1, 2]
            )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED
        assert retrieved.date_completed == date(2026, 3, 14)


class TestCompleteContentItem:
    """Regression tests for the explicit-completion door.

    Bug reported: `recommendinator complete` (and POST /api/complete) on an
    item imported with ``date_completed = 2020-01-01`` rewrote the date to
    today, silently losing a date the user owned — inside the change whose
    whole thesis is that user-owned state is never silently lost. That date
    feeds the variety ladder's ordering.
    Root cause: both interfaces built the item with today's date and persisted
    through ``save_content_item``, whose later-date-wins rule takes today over
    any past date; they then issued a second write through the edit door to
    apply the rating, so the two writes were separate transactions and the
    same block was duplicated across the CLI/web boundary.
    Fix: one storage entry point does find-or-create plus the user-owned write
    in a single transaction, and a completion carrying no date fills an empty
    one rather than replacing a stored one.
    """

    def _seeded(
        self,
        temp_db: SQLiteDB,
        *,
        rating: int | None = 5,
        review: str | None = "Loved it",
        date_completed: date | None = date(2020, 1, 1),
        status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
    ) -> int:
        """Store one book the user already owns values on."""
        return temp_db.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                author="Frank Herbert",
                content_type=ContentType.BOOK,
                status=status,
                rating=rating,
                review=review,
                date_completed=date_completed,
            )
        )

    def test_creates_the_item_when_the_library_has_no_match(
        self, temp_db: SQLiteDB
    ) -> None:
        """Completing something new stores it, completed, with what was given."""
        db_id = temp_db.complete_content_item(
            ContentItem(
                id=None,
                title="Piranesi",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
                review="Strange and lovely",
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.title == "Piranesi"
        assert retrieved.status == ConsumptionStatus.COMPLETED
        assert retrieved.rating == 4
        assert retrieved.review == "Strange and lovely"
        assert retrieved.date_completed is not None

    def test_existing_completion_date_is_preserved_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A stored completion date survives a completion that supplies none."""
        db_id = self._seeded(temp_db)

        temp_db.complete_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=2,
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2020, 1, 1)

    def test_missing_completion_date_is_filled(self, temp_db: SQLiteDB) -> None:
        """An item completed without a date gets today's."""
        db_id = self._seeded(temp_db, date_completed=None)

        with patch("src.utils.dates.utc_now", return_value=FROZEN_NOW):
            temp_db.complete_content_item(
                ContentItem(
                    id="book-1",
                    title="Dune",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                )
            )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == FROZEN_TODAY

    def test_supplied_rating_and_review_overwrite_the_stored_ones(
        self, temp_db: SQLiteDB
    ) -> None:
        """An explicit completion is a user action, so its values win."""
        db_id = self._seeded(temp_db)

        temp_db.complete_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=2,
                review="On reflection, overrated",
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 2
        assert retrieved.review == "On reflection, overrated"

    def test_omitted_rating_and_review_leave_the_stored_ones(
        self, temp_db: SQLiteDB
    ) -> None:
        """Completing without a rating does not erase the one already there."""
        db_id = self._seeded(temp_db)

        temp_db.complete_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 5
        assert retrieved.review == "Loved it"

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS_AT_THE_DOOR)
    def test_blank_review_does_not_replace_the_stored_one_regression(
        self, temp_db: SQLiteDB, blank_review: str
    ) -> None:
        """A blank review leaves the review the user wrote where it is.

        Bug: this door overwrites, and it wrote the review whenever it was not
        None, so a completion carrying ``""`` replaced a real review with an
        empty string — which then reads as a review the user wrote and stops
        any later import from filling the field. While both completion
        surfaces still persisted through the fill-only sync door a blank could
        only ever land in an empty column, so moving them onto this door is
        what turned it into data loss.
        Root cause: the write guarded on ``review is not None`` alone, while
        ``POST /api/complete`` and ``complete --review`` both still accepted an
        empty or all-whitespace string.
        Fix: blank counts as "supplied none" here, and both surfaces refuse one
        outright. Whitespace is the same emptiness spelled differently, so every
        spelling the doors are checked against is covered.
        """
        db_id = self._seeded(temp_db)

        temp_db.complete_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review=blank_review,
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.review == "Loved it"

    def test_completion_and_creation_are_one_transaction_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A failed user write takes the row creation down with it.

        The two-write shape this replaced committed the row first, so an
        interruption before the second write left the item completed carrying
        the rating it had before — a smaller version of the loss this door
        exists to prevent. The stubbed failure stands in for that
        interruption; nothing may be committed when it happens.
        """
        with patch.object(
            SQLiteDB, "_write_completion", side_effect=RuntimeError("interrupted")
        ):
            with pytest.raises(RuntimeError):
                temp_db.complete_content_item(
                    ContentItem(
                        id="book-2",
                        title="Perdido Street Station",
                        content_type=ContentType.BOOK,
                        status=ConsumptionStatus.COMPLETED,
                        rating=4,
                    )
                )

        assert temp_db.get_content_items(content_type=ContentType.BOOK) == []

    def test_completion_outranks_the_new_season(self, temp_db: SQLiteDB) -> None:
        """Saying "I finished this" beats the sync rule about new seasons.

        A show whose season count has grown past what the user ticked off is
        regressed to currently_consuming by the sync pass the completion runs
        through. Someone who has just said they finished it means it.
        """
        db_id = temp_db.save_content_item(
            ContentItem(
                id="tv-1",
                title="Survivor",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
                metadata={"seasons": 2},
            )
        )
        temp_db.update_item_from_ui(
            db_id=db_id, status="completed", seasons_watched=[1, 2]
        )
        temp_db.save_content_item(
            ContentItem(
                id="tv-1",
                title="Survivor",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={"seasons": 3},
            )
        )

        temp_db.complete_content_item(
            ContentItem(
                id="tv-1",
                title="Survivor",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED


class TestCompletionDoorExplicitDate:
    """Regression tests for a completion date the user named being discarded.

    Bug reported: telling the assistant "I finished Dune last Tuesday" left an
    item that an import had dated later still carrying the import's date. The
    correction was accepted, reported back as done, and never written — the
    silent loss of user-owned state this door exists to prevent, running
    backwards.
    Root cause: the date rode in on the ContentItem, so it met
    ``_upsert_content_item``'s later-date-wins sync rule on the way through,
    and ``_write_completion``'s COALESCE then preserved whatever survived it.
    A date earlier than the stored one never reached the column.
    Fix: the door reads the caller's date off the item and hands it to
    ``_write_completion`` as an argument of its own, which writes it as given.
    Only a caller supplying no date takes the fill-when-empty path.

    Chat is the only surface that names a date; ``complete`` and
    ``POST /api/complete`` accept none. The no-date path they share is pinned
    by ``TestCompleteContentItem`` above — fill-when-empty by
    ``test_missing_completion_date_is_filled``, preserve-when-present by
    ``test_existing_completion_date_is_preserved_regression`` — and this fix
    must leave both as they are.
    """

    def _seeded(self, temp_db: SQLiteDB, stored: date | None) -> int:
        """Store one completed book carrying *stored* as its date."""
        return temp_db.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=stored,
            )
        )

    def _complete_on(self, temp_db: SQLiteDB, supplied: date) -> None:
        """Complete that book again, naming *supplied* as the date."""
        temp_db.complete_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=supplied,
            )
        )

    def test_an_explicit_date_fills_an_undated_item(self, temp_db: SQLiteDB) -> None:
        """A named date lands on an item that has none, instead of today's."""
        db_id = self._seeded(temp_db, None)

        self._complete_on(temp_db, date(2024, 1, 15))

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2024, 1, 15)

    def test_an_explicit_date_later_than_the_stored_one_is_written(
        self, temp_db: SQLiteDB
    ) -> None:
        """A named date replaces an earlier one, as a correction should."""
        db_id = self._seeded(temp_db, date(2020, 1, 1))

        self._complete_on(temp_db, date(2024, 1, 15))

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2024, 1, 15)

    def test_an_explicit_date_earlier_than_the_stored_one_is_written_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A named date replaces a later one too — that is the defect above."""
        db_id = self._seeded(temp_db, date(2026, 12, 1))

        self._complete_on(temp_db, date(2026, 7, 28))

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2026, 7, 28)


class TestIgnoredSyncDoor:
    """Tests for what a synced ``ignored`` value is allowed to do.

    The ignore flag is user-owned, and the rule is that only a stated value
    counts: True and False both win — that is how exporting a library, editing
    it and re-importing un-ignores something on purpose — while None means the
    source said nothing and the stored flag stands. These pin that rule at the
    door, where it is decided, rather than at the importers that feed it: a
    source emitting a concrete False because its author defaulted the field is
    the failure mode that cost users their ignore list, and the door is the
    only place every source passes through.
    """

    def _ignored_item(self, temp_db: SQLiteDB) -> int:
        """Store one book and ignore it, as the user would in the app."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="ignore-1",
                title="Ignored Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        temp_db.set_item_ignored(db_id, ignored=True)
        return db_id

    def _resync(self, temp_db: SQLiteDB, ignored: bool | None) -> None:
        """Re-import the same book with the flag the file states (or does not)."""
        temp_db.save_content_item(
            ContentItem(
                id="ignore-1",
                title="Ignored Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                ignored=ignored,
            )
        )

    def test_unstated_flag_leaves_the_ignore_alone(self, temp_db: SQLiteDB) -> None:
        """A file that says nothing about the flag cannot clear it."""
        db_id = self._ignored_item(temp_db)

        self._resync(temp_db, ignored=None)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.ignored is True

    def test_stated_false_un_ignores_the_item(self, temp_db: SQLiteDB) -> None:
        """A file stating false clears the flag, so the round trip still works."""
        db_id = self._ignored_item(temp_db)

        self._resync(temp_db, ignored=False)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.ignored is False

    def test_stated_true_ignores_the_item(self, temp_db: SQLiteDB) -> None:
        """A file stating true sets the flag on an item that was not ignored."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="ignore-1",
                title="Ignored Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        self._resync(temp_db, ignored=True)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.ignored is True

    def test_new_item_with_no_stated_flag_is_not_ignored(
        self, temp_db: SQLiteDB
    ) -> None:
        """A first import that says nothing creates an ordinary, visible item."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="ignore-2",
                title="New Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.ignored is False


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

    def test_sync_keeps_later_existing_date_over_earlier_incoming_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Regression test: a sync must not clobber a later date with an earlier one.

        Bug reported: after a Trakt re-sync, a season's watch date could
        regress to an earlier value, and the whole ``seasons_watched_dates``
        map could also be frozen at its first written value (dropping a
        newly-watched season's date entirely).
        Root cause: ``_save_detail_table``'s remaining-metadata merge treated
        ``seasons_watched_dates`` either as ordinary existing-wins metadata
        (freezing the whole dict, so a new season's date never appears) or,
        in a later revision, as plain incoming-wins (letting a stale sync
        date overwrite a later known one) — neither compared the two
        timestamps.
        Fix: per-season merge via ``later_iso_timestamp`` — the later of the
        existing and incoming date wins, and a season present on only one
        side is carried over.
        """
        first = ContentItem(
            id="trakt:show1",
            title="Regression Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={
                "seasons_watched": [1],
                "seasons_watched_dates": {"1": "2026-06-01T00:00:00+00:00"},
            },
        )
        db_id = temp_db.save_content_item(first)

        # A later sync: season 1's incoming date is stale (earlier than what
        # we already know), and season 2 is newly watched.
        resync = ContentItem(
            id="trakt:show1",
            title="Regression Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={
                "seasons_watched": [1, 2],
                "seasons_watched_dates": {
                    "1": "2026-01-01T00:00:00+00:00",
                    "2": "2026-06-02T00:00:00+00:00",
                },
            },
        )
        temp_db.save_content_item(resync)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons_watched_dates") == {
            "1": "2026-06-01T00:00:00+00:00",  # later existing date kept
            "2": "2026-06-02T00:00:00+00:00",  # new season gap-filled
        }
        # seasons_watched stays existing-wins: a Trakt sync must not clobber
        # manual check-offs.
        assert retrieved.metadata.get("seasons_watched") == [1]

    def test_sync_updates_to_genuinely_later_incoming_date_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Regression test: a genuinely newer Trakt watch must still update the date.

        Bug reported: a season's watch date could get stuck at a stale value
        even when Trakt reported a genuinely more recent watch.
        Root cause: an existing-wins merge of ``seasons_watched_dates`` never
        compares timestamps, so it can never move a date forward.
        Fix: per-season merge via ``later_iso_timestamp`` lets a later
        incoming date win while still protecting a later existing date (see
        the sibling regression test above).
        """
        first = ContentItem(
            id="trakt:show2",
            title="Regression Show 2",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={
                "seasons_watched": [1],
                "seasons_watched_dates": {"1": "2026-01-01T00:00:00+00:00"},
            },
        )
        db_id = temp_db.save_content_item(first)

        resync = ContentItem(
            id="trakt:show2",
            title="Regression Show 2",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={
                "seasons_watched": [1],
                "seasons_watched_dates": {"1": "2026-06-01T00:00:00+00:00"},
            },
        )
        temp_db.save_content_item(resync)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons_watched_dates") == {
            "1": "2026-06-01T00:00:00+00:00"
        }

    def test_sync_gap_fills_dates_onto_pre_feature_row_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A sync gap-fills seasons_watched_dates onto a row that predates the feature.

        Bug scenario: rows created before ``seasons_watched_dates`` existed
        (or ingested by a source that never sends it) used to rely on a
        one-time schema backfill (``_seed_season_watched_dates``) to
        acquire a date on upgrade. That backfill has been removed, so the
        ordinary per-season merge in ``_save_detail_table`` must be able to
        gap-fill dates onto such a row the first time a sync actually sends
        them — not just onto rows that already carry the key.
        """
        item = ContentItem(
            id="trakt:show3",
            title="Pre-Feature Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={"seasons_watched": [1]},
        )
        db_id = temp_db.save_content_item(item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert "seasons_watched_dates" not in retrieved.metadata

        resync = ContentItem(
            id="trakt:show3",
            title="Pre-Feature Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={
                "seasons_watched": [1],
                "seasons_watched_dates": {"1": "2026-06-01T00:00:00+00:00"},
            },
        )
        temp_db.save_content_item(resync)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons_watched_dates") == {
            "1": "2026-06-01T00:00:00+00:00"
        }


class TestTvSeasonCountFromTraktMetadata:
    """Season counts written under the alias the Trakt plugin actually uses.

    Bug reported: a show whose metadata carried ``total_seasons`` left
    ``tv_show_details.seasons`` NULL, because the write config only knew the
    key ``seasons`` and dumped ``total_seasons`` into the free-form metadata
    blob. Everything reading the season count off the column then went blind:
    the API and CLI reported ``total_seasons: null``, the web edit dialog had
    no season checklist, and ticking every season resolved to
    currently_consuming forever because the count read as 0.

    Fix: the ``seasons`` column accepts ``total_seasons`` as an alias, the
    same way ``genres`` accepts ``genre`` and ``platforms`` accepts
    ``platform``, and the alias is a known key so it stops being duplicated
    into the blob.

    Trakt is the only producer of the alias. ``total_seasons`` is also what
    the CSV and JSON templates call the *column*, but both generic importers
    translate it onto the canonical ``seasons`` before the item reaches
    storage — they share ``CONTENT_TYPE_COLUMNS`` in
    ``src/ingestion/sources/generic_csv/generic_csv.py`` — so neither
    exercises this path. Trakt writes the alias straight into metadata, which
    its own suite pins.
    """

    @staticmethod
    def _save_trakt_show(temp_db: SQLiteDB) -> int:
        """Save a show shaped the way the Trakt plugin produces one."""
        return temp_db.save_content_item(
            ContentItem(
                id="trakt:1388",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                source="trakt",
                metadata={"total_seasons": 5, "seasons_watched": [1, 2, 3, 4, 5]},
            )
        )

    def test_total_seasons_metadata_populates_seasons_column_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """``total_seasons`` reaches the seasons column and the shared dict."""
        db_id = self._save_trakt_show(temp_db)

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.metadata["seasons"] == 5
        assert item_to_dict(retrieved)["total_seasons"] == 5

    def test_all_seasons_watched_resolves_completed_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Ticking every season completes the show instead of sticking."""
        db_id = self._save_trakt_show(temp_db)

        temp_db.update_item_from_ui(
            db_id=db_id,
            status="currently_consuming",
            seasons_watched=[1, 2, 3, 4, 5],
        )

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_total_seasons_is_not_duplicated_into_metadata_blob_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The alias is a known key, so the blob does not keep a second copy."""
        db_id = self._save_trakt_show(temp_db)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM tv_show_details WHERE content_item_id = ?",
                (db_id,),
            )
            blob = json.loads(cursor.fetchone()["metadata"])

        assert "total_seasons" not in blob


class TestYearAndRuntimeFromImport:
    """Release year and runtime written under the names the sources use.

    Bug reported: a Radarr movie or a Sonarr/Trakt show synced into an empty
    library showed no year, and the movie no runtime, until an enrichment
    provider happened to fill them in. Exporting the library left the ``year``
    and ``runtime_minutes`` columns blank for the same items.

    Root cause: those plugins write ``year`` and ``runtime_minutes``, but the
    detail-table config only knew the canonical ``release_year`` and
    ``runtime``, so both values fell into the free-form metadata blob and the
    columns stayed NULL. Only the enrichment providers write the canonical
    spellings, so exactly the unenriched items were affected.

    Fix: the ``release_year`` and ``runtime`` columns accept ``year`` and
    ``runtime_minutes`` as aliases, the same way ``seasons`` accepts
    ``total_seasons``, and each alias is a known key so it stops being
    duplicated into the blob.

    The fixtures come from the plugins' own metadata extractors: a
    hand-written dict is how the mismatch survived a green suite.
    """

    @staticmethod
    def _save_radarr_movie(temp_db: SQLiteDB) -> int:
        """Save the movie Radarr produces for a representative API payload."""
        return temp_db.save_content_item(
            ContentItem(
                id="tmdb:27205",
                title="Inception",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                source="radarr",
                metadata=RadarrPlugin().build_metadata(
                    {
                        "title": "Inception",
                        "tmdbId": 27205,
                        "year": 2010,
                        "runtime": 148,
                        "studio": "Warner Bros. Pictures",
                    }
                ),
            )
        )

    @staticmethod
    def _save_sonarr_show(temp_db: SQLiteDB) -> int:
        """Save the show Sonarr produces for a representative API payload."""
        return temp_db.save_content_item(
            ContentItem(
                id="tvdb:81189",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                source="sonarr",
                metadata=SonarrPlugin().build_metadata(
                    {
                        "title": "Breaking Bad",
                        "tvdbId": 81189,
                        "year": 2008,
                        "network": "AMC",
                        "statistics": {"seasonCount": 5},
                    }
                ),
            )
        )

    def test_movie_year_and_runtime_populate_their_columns_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Radarr's ``year`` and ``runtime_minutes`` reach the movie columns."""
        db_id = self._save_radarr_movie(temp_db)

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.metadata["release_year"] == 2010
        assert retrieved.metadata["runtime"] == 148

    def test_movie_aliases_are_not_duplicated_into_metadata_blob_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The aliases are known keys, so the blob keeps no second copy."""
        db_id = self._save_radarr_movie(temp_db)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM movie_details WHERE content_item_id = ?",
                (db_id,),
            )
            blob = json.loads(cursor.fetchone()["metadata"])

        assert "year" not in blob
        assert "runtime_minutes" not in blob

    def test_show_year_populates_the_release_year_column_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Sonarr's ``year`` reaches the TV release year column."""
        db_id = self._save_sonarr_show(temp_db)

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.metadata["release_year"] == 2008
        assert retrieved.metadata["seasons"] == 5

    def test_show_year_is_not_duplicated_into_metadata_blob_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The TV alias is a known key too."""
        db_id = self._save_sonarr_show(temp_db)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM tv_show_details WHERE content_item_id = ?",
                (db_id,),
            )
            blob = json.loads(cursor.fetchone()["metadata"])

        assert "year" not in blob

    def test_canonical_keys_still_win_over_their_aliases(
        self, temp_db: SQLiteDB
    ) -> None:
        """An enriched item carrying both spellings keeps the canonical value."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="tmdb:27205",
                title="Inception",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                source="radarr",
                metadata={
                    "release_year": 2010,
                    "year": 1999,
                    "runtime": 148,
                    "runtime_minutes": 90,
                },
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.metadata["release_year"] == 2010
        assert retrieved.metadata["runtime"] == 148


class TestCreatorReadsBackAsAuthor:
    """The creator a provider writes reads back on every content type.

    Bug reported: a movie, TV show or video game always read back with
    ``author`` as None, so the library export wrote a blank
    director/creator/developer cell even for a TMDB-enriched item whose
    column the provider had filled.

    Root cause: only ``book.author`` was declared as the creator, so the read
    path left the other three types' creator columns in the metadata dict and
    never set ``ContentItem.author`` from them.

    Fix: every content type declares one ``FieldKind.CREATOR`` column, which
    the read path lifts onto ``author`` and out of the metadata dict.
    """

    @pytest.mark.parametrize(
        ("content_type", "metadata_key", "creator"),
        [
            (ContentType.BOOK, "author", "Patrick Rothfuss"),
            (ContentType.MOVIE, "director", "Denis Villeneuve"),
            (ContentType.TV_SHOW, "creators", "Vince Gilligan, Peter Gould"),
            (ContentType.VIDEO_GAME, "developer", "Team Cherry"),
        ],
        ids=["book", "movie", "tv_show", "video_game"],
    )
    def test_provider_creator_key_becomes_the_author_regression(
        self,
        temp_db: SQLiteDB,
        content_type: ContentType,
        metadata_key: str,
        creator: str,
    ) -> None:
        """The key an enrichment provider writes reads back as the author."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id=f"creator-{content_type.value}",
                title="Enriched",
                content_type=content_type,
                status=ConsumptionStatus.UNREAD,
                metadata={metadata_key: creator},
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.author == creator
        assert metadata_key not in retrieved.metadata

    def test_tv_creator_alias_reaches_the_creators_column_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The singular ``creator`` a template row carries stores as ``creators``.

        The tv_show template column is ``creator`` and storage stores
        ``creators``, so the declared key and the stored one used to disagree
        and the value fell into the free-form blob.
        """
        db_id = temp_db.save_content_item(
            ContentItem(
                id="tv-creator-alias",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"creator": "Vince Gilligan"},
            )
        )

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT creators, metadata FROM tv_show_details"
                " WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()

        assert row["creators"] == "Vince Gilligan"
        assert "creator" not in json.loads(row["metadata"] or "{}")

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.author == "Vince Gilligan"

    def test_item_author_fills_the_creator_column_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """An importer's ``author`` reaches the column for every type.

        The import path puts the template's creator cell on
        ``ContentItem.author`` rather than in metadata, and the write path
        used to consult it for books alone — so an imported director was
        written nowhere.
        """
        db_id = temp_db.save_content_item(
            ContentItem(
                id="movie-author",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                author="Denis Villeneuve",
            )
        )

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT director FROM movie_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()

        assert row["director"] == "Denis Villeneuve"


class TestCreatorColumnEdges:
    """The boundaries of the creator column, once every type has one.

    The creator now crosses a codec (a plural key may arrive as a list) and
    a fill-only write rule, so the shapes below are the ones a plugin, an
    enrichment provider or a re-sync can actually produce.
    """

    def test_a_list_creator_joins_into_the_one_column(self, temp_db: SQLiteDB) -> None:
        """Several developers become one comma-joined name, like TMDB's."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="game-two-devs",
                title="Divinity",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"developers": ["Larian Studios", "Larian Belgium"]},
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.author == "Larian Studios, Larian Belgium"

    def test_an_empty_list_creator_stores_nothing(self, temp_db: SQLiteDB) -> None:
        """A plugin's empty list is no creator, not an empty-string one."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="game-no-devs",
                title="Unattributed",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"developers": []},
            )
        )

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT developer FROM video_game_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()

        assert row["developer"] is None
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.author is None
        assert "developers" not in retrieved.metadata

    def test_the_item_author_outranks_a_metadata_creator(
        self, temp_db: SQLiteDB
    ) -> None:
        """An importer's author beats a creator key riding in metadata."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="movie-both",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                author="Denis Villeneuve",
                metadata={"director": "Somebody Else"},
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.author == "Denis Villeneuve"

    def test_the_canonical_creators_key_outranks_the_creator_alias(
        self, temp_db: SQLiteDB
    ) -> None:
        """A show carrying both spellings keeps the one storage declares."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="tv-both-spellings",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"creators": "Vince Gilligan", "creator": "Somebody Else"},
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.author == "Vince Gilligan"
        assert "creator" not in retrieved.metadata

    def test_a_later_sync_does_not_overwrite_a_stored_creator(
        self, temp_db: SQLiteDB
    ) -> None:
        """The creator is fill-only, like every other non-list column."""
        first = temp_db.save_content_item(
            ContentItem(
                id="movie-fill-only",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                author="Denis Villeneuve",
            )
        )
        second = temp_db.save_content_item(
            ContentItem(
                id="movie-fill-only",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                author="Wrong Person",
            )
        )

        assert second == first
        retrieved = temp_db.get_content_item(first)
        assert retrieved is not None
        assert retrieved.author == "Denis Villeneuve"

    def test_a_later_sync_fills_a_creator_the_first_left_empty(
        self, temp_db: SQLiteDB
    ) -> None:
        """An enrichment pass supplies the creator an import had none of."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="movie-later-fill",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        temp_db.save_content_item(
            ContentItem(
                id="movie-later-fill",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"director": "Denis Villeneuve"},
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.author == "Denis Villeneuve"

    def test_a_non_latin_creator_reads_back_unchanged(self, temp_db: SQLiteDB) -> None:
        """A creator name outside ASCII survives the column verbatim."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="movie-unicode",
                title="Spirited Away",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"director": "宮崎駿"},
            )
        )

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.author == "宮崎駿"

    def test_plural_publishers_reach_the_singular_publisher_column(
        self, temp_db: SQLiteDB
    ) -> None:
        """GOG's ``publishers`` is the alias of the ``publisher`` column."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="game-publishers",
                title="Cyberpunk 2077",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"publishers": ["CD Projekt", "Warner Bros."]},
            )
        )

        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT publisher FROM video_game_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()

        assert row["publisher"] == "CD Projekt, Warner Bros."
        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata["publisher"] == "CD Projekt, Warner Bros."

    @pytest.mark.parametrize(
        ("content_type", "search_term"),
        [
            (ContentType.MOVIE, "Villeneuve"),
            (ContentType.TV_SHOW, "Gilligan"),
            (ContentType.VIDEO_GAME, "Cherry"),
        ],
        ids=["movie", "tv_show", "video_game"],
    )
    def test_library_search_finds_an_item_by_its_stored_creator(
        self, temp_db: SQLiteDB, content_type: ContentType, search_term: str
    ) -> None:
        """Search reads the creator off ``author`` for every content type."""
        creators = {
            ContentType.MOVIE: "Denis Villeneuve",
            ContentType.TV_SHOW: "Vince Gilligan",
            ContentType.VIDEO_GAME: "Team Cherry",
        }
        temp_db.save_content_item(
            ContentItem(
                id=f"search-{content_type.value}",
                title="Untitled",
                content_type=content_type,
                status=ConsumptionStatus.UNREAD,
                author=creators[content_type],
            )
        )

        results = temp_db.get_content_items(search=search_term)

        assert [item.title for item in results] == ["Untitled"]


class TestDetailTableWhitelist:
    """Tests for detail table whitelist validation in _save_detail_table."""

    def test_rejects_unknown_table_name(self, temp_db: SQLiteDB) -> None:
        """_save_detail_table raises ValueError for table not in whitelist.

        Validates the SQL injection defense-in-depth guard on ALLOWED_DETAIL_TABLES.
        """
        malicious_config = {
            "injected": ContentTypeFields(
                table="malicious_table; DROP TABLE users; --",
                table_alias="mt",
                metadata_alias="injected_metadata",
                # Every ContentTypeFields names one creator, so this spec
                # carries one to reach the guard it is here to exercise.
                fields=(
                    DetailField(
                        "author",
                        FieldKind.CREATOR,
                        column="author",
                        template_column="author",
                    ),
                    DetailField("title", FieldKind.TEXT, column="title"),
                ),
            )
        }
        item = ContentItem(
            id="test_1",
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"title": "Test"},
        )
        # Save the item first so we have a db_id
        db_id = temp_db.save_content_item(item)

        conn = temp_db._get_connection()
        cursor = conn.cursor()

        with (
            patch.dict(DETAIL_FIELDS, malicious_config),
            pytest.raises(ValueError, match="Unknown detail table"),
        ):
            temp_db._save_detail_table(cursor, db_id, item, "injected")


class TestParseJsonList:
    """Tests for merge.parse_json_list helper."""

    def test_none_returns_empty(self) -> None:
        assert parse_json_list(None) == []

    def test_empty_string_returns_empty(self) -> None:
        assert parse_json_list("") == []

    def test_valid_json_array(self) -> None:
        assert parse_json_list('["a", "b", "c"]') == ["a", "b", "c"]

    def test_non_list_json_returns_empty(self) -> None:
        assert parse_json_list('{"key": "value"}') == []

    def test_invalid_json_returns_empty(self) -> None:
        assert parse_json_list("not json") == []

    def test_converts_elements_to_strings(self) -> None:
        assert parse_json_list("[1, 2, 3]") == ["1", "2", "3"]


class TestAssertSafeIdentifier:
    """Tests for assert_safe_identifier SQL injection guard."""

    def test_valid_lowercase_identifier(self) -> None:
        assert_safe_identifier("some_column")

    def test_valid_identifier_with_digits(self) -> None:
        assert_safe_identifier("col_2")

    def test_identifier_with_space_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            assert_safe_identifier("bad column")

    def test_sql_injection_pattern_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            assert_safe_identifier("col; DROP TABLE users;--")

    def test_uppercase_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            assert_safe_identifier("BadColumn")

    def test_starting_with_digit_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            assert_safe_identifier("1col")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            assert_safe_identifier("")


class TestDetailTableColumnsConsistency:
    """Ensures _DETAIL_TABLE_COLUMNS stays in sync with the field declaration."""

    def test_detail_table_columns_matches_declaration(self) -> None:
        """_DETAIL_TABLE_COLUMNS must list exactly the declared columns.

        Both describe the detail table schema, and _DETAIL_TABLE_COLUMNS is
        deliberately independent of the declaration because it is the source
        of the ALLOWED_DETAIL_TABLES guard.  If a column is added to the
        declaration but not here, the merge logic in merge_detail_tables
        silently skips the new column.

        Membership is compared, not order: _DETAIL_TABLE_COLUMNS' order
        reaches nothing but the order of SET clauses in merge_detail_tables,
        so pinning it to the declaration would force cosmetic edits to
        merge.py every time this one is reordered.

        Duplicates are rejected on both sides, since a column named twice is
        invisible to the set comparison: two DetailFields declaring the same
        column with different select aliases would pass every other check
        here and read one column's value back under two names.
        """
        for content_type, spec in DETAIL_FIELDS.items():
            assert spec.table in _DETAIL_TABLE_COLUMNS, (
                f"Table {spec.table!r} (content_type={content_type!r}) "
                f"missing from _DETAIL_TABLE_COLUMNS"
            )
            assert set(_DETAIL_TABLE_COLUMNS[spec.table]) == set(spec.columns), (
                f"Column mismatch for {spec.table!r}: "
                f"_DETAIL_TABLE_COLUMNS={sorted(_DETAIL_TABLE_COLUMNS[spec.table])!r} "
                f"vs DETAIL_FIELDS={sorted(spec.columns)!r}"
            )
            assert len(set(_DETAIL_TABLE_COLUMNS[spec.table])) == len(
                _DETAIL_TABLE_COLUMNS[spec.table]
            ), f"Duplicate column in _DETAIL_TABLE_COLUMNS[{spec.table!r}]"
            assert len(set(spec.columns)) == len(
                spec.columns
            ), f"Duplicate column in DETAIL_FIELDS[{content_type!r}]"

        declared_tables = {spec.table for spec in DETAIL_FIELDS.values()}
        for table in _DETAIL_TABLE_COLUMNS:
            assert (
                table in declared_tables
            ), f"Table {table!r} in _DETAIL_TABLE_COLUMNS but not in DETAIL_FIELDS"


class TestCrossSourceDuplicateDetectionRegression:
    """Regression tests for cross-source duplicate detection and merging.

    Bug reported: When running a full sync, items from different sources
    (e.g., Steam "Fable Anniversary" with external_id="207170" and personal
    site "Fable: Anniversary" with external_id="fable-anniversary") created
    duplicate entries even though they represent the same game.

    Root cause: Two bugs in save_content_item:
    1. The normalized_title check only ran as a fallback when no external_id
       match was found.  Once both items existed with different external_ids,
       each sync found its own row and the title dedup was bypassed.
    2. The migration backfill used SQL lower(title) instead of the full
       Python normalize_title_for_matching(), so "fable: anniversary" !=
       "fable anniversary" and both rows were inserted.

    Fix: Added a cross-source dedup check after the external_id lookup
    that merges any duplicate row with the same normalized title.
    """

    def test_resave_triggers_cross_source_merge_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Re-saving an item merges a cross-source duplicate found by title.

        Exercises the cross-source dedup check in save_content_item
        (not the title-fallback path).  Two rows are created with different
        external_ids and matching normalized titles via raw SQL, then
        re-saving one triggers _merge_duplicate_into.
        """
        steam = ContentItem(
            id="207170",
            title="Fable Anniversary",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            source="steam",
            metadata={"genres": ["RPG", "Action"]},
        )
        steam_db_id = temp_db.save_content_item(steam)

        # Insert blog row with correct normalized_title (both rows exist)
        _insert_raw_item(
            temp_db,
            external_id="fable-anniversary",
            title="Fable: Anniversary",
            normalized_title="fable anniversary",
            source="personal_site_games",
        )

        # Verify two rows exist
        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 2

        # Re-save Steam item — triggers cross-source dedup
        temp_db.save_content_item(steam)

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1

        retrieved = temp_db.get_content_item(steam_db_id)
        assert retrieved is not None
        assert retrieved.id == "207170"  # Kept row retains its external_id
        assert retrieved.rating == 4

    def test_merge_preserves_rating_from_duplicate_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """_merge_duplicate_into fills rating from duplicate when kept is null.

        Both rows are created with different external_ids via raw SQL to
        ensure _merge_duplicate_into is exercised (not the title-fallback).
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-ds",
            title="Dark Souls",
            normalized_title="dark souls",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-ds",
            title="Dark Souls",
            normalized_title="dark souls",
            rating=5,
            review="Masterpiece",
            source="personal_site",
        )

        # Trigger cross-source merge via re-save
        steam = ContentItem(
            id="steam-ds",
            title="Dark Souls",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
        )
        temp_db.save_content_item(steam)

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.id == "steam-ds"  # Kept row retains its external_id
        assert retrieved.rating == 5
        assert retrieved.review == "Masterpiece"

    def test_merge_unions_seasons_watched_dates_with_later_date_winning_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Cross-source dedup merge unions seasons_watched_dates per season.

        Bug reported: consolidating a duplicate TV show found by title could
        drop a season the duplicate has a date for but the kept row doesn't,
        or freeze a season present in both rows at the kept row's possibly
        stale date.
        Root cause: ``_merge_detail_metadata``'s keep-wins metadata merge
        never compared per-season timestamps, so it could neither gap-fill
        a dup-only season nor let a genuinely later duplicate date win.
        Fix: per-season merge via ``later_iso_timestamp`` in
        ``_merge_detail_metadata``.

        The shared conflict season (2) is set up so the *duplicate* row's
        date is genuinely later than the kept item's own incoming date for
        that season. This matters because after ``_merge_duplicate_into``
        runs, ``save_content_item`` continues on to re-save the (unchanged)
        ``keep`` item via ``_save_detail_table``, which does its own
        later-wins re-merge against the DB row. If the conflict season's
        winner were the kept row's own date (as in an earlier, non-
        discriminating version of this test), that trailing re-merge would
        reconstruct the correct answer from ``keep``'s own incoming
        metadata alone — passing even if ``_merge_detail_metadata`` were
        broken and dropped the duplicate's data entirely. With the
        duplicate's date genuinely later, only a correct
        ``_merge_detail_metadata`` merge persists it to the DB row that the
        trailing re-merge reads back.

        Both rows are created with different external_ids and matching
        normalized titles (one via raw SQL, since _insert_raw_item only
        supports video games) so re-saving the kept item triggers
        _merge_duplicate_into (this exercises _merge_detail_metadata, not
        the resync path in _save_detail_table exercised by
        TestTvSeasonSyncRegression).
        """
        keep = ContentItem(
            id="trakt-show",
            title="Regression Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.CURRENTLY_CONSUMING,
            source="trakt",
            metadata={
                "seasons_watched_dates": {
                    "1": "2026-01-01T00:00:00+00:00",
                    "2": "2026-02-01T00:00:00+00:00",
                }
            },
        )
        keep_id = temp_db.save_content_item(keep)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title,
                    content_type, status, source)
                   VALUES (1, 'sonarr-show', 'Regression Show',
                           'regression show', 'tv_show',
                           'currently_consuming', 'sonarr')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO tv_show_details (content_item_id, metadata)
                   VALUES (?, ?)""",
                (
                    dup_id,
                    json.dumps(
                        {
                            "seasons_watched_dates": {
                                # Season 2: later than the kept row's date.
                                "2": "2026-05-01T00:00:00+00:00",
                                # Season 3: only on the duplicate row.
                                "3": "2026-03-01T00:00:00+00:00",
                            }
                        }
                    ),
                ),
            )
            conn.commit()

        # Re-save the kept item — triggers the cross-source dedup merge.
        temp_db.save_content_item(keep)

        all_shows = temp_db.get_content_items(content_type=ContentType.TV_SHOW)
        assert len(all_shows) == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons_watched_dates") == {
            "1": "2026-01-01T00:00:00+00:00",  # Only on the kept row
            "2": "2026-05-01T00:00:00+00:00",  # Duplicate's later date wins
            "3": "2026-03-01T00:00:00+00:00",  # Only on the duplicate row
        }

    def test_merge_does_not_overwrite_existing_rating_on_kept_row(
        self, temp_db: SQLiteDB
    ) -> None:
        """merge_scalar_columns does not overwrite kept row's rating.

        When both rows have a rating, the kept row's rating must be
        preserved — the duplicate's rating is discarded.  This exercises
        the deduplicate_items path; see
        test_cross_source_resave_preserves_kept_rating for the
        save_content_item path.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-bg",
            title="Baldur's Gate 3",
            normalized_title="baldurs gate 3",
            rating=4,
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-bg",
            title="Baldur's Gate 3",
            normalized_title="baldurs gate 3",
            rating=5,
            review="Amazing RPG",
            source="personal_site",
        )

        merged = temp_db.deduplicate_items()
        assert merged == 1

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.rating == 4  # Kept row's rating preserved
        assert retrieved.review == "Amazing RPG"  # Review filled from duplicate

    def test_cross_source_resave_preserves_kept_rating(self, temp_db: SQLiteDB) -> None:
        """save_content_item cross-source merge does not overwrite existing rating.

        Similar to test_merge_does_not_overwrite_existing_rating_on_kept_row
        but exercises the save_content_item cross-source path instead of
        deduplicate_items.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-skyrim",
            title="Skyrim",
            normalized_title="skyrim",
            rating=4,
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-skyrim",
            title="Skyrim",
            normalized_title="skyrim",
            rating=5,
            review="Classic RPG",
            source="blog",
        )

        # Re-save via save_content_item with a conflicting rating —
        # triggers cross-source merge.  The final rating=4 verifies the
        # combined outcome of merge_scalar_columns and save_content_item's
        # "set once" guards (this test cannot isolate which guard fires).
        steam = ContentItem(
            id="steam-skyrim",
            title="Skyrim",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
            rating=5,
        )
        temp_db.save_content_item(steam)

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert (
            retrieved.rating == 4
        )  # Kept row's rating preserved (not overwritten by 5)
        assert retrieved.review == "Classic RPG"  # Review filled from duplicate

    def test_merge_keeps_later_date_completed_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """_merge_duplicate_into keeps the later date_completed."""
        _insert_raw_item(
            temp_db,
            external_id="steam-hades",
            title="Hades",
            normalized_title="hades",
            date_completed="2024-01-15",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-hades",
            title="Hades",
            normalized_title="hades",
            date_completed="2024-06-20",
            source="personal_site",
        )

        merged = temp_db.deduplicate_items()
        assert merged == 1

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        assert all_games[0].date_completed == date(2024, 6, 20)

    def test_merge_does_not_overwrite_later_date_with_earlier(
        self, temp_db: SQLiteDB
    ) -> None:
        """_merge_duplicate_into does not replace a later date with an earlier one."""
        _insert_raw_item(
            temp_db,
            external_id="steam-celeste",
            title="Celeste",
            normalized_title="celeste",
            date_completed="2024-12-01",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-celeste",
            title="Celeste",
            normalized_title="celeste",
            date_completed="2024-03-15",
            source="personal_site",
        )

        merged = temp_db.deduplicate_items()
        assert merged == 1

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        assert all_games[0].date_completed == date(2024, 12, 1)

    def test_merge_all_null_dup_does_not_bump_updated_at_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """merge_scalar_columns skips UPDATE when duplicate has no scalar data.

        Bug: Merging a duplicate with NULL rating, review, and date_completed
        still issued an UPDATE that bumped updated_at, corrupting the
        user-facing sort order.
        Root cause: No early-exit guard — the UPDATE always fired.
        Fix: Added will_change guard that compares actual values and skips
        the UPDATE entirely when no data change would occur.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-portal",
            title="Portal",
            normalized_title="portal",
            rating=5,
            source="steam",
        )

        # Record the kept row's updated_at before merge
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM content_items WHERE id = ?", (keep_id,)
            )
            original_updated_at = cursor.fetchone()["updated_at"]

        # Insert a duplicate with all-null scalars
        _insert_raw_item(
            temp_db,
            external_id="blog-portal",
            title="Portal",
            normalized_title="portal",
            source="blog",
        )

        merged = temp_db.deduplicate_items()
        assert merged == 1

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM content_items WHERE id = ?", (keep_id,)
            )
            after_updated_at = cursor.fetchone()["updated_at"]

        assert after_updated_at == original_updated_at

    def test_merge_same_data_does_not_bump_updated_at_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """merge_scalar_columns skips UPDATE when both rows have identical data.

        Bug: The all-null-dup guard tested by
        test_merge_all_null_dup_does_not_bump_updated_at_regression did not
        cover the case where both rows have the same non-NULL values.  In that
        scenario the fill-only rules also produce no change, but the distinct
        code path (non-NULL comparison) was unexercised.
        Root cause: Missing test — the will_change guard handles this correctly
        but the "same non-NULL data" branch was never verified.
        Fix: Added this test to pin the no-op behaviour for identical data.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-portal",
            title="Portal",
            normalized_title="portal",
            rating=5,
            review="Brilliant",
            source="steam",
        )

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM content_items WHERE id = ?", (keep_id,)
            )
            original_updated_at = cursor.fetchone()["updated_at"]

        # Insert a duplicate with the same rating and review
        _insert_raw_item(
            temp_db,
            external_id="blog-portal",
            title="Portal",
            normalized_title="portal",
            rating=5,
            review="Brilliant",
            source="blog",
        )

        merged = temp_db.deduplicate_items()
        assert merged == 1

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM content_items WHERE id = ?", (keep_id,)
            )
            after_updated_at = cursor.fetchone()["updated_at"]

        assert after_updated_at == original_updated_at

    def test_deduplicate_items_merges_existing_duplicates_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """deduplicate_items finds and merges rows with matching normalized titles.

        Verifies the kept row (lowest id) retains its external_id and
        receives merged data from the duplicate.
        """
        _insert_raw_item(
            temp_db,
            external_id="steam-123",
            title="Portal 2",
            normalized_title="portal 2",
            rating=5,
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-portal",
            title="Portal 2",
            normalized_title="portal 2",
            review="Amazing game",
            source="personal_site",
        )

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 2

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        assert all_games[0].id == "steam-123"  # Kept: lowest db id
        assert all_games[0].rating == 5
        assert all_games[0].review == "Amazing game"

    def test_deduplicate_items_returns_zero_when_no_duplicates(
        self, temp_db: SQLiteDB
    ) -> None:
        """deduplicate_items returns 0 when there are no duplicates."""
        item = ContentItem(
            id="unique1",
            title="Unique Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        temp_db.save_content_item(item)
        assert temp_db.deduplicate_items() == 0

    def test_deduplicate_items_nonexistent_user_returns_zero(
        self, temp_db: SQLiteDB
    ) -> None:
        """deduplicate_items with a user_id that has no rows returns 0."""
        assert temp_db.deduplicate_items(user_id=999) == 0

    def test_deduplicate_items_respects_user_id_filter(self, temp_db: SQLiteDB) -> None:
        """deduplicate_items with user_id only deduplicates that user's items.

        Verifies that deduplicating user A's items does not touch user B's
        duplicate rows.
        """
        # Create a second user
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (id, username) VALUES (2, 'user_b')")
            conn.commit()

        # Insert duplicates for user 1 (default)
        _insert_raw_item(
            temp_db,
            external_id="a",
            title="Game X",
            normalized_title="game x",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="b",
            title="Game X",
            normalized_title="game x",
            source="blog",
        )

        # Insert duplicates for user 2
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (2, 'c', 'Game X', 'game x', 'video_game',
                           'completed', 'steam')""",
            )
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (2, 'd', 'Game X', 'game x', 'video_game',
                           'completed', 'blog')""",
            )
            conn.commit()

        # Dedup only user 1 — should merge user 1's pair only
        assert temp_db.deduplicate_items(user_id=1) == 1

        # User 1 should now have exactly 1 row — the lowest-id row survived
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM content_items WHERE user_id = 1")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT external_id FROM content_items WHERE user_id = 1")
            assert cursor.fetchone()["external_id"] == "a"

        # User 2's duplicates should be untouched
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM content_items WHERE user_id = 2")
            assert cursor.fetchone()[0] == 2  # Still 2 rows for user 2

    def test_merge_genres_additively(self, temp_db: SQLiteDB) -> None:
        """_merge_duplicate_into combines genres from both detail rows."""
        item_a = ContentItem(
            id="a1",
            title="Elden Ring",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
            metadata={"genres": ["RPG", "Action"]},
        )
        temp_db.save_content_item(item_a)

        # Insert a duplicate with different genres via raw SQL
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-elden', 'Elden Ring', 'elden ring',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres)
                   VALUES (?, ?)""",
                (dup_id, '["Souls-like", "Open World"]'),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        item = all_games[0]
        assert item.metadata is not None
        genres = item.metadata.get("genres", [])
        assert set(genres) == {"RPG", "Action", "Souls-like", "Open World"}

    def test_merge_moves_detail_row_when_kept_has_none(self, temp_db: SQLiteDB) -> None:
        """When kept row has no detail row, duplicate's detail row is moved."""
        # Insert kept row with no detail row
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-hollow",
            title="Hollow Knight",
            normalized_title="hollow knight",
            source="steam",
        )
        # Insert duplicate with a detail row
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-hollow', 'Hollow Knight', 'hollow knight',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, developer, genres)
                   VALUES (?, 'Team Cherry', '["Metroidvania"]')""",
                (dup_id,),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.author == "Team Cherry"
        genres = retrieved.metadata.get("genres", [])
        assert "Metroidvania" in genres

    def test_deduplicate_three_way_merge(self, temp_db: SQLiteDB) -> None:
        """deduplicate_items correctly merges three rows into one."""
        _insert_raw_item(
            temp_db,
            external_id="a",
            title="Disco Elysium",
            normalized_title="disco elysium",
            rating=5,
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="b",
            title="Disco Elysium",
            normalized_title="disco elysium",
            review="Brilliant writing",
            source="gog",
        )
        _insert_raw_item(
            temp_db,
            external_id="c",
            title="Disco Elysium",
            normalized_title="disco elysium",
            date_completed="2024-09-01",
            source="blog",
        )

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 2

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        item = all_games[0]
        assert item.id == "a"  # Kept: lowest db id
        assert item.rating == 5
        assert item.review == "Brilliant writing"
        assert item.date_completed == date(2024, 9, 1)

    def test_schema_migration_renormalizes_and_deduplicates(
        self, tmp_path: Path
    ) -> None:
        """Schema migration re-normalizes titles and merges exposed duplicates.

        Exercises the _renormalize_titles and _deduplicate_inline functions
        in schema.py by creating a raw database with stale lower(title)
        normalization, then calling create_schema to trigger the migration.
        """
        db_path = tmp_path / "migration_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        # First call creates the schema
        create_schema(conn)

        # Insert two rows with different normalized_titles that should match
        # after full normalization (simulating the lower(title) backfill bug)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, rating, source)
               VALUES (1, 'steam-207170', 'Fable Anniversary',
                       'fable anniversary', 'video_game', 'completed', 4, 'steam')"""
        )
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, review, source)
               VALUES (1, 'blog-fable', 'Fable: Anniversary',
                       'fable: anniversary', 'video_game', 'completed',
                       'Great game', 'personal_site')"""
        )
        conn.commit()

        # Verify two rows exist
        cursor.execute("SELECT COUNT(*) FROM content_items")
        assert cursor.fetchone()[0] == 2

        # Re-run create_schema — triggers _renormalize_titles + _deduplicate_inline
        create_schema(conn)

        # Should now have one row with merged data
        cursor.execute("SELECT COUNT(*) FROM content_items")
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT rating, review, normalized_title"
            " FROM content_items WHERE external_id = 'steam-207170'"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["rating"] == 4
        assert row["review"] == "Great game"
        assert row["normalized_title"] == "fable anniversary"

        conn.close()

    def test_schema_migration_deduplicates_with_bare_connection_regression(
        self, tmp_path: Path
    ) -> None:
        """create_schema sets row_factory even on a bare connection.

        Bug: merge_scalar_columns used named column access (row["rating"])
        but create_schema did not set row_factory, causing TypeError on
        bare sqlite3.connect() connections during migration dedup.
        Fix: create_schema now sets conn.row_factory = sqlite3.Row.
        """
        db_path = tmp_path / "bare_conn_test.db"
        conn = sqlite3.connect(db_path)
        # Intentionally no row_factory — this is the scenario that was broken
        conn.execute("PRAGMA foreign_keys = ON")

        create_schema(conn)

        # Insert duplicate rows
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, rating, source)
               VALUES (1, 'a', 'Test Game', 'test game', 'video_game',
                       'completed', 5, 'steam')"""
        )
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, source)
               VALUES (1, 'b', 'Test Game', 'test game', 'video_game',
                       'completed', 'blog')"""
        )
        conn.commit()

        # Verify two rows exist before the dedup migration
        cursor.execute("SELECT COUNT(*) FROM content_items")
        assert cursor.fetchone()[0] == 2

        # Re-run create_schema — triggers _renormalize_titles + _deduplicate_inline.
        # The first call already set row_factory; this verifies dedup runs
        # correctly and is idempotent on the second pass.
        create_schema(conn)

        cursor.execute("SELECT COUNT(*) FROM content_items")
        assert cursor.fetchone()[0] == 1

        # Verify the correct row survived (lowest id) with correct data
        cursor.execute("SELECT external_id, rating FROM content_items")
        row = cursor.fetchone()
        assert row["external_id"] == "a"
        assert row["rating"] == 5

        conn.close()

    def test_empty_title_skips_cross_source_dedup(self, temp_db: SQLiteDB) -> None:
        """Items with empty title skip cross-source dedup without crashing.

        Bug scenario: A ContentItem with title="" from a malformed ingestion
        source should be saved without triggering the cross-source dedup
        check (which requires a non-empty normalized title).
        Verified by inserting two items with the same external_id prefix
        but empty titles — both must survive (no dedup attempted).
        """
        item1 = ContentItem(
            id="empty-title-1",
            title="",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        item2 = ContentItem(
            id="empty-title-2",
            title="",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        db_id1 = temp_db.save_content_item(item1)
        db_id2 = temp_db.save_content_item(item2)
        assert db_id1 > 0
        assert db_id2 > 0
        assert db_id1 != db_id2  # Both rows saved separately — no dedup

    def test_content_type_boundary_prevents_cross_type_merge(
        self, temp_db: SQLiteDB
    ) -> None:
        """Items with the same normalized title but different content types are not merged.

        A book named "Dune" and a movie named "Dune" must remain as separate
        rows — the cross-source dedup only operates within the same content type.
        """
        book = ContentItem(
            id="dune-book",
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source="goodreads",
        )
        movie = ContentItem(
            id="dune-movie",
            title="Dune",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED,
            source="letterboxd",
        )
        temp_db.save_content_item(book)
        temp_db.save_content_item(movie)

        books = temp_db.get_content_items(content_type=ContentType.BOOK)
        movies = temp_db.get_content_items(content_type=ContentType.MOVIE)
        assert len(books) == 1
        assert len(movies) == 1

        # deduplicate_items should not merge them either
        assert temp_db.deduplicate_items() == 0

    def test_merge_monotonic_columns_keeps_higher_value(
        self, temp_db: SQLiteDB
    ) -> None:
        """merge_detail_tables keeps the higher value for monotonic columns.

        TV show seasons/episodes use monotonic merge: the higher value wins.
        Tests both directions: kept < dup (seasons: 2 vs 4, dup wins) and
        kept > dup (episodes: 20 vs 15, kept preserved).
        """
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            # Insert kept row: seasons=2, episodes=20
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'sonarr-bb', 'Breaking Bad', 'breaking bad',
                           'tv_show', 'completed', 'sonarr')""",
            )
            keep_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO tv_show_details
                   (content_item_id, seasons, episodes)
                   VALUES (?, 2, 20)""",
                (keep_id,),
            )
            # Insert duplicate row: seasons=4, episodes=15
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-bb', 'Breaking Bad', 'breaking bad',
                           'tv_show', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO tv_show_details
                   (content_item_id, seasons, episodes)
                   VALUES (?, 4, 15)""",
                (dup_id,),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        # seasons: dup (4) > kept (2), so 4 wins
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons") == 4
        # episodes: kept (20) > dup (15), so 20 is preserved
        assert retrieved.metadata.get("episodes") == 20

    def test_merge_metadata_additively_in_detail_table(self, temp_db: SQLiteDB) -> None:
        """merge_detail_tables merges metadata JSON additively.

        When both rows have metadata in a detail table, the merge should
        combine them with existing keys taking precedence.
        """
        # Insert kept item with metadata via save_content_item
        kept = ContentItem(
            id="steam-witcher",
            title="The Witcher 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
            metadata={"genres": ["RPG"], "playtime_hours": 120},
        )
        keep_id = temp_db.save_content_item(kept)

        # Insert duplicate with different metadata via raw SQL
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["Action"]', ?)""",
                (dup_id, json.dumps({"award": "GOTY 2015", "playtime_hours": 80})),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        # Genres merged additively
        genres = retrieved.metadata.get("genres", [])
        assert "RPG" in genres
        assert "Action" in genres
        # Metadata merged: existing key (playtime_hours=120) takes precedence
        assert retrieved.metadata.get("playtime_hours") == 120
        # New key from duplicate is added
        assert retrieved.metadata.get("award") == "GOTY 2015"

    def test_merge_skips_when_dup_has_no_detail_metadata(
        self, temp_db: SQLiteDB
    ) -> None:
        """Detail metadata merge is skipped when the duplicate has no metadata.

        _merge_detail_metadata returns None when dup_detail["metadata"]
        is NULL, preserving the kept row's metadata unchanged.
        """
        kept = ContentItem(
            id="steam-witcher",
            title="The Witcher 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
            metadata={"playtime_hours": 120},
        )
        keep_id = temp_db.save_content_item(kept)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            # Detail row with NULL metadata
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["Action"]', NULL)""",
                (dup_id,),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        # Original metadata preserved unchanged
        assert retrieved.metadata.get("playtime_hours") == 120
        # Genres still merged additively
        genres = retrieved.metadata.get("genres", [])
        assert "Action" in genres

    def test_merge_skips_when_dup_has_corrupt_detail_metadata(
        self, temp_db: SQLiteDB
    ) -> None:
        """Detail metadata merge is skipped when the duplicate has non-JSON metadata.

        _merge_detail_metadata returns None when dup metadata cannot
        be parsed as JSON, preserving the kept row's metadata.
        """
        kept = ContentItem(
            id="steam-witcher",
            title="The Witcher 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
            metadata={"playtime_hours": 120},
        )
        keep_id = temp_db.save_content_item(kept)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            # Detail row with corrupt (non-JSON) metadata
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["Action"]', 'not-valid-json{{{')""",
                (dup_id,),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("playtime_hours") == 120

    def test_merge_skips_when_kept_has_corrupt_detail_metadata(
        self, temp_db: SQLiteDB
    ) -> None:
        """Detail metadata merge is skipped when the kept row has non-JSON metadata.

        _merge_detail_metadata returns None when kept metadata cannot
        be parsed, preserving it as-is rather than overwriting with dup data.
        """
        # Insert kept item via raw SQL to set corrupt metadata
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'steam-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'steam')""",
            )
            keep_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["RPG"]', 'corrupt{json')""",
                (keep_id,),
            )

            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["Action"]', ?)""",
                (dup_id, json.dumps({"award": "GOTY 2015"})),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM video_game_details WHERE content_item_id = ?",
                (keep_id,),
            )
            row = cursor.fetchone()
            # Kept row's corrupt metadata is preserved (not overwritten)
            assert row is not None
            assert row["metadata"] == "corrupt{json"

    def test_merge_fills_when_kept_has_no_detail_metadata(
        self, temp_db: SQLiteDB
    ) -> None:
        """Detail metadata merge fills kept row when it has NULL metadata.

        _merge_detail_metadata correctly fills when keep_meta_raw
        is NULL (keep_meta starts as empty dict, merged = dup_meta).
        """
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'steam-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'steam')""",
            )
            keep_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["RPG"]', NULL)""",
                (keep_id,),
            )

            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["Action"]', ?)""",
                (dup_id, json.dumps({"award": "GOTY 2015"})),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM video_game_details WHERE content_item_id = ?",
                (keep_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            meta = json.loads(row["metadata"])
            assert meta == {"award": "GOTY 2015"}

    def test_merge_skips_when_dup_has_non_dict_detail_metadata(
        self, temp_db: SQLiteDB
    ) -> None:
        """Detail metadata merge is skipped when the duplicate has a JSON array.

        _merge_detail_metadata returns None when dup metadata is
        valid JSON but not a dict (e.g. a list), preserving kept metadata.
        """
        kept = ContentItem(
            id="steam-witcher",
            title="The Witcher 3",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            source="steam",
            metadata={"playtime_hours": 120},
        )
        keep_id = temp_db.save_content_item(kept)

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'blog-witcher', 'The Witcher 3', 'witcher 3',
                           'video_game', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            # Valid JSON but not a dict
            cursor.execute(
                """INSERT INTO video_game_details
                   (content_item_id, genres, metadata)
                   VALUES (?, '["Action"]', '["not", "a", "dict"]')""",
                (dup_id,),
            )
            conn.commit()

        merged_count = temp_db.deduplicate_items()
        assert merged_count == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("playtime_hours") == 120

    def test_deduplicate_items_global_merges_all_users(self, temp_db: SQLiteDB) -> None:
        """deduplicate_items() with no user_id merges duplicates for all users.

        Verifies the global dedup path (user_id=None) handles multiple users.
        """
        # Create a second user
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (id, username) VALUES (2, 'user_b')")
            conn.commit()

        # Insert duplicates for user 1
        _insert_raw_item(
            temp_db,
            external_id="u1-a",
            title="Hades",
            normalized_title="hades",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="u1-b",
            title="Hades",
            normalized_title="hades",
            source="blog",
        )

        # Insert duplicates for user 2
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (2, 'u2-a', 'Hades', 'hades', 'video_game',
                           'completed', 'steam')""",
            )
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, external_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (2, 'u2-b', 'Hades', 'hades', 'video_game',
                           'completed', 'blog')""",
            )
            conn.commit()

        # Global dedup — should merge both users' duplicates
        merged_count = temp_db.deduplicate_items()
        assert merged_count == 2

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM content_items WHERE user_id = 1")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT COUNT(*) FROM content_items WHERE user_id = 2")
            assert cursor.fetchone()[0] == 1

    def test_resave_source_updated_after_cross_source_merge(
        self, temp_db: SQLiteDB
    ) -> None:
        """After cross-source merge, the re-saved item's fields are applied.

        Verifies that not only is the duplicate removed, but the kept row
        is updated with the re-saved item's data (source, title, etc.).
        The kept row starts with source="old_import" to prove the update
        path actually ran (if it didn't, source would remain "old_import").
        """
        steam_id = _insert_raw_item(
            temp_db,
            external_id="steam-ori",
            title="Ori and the Blind Forest",
            normalized_title="ori and the blind forest",
            rating=5,
            source="old_import",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-ori",
            title="Ori and the Blind Forest",
            normalized_title="ori and the blind forest",
            source="blog",
        )

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 2

        # Re-save Steam item — triggers cross-source merge and UPDATE
        steam = ContentItem(
            id="steam-ori",
            title="Ori and the Blind Forest",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            source="steam",
        )
        temp_db.save_content_item(steam)

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1

        retrieved = temp_db.get_content_item(steam_id)
        assert retrieved is not None
        # Source changed from "old_import" to "steam" — proves update ran
        assert retrieved.source == "steam"
        assert retrieved.rating == 5

    def test_schema_migration_dedup_merges_detail_tables_regression(
        self, tmp_path: Path
    ) -> None:
        """Schema migration dedup merges detail table data (genres, tags, metadata).

        Bug: _deduplicate_inline calls _merge_duplicate_row which delegates to
        merge_detail_tables, but no test exercised this migration code path
        with actual detail table rows.  Detail data could be silently lost
        during migration dedup without any test detecting it.
        Root cause: Security review flagged the missing coverage — the runtime
        dedup path (deduplicate_items) was tested but the schema migration
        path (_deduplicate_inline → _merge_duplicate_row) was not.
        Fix: Added this integration test that creates duplicate video game items
        with detail rows (genres, tags, metadata), triggers schema migration,
        and verifies the merge preserves all data.
        """
        db_path = tmp_path / "migration_detail_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)

        cursor = conn.cursor()

        # Insert kept row with some detail data
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, rating, source)
               VALUES (1, 'steam-dishonored', 'Dishonored',
                       'dishonored', 'video_game', 'completed', 5, 'steam')"""
        )
        keep_id = cursor.lastrowid
        assert keep_id is not None
        cursor.execute(
            """INSERT INTO video_game_details
               (content_item_id, developer, genres, tags, metadata)
               VALUES (?, 'Arkane Studios', '["Stealth", "Action"]',
                       '["immersive-sim"]', ?)""",
            (keep_id, json.dumps({"playtime_hours": 40})),
        )

        # Insert duplicate row with complementary detail data
        cursor.execute(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, review, source)
               VALUES (1, 'blog-dishonored', 'Dishonored',
                       'dishonored', 'video_game', 'completed',
                       'Masterpiece of level design', 'blog')"""
        )
        dup_id = cursor.lastrowid
        assert dup_id is not None
        cursor.execute(
            """INSERT INTO video_game_details
               (content_item_id, developer, publisher, genres, tags, metadata)
               VALUES (?, 'Arkane Lyon', 'Bethesda', '["Action", "RPG"]',
                       '["steampunk", "immersive-sim"]', ?)""",
            (dup_id, json.dumps({"award": "GOTY", "playtime_hours": 30})),
        )
        conn.commit()

        # Verify two rows exist
        cursor.execute("SELECT COUNT(*) FROM content_items")
        assert cursor.fetchone()[0] == 2

        # Re-run create_schema — triggers migration dedup
        create_schema(conn)

        # Should now have one row
        cursor.execute("SELECT COUNT(*) FROM content_items")
        assert cursor.fetchone()[0] == 1

        # Verify scalar merge: kept row's rating preserved, review from dup
        cursor.execute(
            "SELECT rating, review FROM content_items WHERE id = ?",
            (keep_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["rating"] == 5
        assert row["review"] == "Masterpiece of level design"

        # Verify detail table merge
        cursor.execute(
            "SELECT * FROM video_game_details WHERE content_item_id = ?",
            (keep_id,),
        )
        detail = cursor.fetchone()
        assert detail is not None

        # Developer: kept had value, so it's preserved (fill-only)
        assert detail["developer"] == "Arkane Studios"
        # Publisher: kept was NULL, filled from dup
        assert detail["publisher"] == "Bethesda"

        # Genres: additive merge
        genres = json.loads(detail["genres"])
        assert "Stealth" in genres
        assert "Action" in genres
        assert "RPG" in genres

        # Tags: additive merge
        tags = json.loads(detail["tags"])
        assert "immersive-sim" in tags
        assert "steampunk" in tags

        # Metadata: existing keys take precedence
        meta = json.loads(detail["metadata"])
        assert meta["playtime_hours"] == 40  # kept's value wins
        assert meta["award"] == "GOTY"  # dup's unique key added

        # Verify dup's content_items row is gone
        cursor.execute(
            "SELECT COUNT(*) FROM content_items WHERE id = ?",
            (dup_id,),
        )
        assert cursor.fetchone()[0] == 0

        # Verify dup's detail row is gone
        cursor.execute(
            "SELECT COUNT(*) FROM video_game_details WHERE content_item_id = ?",
            (dup_id,),
        )
        assert cursor.fetchone()[0] == 0


class TestDuplicateMergePreservesState:
    """Regression tests for a duplicate merge dropping status and ignored.

    Bug reported: merging two rows for the same title carried only rating,
    review and date_completed across before deleting the duplicate. A
    COMPLETED duplicate merged into an UNREAD kept row silently reverted the
    completion, and an ignored duplicate merged into a non-ignored kept row
    silently un-ignored the item — both unrecoverable, since the duplicate row
    is then deleted. The item came back as a recommendation candidate.
    Root cause: ``merge_scalar_columns`` selected and updated only those three
    columns; the forward-only status rule protecting the sync path was not
    applied to the merge at all.
    Fix: the merge resolves status with the same forward-only ordering and
    ORs the ignored flags, so the strongest state on either row survives.
    """

    def test_merge_keeps_completed_status_regression(self, temp_db: SQLiteDB) -> None:
        """A completed duplicate does not revert the kept row to unread."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-portal2",
            title="Portal 2",
            normalized_title="portal 2",
            status="unread",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-portal2",
            title="Portal 2",
            normalized_title="portal 2",
            status="completed",
            source="personal_site",
        )

        assert temp_db.deduplicate_items() == 1

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_merge_does_not_revert_completed_kept_row(self, temp_db: SQLiteDB) -> None:
        """An unread duplicate does not drag a completed kept row backward."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-hollow",
            title="Hollow Knight",
            normalized_title="hollow knight",
            status="completed",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-hollow",
            title="Hollow Knight",
            normalized_title="hollow knight",
            status="unread",
            source="personal_site",
        )

        assert temp_db.deduplicate_items() == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_merge_keeps_ignored_flag_regression(self, temp_db: SQLiteDB) -> None:
        """An ignore on either row survives the merge."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-doom",
            title="Doom",
            normalized_title="doom",
            ignored=False,
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="blog-doom",
            title="Doom",
            normalized_title="doom",
            ignored=True,
            source="personal_site",
        )

        assert temp_db.deduplicate_items() == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.ignored is True

    def test_unrecognised_status_ranks_lowest_in_the_merge(
        self, temp_db: SQLiteDB
    ) -> None:
        """A status neither row's rules know about never outranks a real one.

        Statuses reach the merge as raw strings from rows written by earlier
        schema versions and by migration paths, so the ordering has to answer
        for a value it does not recognise. It ranks with ``unread``: a
        completed row is not dragged backward by one, and a recognised status
        on the duplicate replaces one on the kept row. The status column is
        read directly because an unrecognised value has no enum to map to.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="legacy-outer-wilds",
            title="Outer Wilds",
            normalized_title="outer wilds",
            status="abandoned",
            source="personal_site",
        )
        _insert_raw_item(
            temp_db,
            external_id="steam-outer-wilds",
            title="Outer Wilds",
            normalized_title="outer wilds",
            status="completed",
            source="steam",
        )

        assert temp_db.deduplicate_items() == 1

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM content_items WHERE id = ?", (keep_id,))
            assert cursor.fetchone()["status"] == "completed"

    def test_unrecognised_duplicate_status_does_not_revert_a_completion(
        self, temp_db: SQLiteDB
    ) -> None:
        """A completed kept row survives a duplicate whose status is unknown."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-celeste",
            title="Celeste",
            normalized_title="celeste",
            status="completed",
            source="steam",
        )
        _insert_raw_item(
            temp_db,
            external_id="legacy-celeste",
            title="Celeste",
            normalized_title="celeste",
            status="abandoned",
            source="personal_site",
        )

        assert temp_db.deduplicate_items() == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_completed_and_ignored_item_survives_dedupe_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The full reported scenario: completion and ignore both survive.

        The duplicate carries the completion, the kept (older, lower-id) row
        carries the ignore. Before the fix each row kept only its own state
        and whichever lived on the deleted row was gone.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="early-sync",
            title="Portal 2",
            normalized_title="portal 2",
            status="unread",
            ignored=True,
            source="personal_site",
        )
        _insert_raw_item(
            temp_db,
            external_id="steam-620",
            title="Portal 2™",
            normalized_title="portal 2",
            status="completed",
            ignored=False,
            source="steam",
        )

        assert temp_db.deduplicate_items() == 1

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED
        assert retrieved.ignored is True

    def test_merge_with_identical_state_does_not_bump_updated_at(
        self, temp_db: SQLiteDB
    ) -> None:
        """Adding status/ignored to the merge keeps the no-op guard intact.

        The will_change guard exists so a merge that changes nothing does not
        disturb ``updated_at``, a user-facing sort key.

        The kept row's ``updated_at`` is pinned to a fixed past value first,
        rather than read back from the ``CURRENT_TIMESTAMP`` default: that
        default has one-second resolution and this test runs in milliseconds,
        so a before/after comparison of two fresh timestamps would agree
        whether or not the guard skipped the UPDATE.
        """
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-tunic",
            title="Tunic",
            normalized_title="tunic",
            status="completed",
            ignored=True,
            rating=4,
            source="steam",
        )

        pinned_updated_at = "2020-01-01 00:00:00"
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE content_items SET updated_at = ? WHERE id = ?",
                (pinned_updated_at, keep_id),
            )
            conn.commit()

        _insert_raw_item(
            temp_db,
            external_id="blog-tunic",
            title="Tunic",
            normalized_title="tunic",
            status="completed",
            ignored=True,
            rating=4,
            source="personal_site",
        )

        assert temp_db.deduplicate_items() == 1

        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM content_items WHERE id = ?", (keep_id,)
            )
            assert cursor.fetchone()["updated_at"] == pinned_updated_at


class TestIgnoredSignalFetchRegression:
    """Ignored items must be excludable from recommendation-signal fetches.

    Bug (issue #99): ``get_completed_items`` / ``get_unconsumed_items`` had no
    way to exclude ignored items, so ignored content leaked into the
    recommendation signal (preferences, scoring, similarity, explanations).

    Root cause: both wrappers always delegated to ``get_content_items`` with
    the default ``include_ignored=True``.

    Fix: both wrappers accept ``include_ignored`` and thread it through, so the
    engine can fetch a clean signal set while library views keep seeing
    ignored items.
    """

    @pytest.mark.parametrize("ignored_rating", [5, None])
    def test_get_completed_items_excludes_ignored_when_flag_false_regression(
        self, temp_db: SQLiteDB, ignored_rating: int | None
    ) -> None:
        """include_ignored=False drops ignored completed items regardless of rating.

        The ``None`` case covers an item that is both ignored *and* unrated —
        it must still be excluded via the ignored flag.
        """
        kept = ContentItem(
            id="kept",
            title="Kept Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        ignored = ContentItem(
            id="ignored",
            title="Ignored Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=ignored_rating,
        )
        temp_db.save_content_item(kept)
        ignored_db_id = temp_db.save_content_item(ignored)
        temp_db.set_item_ignored(ignored_db_id, True)

        default_titles = {i.title for i in temp_db.get_completed_items()}
        assert default_titles == {"Kept Book", "Ignored Book"}

        filtered = temp_db.get_completed_items(include_ignored=False)
        assert {i.title for i in filtered} == {"Kept Book"}

    def test_get_unconsumed_items_excludes_ignored_when_flag_false_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """include_ignored=False drops ignored unconsumed items."""
        kept = ContentItem(
            id="kept",
            title="Kept Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        ignored = ContentItem(
            id="ignored",
            title="Ignored Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        temp_db.save_content_item(kept)
        ignored_db_id = temp_db.save_content_item(ignored)
        temp_db.set_item_ignored(ignored_db_id, True)

        default_titles = {i.title for i in temp_db.get_unconsumed_items()}
        assert default_titles == {"Kept Game", "Ignored Game"}

        filtered = temp_db.get_unconsumed_items(include_ignored=False)
        assert {i.title for i in filtered} == {"Kept Game"}


class TestMissingColumnRaisesRegression:
    """A column the row does not carry raises instead of reading as absent.

    Bug: metadata could go missing from items, and every item could read back
    as not enriched, with nothing in the logs and no failing test unless one
    happened to assert that exact field.

    Root cause: ``SQLiteDB._get_row_value`` caught KeyError/IndexError and
    returned its default, so a column name absent from the SELECT was
    indistinguishable from a column holding NULL.

    Fix: the read path subscripts ``sqlite3.Row`` directly. Every row it is
    handed comes from ``_CONTENT_ITEM_SELECT``, whose aliases are generated
    from ``DETAIL_FIELDS``, so a name it does not carry is a bug and says so.
    """

    @staticmethod
    def _row_without_joins(temp_db: SQLiteDB, db_id: int) -> sqlite3.Row:
        """The content_items row alone, carrying no joined column."""
        with temp_db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM content_items WHERE id = ?", (db_id,)
            ).fetchone()
        assert row is not None
        return row

    def test_a_missing_detail_column_raises_regression(self, temp_db: SQLiteDB) -> None:
        """Without the detail join, the read raises rather than losing metadata.

        sqlite3.Row raises IndexError for a name it does not carry.
        """
        db_id = temp_db.save_content_item(
            ContentItem(
                id="missing-detail-columns",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                author="Frank Herbert",
                metadata={"genres": ["Science Fiction"]},
            )
        )

        joined = temp_db.get_content_item(db_id)
        assert joined is not None
        assert joined.author == "Frank Herbert"
        assert joined.metadata["genres"] == ["Science Fiction"]

        with pytest.raises(IndexError):
            temp_db._row_to_content_item(self._row_without_joins(temp_db, db_id))

    def test_a_missing_enrichment_column_raises_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Without the enrichment join, the flag raises rather than reading False."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="missing-enrichment-columns",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        with temp_db.connection() as conn:
            write_enrichment_complete(conn.cursor(), db_id, "tmdb", "high")
            conn.commit()

        joined = temp_db.get_content_item(db_id)
        assert joined is not None
        assert joined.enriched is True

        with pytest.raises(IndexError):
            SQLiteDB._row_is_enriched(self._row_without_joins(temp_db, db_id))

    def test_null_joined_columns_still_read_as_absent_data(
        self, temp_db: SQLiteDB
    ) -> None:
        """A row the joins match nothing for reads back, it does not raise.

        The other half of the same distinction: the LEFT JOINs hand every
        detail and enrichment column over as NULL for an item that has neither
        row, and that is real absent data rather than a broken query. Raising
        here would break every item whose detail row a merge or an older
        schema left behind.
        """
        db_id = _insert_raw_item(
            temp_db, "no-joined-rows", "Unjoined Game", "unjoined game"
        )

        item = temp_db.get_content_item(db_id)

        assert item is not None
        assert item.title == "Unjoined Game"
        assert item.author is None
        assert item.metadata == {}
        assert item.enriched is False
        assert item.ignored is False


class TestUnreadableMetadataBlobRegression:
    """A detail row whose metadata blob is not an object still reads back.

    Bug: the read path merged the parsed blob with ``dict.update`` under a
    ``try`` catching ``JSONDecodeError`` and ``TypeError`` alone. A blob
    holding a JSON array parses cleanly and makes ``update`` raise
    ``ValueError``, so every read that touched the row failed — the library
    list included, since one bad row is enough to break the whole query.

    Root cause: the read path assumed a blob that parses is an object.

    Fix: keys are taken from the blob only when it parses to a dict, the guard
    ``_move_stranded_total_seasons`` already applies. The migration
    deliberately leaves such a row alone, so the rows it spares are exactly the
    ones that reach here.
    """

    @staticmethod
    def _show_with_raw_blob(temp_db: SQLiteDB, blob: str) -> int:
        """Save a show, then overwrite its detail blob with *blob*.

        Written afterwards because the write path cannot produce it, and the
        ``seasons`` column is filled so the read is asserted on real data
        rather than on an empty item.
        """
        db_id = temp_db.save_content_item(
            ContentItem(
                id="unreadable-blob",
                title="Breaking Bad",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                metadata={"seasons": 5},
            )
        )
        with temp_db.connection() as conn:
            conn.execute(
                "UPDATE tv_show_details SET metadata = ? WHERE content_item_id = ?",
                (blob, db_id),
            )
            conn.commit()
        return db_id

    def test_a_blob_holding_a_json_array_reads_back_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The array the migration tolerates and the reader used to choke on."""
        db_id = self._show_with_raw_blob(temp_db, json.dumps(["total_seasons", 5]))

        item = temp_db.get_content_item(db_id)

        assert item is not None
        assert item.metadata == {"seasons": 5}

    def test_a_blob_that_is_not_json_reads_back_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The other unreadable blob: text no parser makes anything of."""
        db_id = self._show_with_raw_blob(temp_db, "not json at all")

        item = temp_db.get_content_item(db_id)

        assert item is not None
        assert item.metadata == {"seasons": 5}

    def test_a_blob_holding_a_json_string_reads_back_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The third shape that parses without being an object.

        ``dict.update`` walks a string character by character looking for
        pairs, so a bare JSON string raised ``ValueError`` exactly as the
        array did — the same bug, one the array test alone does not pin.
        """
        db_id = self._show_with_raw_blob(temp_db, json.dumps("total_seasons"))

        item = temp_db.get_content_item(db_id)

        assert item is not None
        assert item.metadata == {"seasons": 5}

    def test_an_unreadable_blob_leaves_the_library_listable_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """One bad row used to take every other row down with it.

        The single-item read is not where this was felt: ``get_content_items``
        converts every matched row, so one unreadable blob anywhere in the
        library raised before the caller saw any of it.
        """
        unreadable_id = self._show_with_raw_blob(
            temp_db, json.dumps(["total_seasons", 5])
        )
        readable_id = temp_db.save_content_item(
            ContentItem(
                id="readable-blob",
                title="The Wire",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
            )
        )

        items = temp_db.get_content_items()

        assert {item.db_id for item in items} == {unreadable_id, readable_id}


class TestUndeclaredContentTypeWriteRegression:
    """Saving a type with no field declaration fails instead of half writing.

    Bug: ``_save_detail_table`` looked the declaration up with ``.get`` and
    returned when it missed. The ``content_items`` row was committed with no
    detail row beside it, so every column the item carried — author, genres,
    year, description — was dropped with nothing raised and nothing logged.

    Root cause: a defensive early return standing in for an invariant
    ``src/models/detail_fields.py`` already enforces at import time.

    Fix: the lookup subscripts the mapping, so a miss raises and the
    transaction is abandoned rather than committed incomplete.
    """

    def test_an_undeclared_type_raises_and_writes_no_row_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The declaration is removed to reach a branch the guards forbid.

        ``_assert_every_content_type_is_declared`` makes this unreachable
        through the public types, which is why the mapping is patched rather
        than a bogus ``ContentType`` invented.
        """
        item = ContentItem(
            id="undeclared-type",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"studio": "Warner Bros."},
        )

        with patch.dict(DETAIL_FIELDS):
            del DETAIL_FIELDS["movie"]
            # Both paths do a lot of dict work, so the message is pinned to
            # the declaration lookup rather than to KeyError alone.
            with pytest.raises(KeyError, match=r"^'movie'$"):
                temp_db.save_content_item(item)

        with temp_db.connection() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) AS count FROM content_items WHERE external_id = ?",
                ("undeclared-type",),
            ).fetchone()["count"]
        assert remaining == 0


class TestUndeclaredContentTypeReadRegression:
    """Reading a type with no field declaration fails instead of blanking it.

    Bug: ``_row_to_content_item`` looked the declaration up with ``.get`` and
    skipped the whole detail block when it missed, so a stored item came back
    with no author, no genres and no metadata — every one of those columns
    already in the row the query had selected — and nothing said so.

    Root cause: the same defensive lookup ``_save_detail_table`` carried, a
    total mapping treated as partial.

    Fix: the lookup subscripts the mapping, so a miss raises rather than
    reporting a full row as an empty one.
    """

    def test_an_undeclared_type_raises_rather_than_dropping_detail_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The declaration is removed to reach a branch the guards forbid.

        The joined SELECT is built once at import, so the row still carries
        every detail column: the read is what fails, not the query. Outside
        the patch the same item reads back whole, which is what the early
        return silently withheld.
        """
        db_id = temp_db.save_content_item(
            ContentItem(
                id="undeclared-read",
                title="The Matrix",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                author="The Wachowskis",
                metadata={"genres": ["Science Fiction"]},
            )
        )

        with patch.dict(DETAIL_FIELDS):
            del DETAIL_FIELDS["movie"]
            # Pinned to the declaration lookup: a KeyError from anywhere else
            # in the read would otherwise keep this green.
            with pytest.raises(KeyError, match=r"^'movie'$"):
                temp_db.get_content_item(db_id)

        item = temp_db.get_content_item(db_id)
        assert item is not None
        assert item.author == "The Wachowskis"
        assert item.metadata["genres"] == ["Science Fiction"]
