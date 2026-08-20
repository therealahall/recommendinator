"""Tests for SQLite database manager."""

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion.sources.radarr.radarr import RadarrPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.detail_fields import to_json_array
from src.storage import sqlite_db
from src.storage.item_merges import MergeEvidence
from src.storage.merge import (
    creators_conflict,
    normalize_creator_for_matching,
    normalize_title_for_matching,
)
from src.storage.schema import write_enrichment_complete
from src.storage.sqlite_db import SaveOutcome, SQLiteDB
from src.utils.item_serialization import item_to_dict
from src.utils.sorting import build_search_text, get_sort_title

# The instant the completion-stamping tests freeze the clock at, so each names
# the date it expects instead of re-deriving it from the helper under test, and
# so none of them straddles midnight between the write and the read back.
FROZEN_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
FROZEN_TODAY = date(2026, 3, 15)


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
               (user_id, title, normalized_title, content_type,
                status, rating, review, date_completed, source, ignored)
               VALUES (1, ?, ?, 'video_game', ?, ?, ?, ?, ?, ?)""",
            (
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
        db_id = cursor.lastrowid
        assert db_id is not None
        _record_raw_external_id(cursor, db_id, source, external_id)
        conn.commit()
        return db_id


def _record_raw_external_id(
    cursor: sqlite3.Cursor, db_id: int, source: str, external_id: str
) -> None:
    cursor.execute(
        "INSERT INTO content_item_external_ids"
        " (content_item_id, user_id, source, external_id, content_type)"
        " SELECT id, user_id, ?, ?, content_type FROM content_items WHERE id = ?",
        (source, external_id, db_id),
    )


def _external_ids(temp_db: SQLiteDB, db_id: int) -> list[tuple[str, str]]:
    """The (source, external id) pairs one row holds, by source."""
    with temp_db.connection() as conn:
        rows = conn.execute(
            "SELECT source, external_id FROM content_item_external_ids"
            " WHERE content_item_id = ? ORDER BY source",
            (db_id,),
        ).fetchall()
    return [(row["source"], row["external_id"]) for row in rows]


def _merged(temp_db: SQLiteDB, survivor_id: int, absorbed_id: int) -> None:
    """Merge one row into another through the door, as the operator does."""
    temp_db.merge_content_items(survivor_id, absorbed_id, MergeEvidence.MANUAL)


def _insert_raw_book(
    temp_db: SQLiteDB, source: str, external_id: str, author: str | None
) -> None:
    """Insert one row of a duplicate pair a save would have merged on the way in.

    Built behind ``save_content_item``'s back, and carrying neither derived
    column, which is what a row written before those columns existed looks like.
    """
    with temp_db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO content_items
               (user_id, title, normalized_title, content_type, status, source)
               VALUES (1, 'The Hobbit', 'hobbit', 'book', 'completed', ?)""",
            (source,),
        )
        db_id = cursor.lastrowid
        assert db_id is not None
        _record_raw_external_id(cursor, db_id, source, external_id)
        cursor.execute(
            "INSERT INTO book_details (content_item_id, author) VALUES (?, ?)",
            (db_id, author),
        )
        conn.commit()


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
    assert retrieved.source == "steam"  # Names what first stored the row

    # One item, holding the id each source knows it by
    all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
    assert len(all_games) == 1
    assert _external_ids(temp_db, db_id_1) == [
        ("personal_site_games", "crysis"),
        ("steam", "steam_12345"),
    ]


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
            source="calibre_web",
        ),
        ContentItem(
            id="completed_unrated",
            title="Completed Unrated",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=None,
            source="calibre_web",
        ),
        ContentItem(
            id="unread_unrated",
            title="Unread Unrated",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            rating=None,
            source="radarr",
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
        source="calibre_web",
    )
    hidden = ContentItem(
        id="ignored_unrated",
        title="Ignored Unrated",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=None,
        ignored=True,
        source="calibre_web",
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


# ---------------------------------------------------------------------------
# Batch ID Lookup Tests
# ---------------------------------------------------------------------------


class TestGetContentItemsByDbIds:
    """Tests for SQLiteDB.get_content_items_by_db_ids batch lookup."""

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

    def test_the_items_come_back_in_the_order_asked_for(
        self, temp_db: SQLiteDB
    ) -> None:
        """The caller's order survives, including across the chunk boundary.

        A library search orders its matches in SQL, keeps the ids and loads
        them through here, so this ordering is what a searched page is sorted
        by. The chunk boundary is where it would be lost: each chunk is its
        own query and returns in whatever order that query chose, so more than
        500 matches is the case that has to be built rather than assumed.
        """
        db_ids: list[int] = []
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            for index in range(502):
                cursor.execute(
                    """INSERT INTO content_items
                       (user_id, title, normalized_title,
                        content_type, status, source)
                       VALUES (1, ?, ?, 'video_game', 'completed', 'test')""",
                    (f"Game {index}", f"game {index}"),
                )
                assert cursor.lastrowid is not None
                db_ids.append(cursor.lastrowid)
            conn.commit()
        asked = list(reversed(db_ids))

        results = temp_db.get_content_items_by_db_ids(asked)

        assert [item.db_id for item in results] == asked

    def test_a_repeated_id_is_returned_once_per_occurrence(
        self, temp_db: SQLiteDB
    ) -> None:
        """The result tracks the argument, not the set of distinct ids in it."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="game-1",
                title="Portal",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        results = temp_db.get_content_items_by_db_ids([db_id, db_id])

        assert [item.db_id for item in results] == [db_id, db_id]


def test_one_source_may_know_a_movie_and_a_show_by_the_same_id(
    temp_db: SQLiteDB,
) -> None:
    """Trakt numbers each type from one, so movie 1 and show 1 both exist."""
    db_ids = [
        temp_db.save_content_item(
            ContentItem(
                id="trakt:1",
                title=title,
                content_type=content_type,
                status=ConsumptionStatus.COMPLETED,
                source="trakt",
            )
        )
        for content_type, title in (
            (ContentType.MOVIE, "Heat"),
            (ContentType.TV_SHOW, "Andor"),
        )
    ]

    assert {
        (item.title, item.content_type)
        for item in temp_db.get_content_items_by_db_ids(db_ids)
    } == {("Heat", ContentType.MOVIE), ("Andor", ContentType.TV_SHOW)}


# ---------------------------------------------------------------------------
# Title Normalization Tests
# ---------------------------------------------------------------------------


class TestNormalizeTitleForMatching:
    """Tests for the normalize_title_for_matching function."""

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

    def test_roman_numerals_only_at_word_boundaries(self) -> None:
        """Test that Roman numerals are only converted at word boundaries.

        This prevents false conversions like "Civil" -> "C1v1l".
        """
        assert normalize_title_for_matching("Civil War") == "civil war"

    def test_a_standalone_i_is_a_numeral_at_the_end_and_a_pronoun_before(
        self,
    ) -> None:
        assert normalize_title_for_matching("I Am Legend") == "i am legend"
        assert normalize_title_for_matching("How I Met Your Mother") == (
            "how i met your mother"
        )
        assert normalize_title_for_matching("Part I") == "part 1"


class TestWhichTrailingParentheticalsAreDropped:
    """Goodreads RSS appends "(Series, #N)" where Calibre appends nothing."""

    _ONE_BOOK_TWO_SPELLINGS = [
        (
            "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
            "The Gate of the Feral Gods",
        ),
        ("Burden to Bear (Spear of the Gods #1)", "Burden to Bear"),
        ("Endgame (Doom #4)", "Endgame"),
        ("Dawnshard (The Stormlight Archive, #3.5)", "Dawnshard"),
        ("A Clash of Kings  (A Song of Ice and Fire, #2)", "A Clash of Kings"),
    ]

    @pytest.mark.parametrize(("shelved", "bare"), _ONE_BOOK_TWO_SPELLINGS)
    def test_a_series_position_matches_the_calibre_row_beside_it(
        self, shelved: str, bare: str
    ) -> None:
        assert normalize_title_for_matching(shelved) == normalize_title_for_matching(
            bare
        )

    def test_a_regional_qualifier_matches_the_same_show_without_one(self) -> None:
        assert normalize_title_for_matching("Hell's Kitchen (US)") == (
            normalize_title_for_matching("Hell's Kitchen")
        )

    def test_an_edition_of_one_work_leaves_the_key(self) -> None:
        """A translation and an audiobook are the book, as two printings are."""
        assert normalize_title_for_matching("Brave New World (Indonesian Edition)") == (
            normalize_title_for_matching("Brave New World")
        )
        assert normalize_title_for_matching("Frankenstein (Unabridged)") == (
            normalize_title_for_matching("Frankenstein")
        )

    @pytest.mark.parametrize(("one", "two"), [("3rd", "4th"), ("Second", "Third")])
    def test_a_counted_edition_stays_in_the_key(self, one: str, two: str) -> None:
        """Books get no year veto, so stripping this hides one textbook."""
        assert normalize_title_for_matching(
            f"Introduction to Algorithms ({one} Edition)"
        ) != normalize_title_for_matching(f"Introduction to Algorithms ({two} Edition)")

    def test_a_year_leaves_the_key_for_the_veto_to_answer(self) -> None:
        """In the key it kept "Die Hard (1988)" off a "Die Hard" stating none."""
        assert normalize_title_for_matching("DOOM (2016)") == (
            normalize_title_for_matching("Doom")
        )


class TestTheCreatorVeto:
    """Creator is no part of the match key; it only rejects a title match."""

    def test_initials_and_the_goodreads_inversion_are_one_author(self) -> None:
        spellings = ["JK Rowling", "J.K. Rowling", "J. K. Rowling", "Rowling, J.K."]
        assert len({normalize_creator_for_matching(name) for name in spellings}) == 1

    def test_dune_by_frank_herbert_and_dune_by_alexander_freed_conflict(self) -> None:
        assert creators_conflict("Frank Herbert", "Alexander Freed")

    def test_an_unstated_or_partial_creator_does_not_conflict(self) -> None:
        assert not creators_conflict(None, "Frank Herbert")
        assert not creators_conflict("", "Frank Herbert")
        assert not creators_conflict("Frank Herbert", "Frank Herbert, Brian Herbert")
        assert not creators_conflict("Arkane Studios", "Arkane Lyon")

    def test_a_creator_of_nothing_but_furniture_agrees_with_its_own_spelling(
        self,
    ) -> None:
        """Sharing only furniture leaves the spelling the whole answer, so reading
        the shared tokens alone splits a publisher off itself."""
        assert not creators_conflict(
            "Interactive Entertainment Ltd", "Interactive Entertainment Ltd"
        )


class TestWhatTheSaveDoorMatchesOnTitle:
    """Title, creator veto and year veto, over the door every sync comes through."""

    @staticmethod
    def _book(
        source: str,
        external_id: str,
        title: str,
        author: str | None = None,
        year_published: int | None = None,
    ) -> ContentItem:
        return ContentItem(
            id=external_id,
            title=title,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source=source,
            author=author,
            metadata={"year_published": year_published} if year_published else {},
        )

    @staticmethod
    def _dated(
        content_type: ContentType,
        source: str,
        external_id: str,
        title: str,
        release_year: int | None = None,
    ) -> ContentItem:
        return ContentItem(
            id=external_id,
            title=title,
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            source=source,
            metadata={"release_year": release_year} if release_year else {},
        )

    def test_a_goodreads_series_row_and_an_authorless_calibre_row_are_one_book(
        self, temp_db: SQLiteDB
    ) -> None:
        goodreads = temp_db.save_content_item(
            self._book(
                "goodreads_rss",
                "57905101",
                "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
                "Matt Dinniman",
            )
        )

        calibre = temp_db.save_content_item(
            self._book("calibre_web", "calibre:51a0e808", "The Gate of the Feral Gods")
        )

        assert calibre == goodreads

    def test_two_books_of_one_title_by_unrelated_authors_stay_apart(
        self, temp_db: SQLiteDB
    ) -> None:
        herbert = temp_db.save_content_item(
            self._book("goodreads_rss", "234225", "Dune", "Frank Herbert")
        )

        freed = temp_db.save_content_item(
            self._book("calibre_web", "calibre:dune-novel", "Dune", "Alexander Freed")
        )

        assert freed != herbert

    def test_a_third_source_reaches_past_the_older_row_its_author_rules_out(
        self, temp_db: SQLiteDB
    ) -> None:
        temp_db.save_content_item(
            self._book("goodreads_rss", "234225", "Dune", "Frank Herbert")
        )
        freed = temp_db.save_content_item(
            self._book("calibre_web", "calibre:dune-novel", "Dune", "Alexander Freed")
        )

        imported = temp_db.save_content_item(
            self._book("generic_csv", "csv-dune", "Dune", "Freed, Alexander")
        )

        assert imported == freed

    @staticmethod
    def _show(source: str, external_id: str, title: str) -> ContentItem:
        return ContentItem(
            id=external_id,
            title=title,
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            source=source,
        )

    @staticmethod
    def _game(
        source: str, external_id: str, title: str, **metadata: Any
    ) -> ContentItem:
        """A game as its plugins send one: ``author`` None, developer in metadata."""
        return ContentItem(
            id=external_id,
            title=title,
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=source,
            metadata=metadata,
        )

    def test_the_developer_a_game_states_in_metadata_vetoes_the_match(
        self, temp_db: SQLiteDB
    ) -> None:
        """DEFECT: the incoming creator was read off ``author`` alone, which no
        game plugin sets, so GOG's Tomb Raider landed on Steam's and took its id."""
        steam = temp_db.save_content_item(self._game("steam", "203160", "Tomb Raider"))
        temp_db.save_enrichment_metadata(
            steam,
            self._game("steam", "203160", "Tomb Raider", developer="Crystal Dynamics"),
        )

        gog = temp_db.save_content_item(
            self._game("gog", "1207658919", "Tomb Raider", developers=["Core Design"])
        )

        assert gog != steam

    def test_two_preys_by_studios_sharing_only_a_suffix_stay_apart(
        self, temp_db: SQLiteDB
    ) -> None:
        """DEFECT: a shared "Studios" landed GOG's 2017 Prey on Steam's 2006 one."""
        steam = temp_db.save_content_item(
            self._game("steam", "3970", "Prey", developer="Human Head Studios")
        )

        gog = temp_db.save_content_item(
            self._game("gog", "1424216861", "Prey", developers=["Arkane Studios"])
        )

        assert gog != steam

    def test_a_trakt_show_does_not_land_on_the_sonarr_row_for_another_region(
        self, temp_db: SQLiteDB
    ) -> None:
        uk = temp_db.save_content_item(
            self._show("sonarr", "tvdb:78107", "The Office (UK)")
        )
        us = temp_db.save_content_item(
            self._show("sonarr", "tvdb:73244", "The Office (US)")
        )

        trakt_us = temp_db.save_content_item(
            self._show("trakt", "trakt:4063", "The Office")
        )

        assert us != uk
        assert trakt_us != uk

    def test_two_films_of_one_name_are_told_apart_by_the_years_they_state(
        self, temp_db: SQLiteDB
    ) -> None:
        """Neither title spells a year, so only the stored column can refuse. No
        sync source states one, so both sides are the importer that does."""
        watched = temp_db.save_content_item(
            self._dated(ContentType.MOVIE, "csv_import", "csv-841", "Dune", 1984)
        )

        downloaded = temp_db.save_content_item(
            self._dated(ContentType.MOVIE, "json_import", "json-438631", "Dune", 2021)
        )

        assert downloaded != watched

    def test_a_year_only_one_source_states_does_not_stop_the_match(
        self, temp_db: SQLiteDB
    ) -> None:
        """An import dates a film in a field, and Radarr, which dates none, agrees."""
        dated = temp_db.save_content_item(
            self._dated(ContentType.MOVIE, "csv_import", "csv-562", "Die Hard", 1988)
        )

        undated = temp_db.save_content_item(
            self._dated(ContentType.MOVIE, "radarr", "tmdb:481", "Die Hard")
        )

        assert undated == dated

    def test_a_remake_no_source_dates_stays_off_the_original(
        self, temp_db: SQLiteDB
    ) -> None:
        """No game plugin fills release_year, so the title's year is all there is."""
        original = temp_db.save_content_item(
            self._dated(ContentType.VIDEO_GAME, "steam", "2280", "Doom")
        )

        remake = temp_db.save_content_item(
            self._dated(ContentType.VIDEO_GAME, "epic_games", "379720", "DOOM (2016)")
        )

        assert remake != original

    def test_two_editions_of_one_book_are_not_told_apart_by_their_years(
        self, temp_db: SQLiteDB
    ) -> None:
        """``year_published`` is the edition's year, so a reprint is the book."""
        first = temp_db.save_content_item(
            self._book("goodreads_rss", "234225", "Dune", "Frank Herbert", 1965)
        )

        reprint = temp_db.save_content_item(
            self._book("calibre_web", "calibre:dune", "Dune", "Frank Herbert", 2011)
        )

        assert reprint == first

    def test_a_year_in_a_books_title_is_not_a_year_the_book_states(
        self, temp_db: SQLiteDB
    ) -> None:
        """A book states no release year, so a title year cannot split a reprint."""
        first = temp_db.save_content_item(
            self._book("goodreads_rss", "234225", "Dune (1965)", "Frank Herbert")
        )

        reprint = temp_db.save_content_item(
            self._book("calibre_web", "calibre:dune", "Dune (2011)", "Frank Herbert")
        )

        assert reprint == first

    def test_a_survivor_is_reachable_by_the_title_it_is_spelled_with(
        self, temp_db: SQLiteDB
    ) -> None:
        """A merge must not hide the survivor's own spelling from a later sync."""
        temp_db.save_content_item(self._show("sonarr", "tvdb:78107", "The Office (UK)"))
        us = temp_db.save_content_item(
            self._show("sonarr", "tvdb:73244", "The Office (US)")
        )
        plain = temp_db.save_content_item(
            self._show("sonarr", "tvdb:99999", "The Office")
        )
        temp_db.merge_content_items(plain, us, MergeEvidence.MANUAL)

        landed = temp_db.save_content_item(
            self._show("trakt", "trakt:4063", "The Office")
        )

        assert landed == plain

    def test_calibres_placeholder_author_does_not_veto_the_goodreads_row(
        self, temp_db: SQLiteDB
    ) -> None:
        goodreads = temp_db.save_content_item(
            self._book(
                "goodreads_rss",
                "57905101",
                "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
                "Matt Dinniman",
            )
        )

        calibre = temp_db.save_content_item(
            self._book(
                "calibre_web",
                "calibre:51a0e808",
                "The Gate of the Feral Gods",
                "Unknown",
            )
        )

        assert calibre == goodreads


# ---------------------------------------------------------------------------
# Normalized Title Indexed Lookup Tests
# ---------------------------------------------------------------------------


class TestNormalizedTitleLookup:
    """Tests for the indexed normalized_title column used during save."""

    def test_update_syncs_normalized_title(self, temp_db: SQLiteDB) -> None:
        """Test that UPDATE keeps normalized_title in sync with title."""
        item = ContentItem(
            id="nt_2",
            title="Old Title",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source="calibre_web",
        )
        db_id = temp_db.save_content_item(item)

        # Re-save with a new title
        updated = ContentItem(
            id="nt_2",
            title="New Title: Remastered",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source="calibre_web",
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

    def test_search_empty_string_is_noop(self, temp_db: SQLiteDB) -> None:
        """An empty or whitespace search term does not filter."""
        self._seed_movies(temp_db)
        assert len(temp_db.get_content_items(search="")) == 3
        assert len(temp_db.get_content_items(search="   ")) == 3

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

    def test_search_ands_with_type_filter(self, temp_db: SQLiteDB) -> None:
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

        ``build_search_text`` stores an empty creator half for such a book, and
        ``_matches_normalized`` bails on an empty haystack, so a creator-style
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

    def test_search_ands_with_the_enrichment_filter(self, temp_db: SQLiteDB) -> None:
        """Search combines with the enrichment filter (AND).

        The filter is a predicate over the enrichment join, and the search
        reads its candidates through a projection of its own; that projection
        carries the join precisely so the predicate stays valid against it.
        """
        db_ids = {
            external_id: temp_db.save_content_item(
                ContentItem(
                    id=external_id,
                    title=title,
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                    source="radarr",
                )
            )
            for external_id, title in (
                ("quest_known", "Quest Alpha"),
                ("quest_unknown", "Quest Bravo"),
            )
        }
        with temp_db.connection() as conn:
            write_enrichment_complete(
                conn.cursor(), db_ids["quest_known"], "tmdb", "high"
            )
            conn.commit()

        enriched = temp_db.get_content_items(search="Quest", enrichment="enriched")
        not_enriched = temp_db.get_content_items(
            search="Quest", enrichment="not_enriched"
        )

        assert [item.id for item in enriched] == ["quest_known"]
        assert [item.id for item in not_enriched] == ["quest_unknown"]

    def test_a_term_cannot_match_across_the_title_and_the_creator(
        self, temp_db: SQLiteDB
    ) -> None:
        """The stored title and creator are matched separately, never as one string.

        They share a column, joined by a character search normalization can
        never produce. Asserted through the read rather than over the stored
        text alone, because it is the read that decides which halves a term is
        offered and would otherwise be free to run the match over the join.
        """
        temp_db.save_content_item(
            ContentItem(
                id="alpha_omega",
                title="Alpha",
                author="Omega",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        assert temp_db.get_content_items(search="alpha omega") == []
        assert len(temp_db.get_content_items(search="alpha")) == 1
        assert len(temp_db.get_content_items(search="omega")) == 1

    def test_typo_only_matches_are_ordered_and_paged_like_any_other(
        self, temp_db: SQLiteDB
    ) -> None:
        """Matches reached only by the fuzzy tier still honour sort and page.

        No title here contains the term, so every match comes from the window
        scan — the tier a caller's ``sort_by`` and ``limit``/``offset`` would
        be easiest to lose, since the matching runs in Python rather than in
        the ORDER BY.
        """
        for external_id, title, rating in (
            ("alienz", "Alienz", 5),
            ("alians", "Alians", 3),
            ("aliems", "Aliems", 1),
        ):
            temp_db.save_content_item(
                ContentItem(
                    id=external_id,
                    title=title,
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                    rating=rating,
                    source="radarr",
                )
            )

        ordered = temp_db.get_content_items(search="Aliens", sort_by="rating")
        page1 = temp_db.get_content_items(
            search="Aliens", sort_by="rating", limit=2, offset=0
        )
        page2 = temp_db.get_content_items(
            search="Aliens", sort_by="rating", limit=2, offset=2
        )

        assert [item.id for item in ordered] == ["alienz", "alians", "aliems"]
        assert [item.id for item in page1] == ["alienz", "alians"]
        assert [item.id for item in page2] == ["aliems"]


def test_get_content_items_refuses_an_unknown_sort(temp_db: SQLiteDB) -> None:
    """A sort nobody declared is refused rather than silently ignored.

    The surfaces validate their own input, so this is the backstop for a
    caller that reaches storage directly — a plugin, or a surface that gains a
    sort option without a matching ORDER BY.
    """
    with pytest.raises(ValueError, match="Invalid sort_by"):
        temp_db.get_content_items(sort_by="bogus")


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
    still means "no limit".

    Every sort now slices in SQL, so the parameter the crash depended on no
    longer selects between two implementations — which is why these cases,
    written to cover both, are what proves the one path handles them all.
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

    # What the term "a" matches, in updated_at order: every seeded title but
    # Echo, so a search here has a matched set larger than any offset below.
    _SEARCH_MATCHES = ["pager_2", "pager_3", "pager_4", "pager_5"]

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
                    source="calibre_web",
                )
            )
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            for external_id, (_, updated_at, created_at, _) in cls._ROWS.items():
                cursor.execute(
                    "UPDATE content_items SET updated_at = ?, created_at = ?"
                    " WHERE id = (SELECT content_item_id"
                    " FROM content_item_external_ids WHERE external_id = ?)",
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

    def test_search_with_offset_and_no_limit_returns_the_tail_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A search term pages the same way, on its own branch of the read.

        A search never reaches the page clause: it slices its matched ids in
        Python and emits no LIMIT or OFFSET at all. So the branch a caller
        lands on the moment they pass a search term has its own copy of the
        offset-without-limit rule, and proves it here rather than inheriting
        it from the sorts above.
        """
        self._seed(temp_db)

        matched = temp_db.get_content_items(
            sort_by="updated_at", search="a", limit=None
        )
        assert [item.id for item in matched] == self._SEARCH_MATCHES

        tail = temp_db.get_content_items(
            sort_by="updated_at", search="a", limit=None, offset=2
        )
        assert [item.id for item in tail] == self._SEARCH_MATCHES[2:]


class TestAPageCostsOnlyThePage:
    """A request builds a ContentItem per row it returns, and none per row it skips.

    Bug reported: the default title sort and every search skipped SQL's
    LIMIT/OFFSET entirely, built a ContentItem for every row the filters left
    — JSON-parsing each detail-table blob on the way — and sliced the list in
    Python afterwards. A 500-item library therefore paid 500 constructions to
    show a 10-item page, on the first load and on every scroll after it.

    Root cause: the title sort key came from ``get_sort_title`` and the search
    haystack from the loaded item's title and creator, so both needed every
    candidate in memory before either could order or filter.

    Fix: both are stored columns. Every sort orders and pages in SQL, and a
    search reads its candidates as an id and a stored search text apiece — no
    detail blob to parse — so neither a candidate that misses nor a match
    outside the page costs a ContentItem. The scan that finds those rows is
    bounded the same way, stopping at the end of the page it was asked for.
    """

    _LIBRARY_SIZE = 500

    @classmethod
    def _seed(cls, temp_db: SQLiteDB) -> None:
        """Seed a library far larger than any page asked of it below."""
        for index in range(cls._LIBRARY_SIZE):
            temp_db.save_content_item(
                ContentItem(
                    id=f"bulk_{index:03d}",
                    title=f"Title {index:03d}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                )
            )

    @staticmethod
    def _items_built(temp_db: SQLiteDB, **query: object) -> tuple[list[str], int]:
        """Run a query, returning its titles and how many items it built.

        Counting the constructions is the measurement: the returned page says
        what the caller sees, and says nothing about what was loaded to
        produce it — which is the whole of the defect.
        """
        with patch.object(
            SQLiteDB,
            "_row_to_content_item",
            autospec=True,
            side_effect=SQLiteDB._row_to_content_item,
        ) as build:
            results = temp_db.get_content_items(**query)  # type: ignore[arg-type]
        return [item.title for item in results], build.call_count

    def test_the_first_page_of_the_title_sort_builds_only_that_page(
        self, temp_db: SQLiteDB
    ) -> None:
        """The default sort of the default view, which pays this on every load."""
        self._seed(temp_db)

        titles, built = self._items_built(temp_db, limit=10)

        assert titles == [f"Title {index:03d}" for index in range(10)]
        assert built == 10

    def test_a_term_of_pure_punctuation_matches_nothing(
        self, temp_db: SQLiteDB
    ) -> None:
        """A term that normalizes away is no term, not a term matching everything.

        An empty needle is a substring of every stored search text, so it has
        to be caught before the match rather than after it — the failure would
        be a search box that returns the whole library the moment someone
        types a stray bracket.
        """
        self._seed(temp_db)

        titles, built = self._items_built(temp_db, search="(((")

        assert titles == []
        assert built == 0

    def test_a_search_builds_only_the_page_it_returns(self, temp_db: SQLiteDB) -> None:
        """Every title contains the term, and ten of the five hundred are built."""
        self._seed(temp_db)

        titles, built = self._items_built(temp_db, search="Title", limit=10)

        assert titles == [f"Title {index:03d}" for index in range(10)]
        assert built == 10


class TestTheDerivedSearchColumns:
    """The stored sort key and search haystack follow their sources.

    Both are derived from the title and the creator, and both are read instead
    of those sources, so a column left behind by an edit is a library that
    sorts and searches on values the user replaced.
    """

    def test_a_retitled_item_sorts_under_its_new_title(self, temp_db: SQLiteDB) -> None:
        """Re-syncing a title moves the item, rather than leaving it in place.

        "The Matrix" sorts between the two neighbours and "Zeppelin" after
        both, so a stale sort key puts the item in the wrong one of two
        positions rather than merely spelling it oddly.
        """
        for external_id, title in (
            ("neighbour_low", "Aardvark"),
            ("neighbour_high", "Zebra"),
            ("renamed", "The Matrix"),
        ):
            temp_db.save_content_item(
                ContentItem(
                    id=external_id,
                    title=title,
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.COMPLETED,
                    source="radarr",
                )
            )

        temp_db.save_content_item(
            ContentItem(
                id="renamed",
                title="Zeppelin",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                source="radarr",
            )
        )

        results = temp_db.get_content_items()
        assert [item.title for item in results] == ["Aardvark", "Zebra", "Zeppelin"]

    def test_a_creator_a_later_sync_fills_becomes_searchable(
        self, temp_db: SQLiteDB
    ) -> None:
        temp_db.save_content_item(
            ContentItem(
                id="hobbit",
                title="The Hobbit",
                author=None,
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        assert temp_db.get_content_items(search="Tolkien") == []

        temp_db.save_content_item(
            ContentItem(
                id="hobbit",
                title="The Hobbit",
                author="J.R.R. Tolkien",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        found = temp_db.get_content_items(search="Tolkien")
        assert [item.title for item in found] == ["The Hobbit"]

    def test_a_stranded_detail_row_does_not_lend_its_creator(
        self, temp_db: SQLiteDB
    ) -> None:
        """The creator is chosen by content type, not by whichever table has a row.

        A COALESCE over the four detail tables would read the director off a
        movie_details row left behind on a book, and the book would then sort
        and search under a name it has nothing to do with. The re-save is what
        recomputes the columns once the stray row exists.
        """
        book = ContentItem(
            id="stranded",
            title="Neuromancer",
            author=None,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        db_id = temp_db.save_content_item(book)
        with temp_db.connection() as conn:
            conn.execute(
                "INSERT INTO movie_details (content_item_id, director) VALUES (?, ?)",
                (db_id, "Ridley Scott"),
            )
            conn.commit()

        temp_db.save_content_item(book)

        with temp_db.connection() as conn:
            stored = conn.execute(
                "SELECT search_text FROM content_items WHERE id = ?", (db_id,)
            ).fetchone()["search_text"]
        assert stored == build_search_text("Neuromancer", None)
        assert temp_db.get_content_items(search="Ridley Scott") == []


# Pages a walk over a paginated read may take before the walk is the bug. Every
# library any test here seeds is a dozen rows at most, so a walk still going
# after this many pages is not paging, whatever it is returning.
_MAX_PAGES_WALKED = 20


def _walk_pages(temp_db: SQLiteDB, page_size: int, **query: object) -> list[str]:
    """Collect the ids a caller sees walking every page of one query.

    Pages the way the library view does: ask for the next page at the offset
    the rows already seen put it at, and stop on the first short page. That
    loop is what makes a page boundary a user-visible thing rather than an
    argument, so a test about boundaries has to take it rather than assert
    about one offset it picked.
    """
    seen: list[str] = []
    offset = 0
    for _ in range(_MAX_PAGES_WALKED):
        page = temp_db.get_content_items(
            limit=page_size, offset=offset, **query  # type: ignore[arg-type]
        )
        seen.extend(item.id or "" for item in page)
        if len(page) < page_size:
            return seen
        offset += len(page)
    raise AssertionError("the walk never reached a short page")


class TestSearchPagesPartitionTheMatchedSet:
    """Walking the pages of one search shows every match once and no match twice.

    The property every other read in this file already holds:
    ``TestPaginationWithoutLimit`` states it as each (limit, offset) selecting
    the same window of that query's own full ordering. A search is a query
    like any other, so the pages of one have to concatenate back into the
    unpaged answer — that is the whole meaning of an offset.
    """

    # Two books whose author's name is spelled correctly and one imported with
    # the surname's middle letters transposed, which is the ordinary way a
    # library ends up holding both spellings. Searching the correct spelling
    # reaches the first two by substring and the third only by the fuzzy tier.
    _LIBRARY = (
        ("caves", "The Caves of Steel", "Isaac Asmiov"),
        ("foundation", "Foundation", "Isaac Asimov"),
        ("i_robot", "I, Robot", "Isaac Asimov"),
    )

    @staticmethod
    def _seed(temp_db: SQLiteDB, rows: tuple[tuple[str, str, str], ...]) -> None:
        """Save the named books through the ordinary sync door."""
        for external_id, title, author in rows:
            temp_db.save_content_item(
                ContentItem(
                    id=external_id,
                    title=title,
                    author=author,
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    source="calibre_web",
                )
            )

    def test_the_two_spellings_are_one_matched_set(self, temp_db: SQLiteDB) -> None:
        """The premise: one spelling holds the term and the other only matches it.

        Stated first because every assertion below is about a page boundary
        between matches of different tiers, and a library whose rows all
        matched the same way would prove nothing about that boundary. It also
        states what the merged design gives back: the typo'd copy is returned
        beside the two that spell the name, rather than only when they are
        missing.
        """
        self._seed(temp_db, self._LIBRARY)
        assert "asimov" not in build_search_text("The Caves of Steel", "Isaac Asmiov")

        assert [item.id for item in temp_db.get_content_items(search="Asimov")] == [
            "caves",
            "foundation",
            "i_robot",
        ]

    @pytest.mark.parametrize("page_size", [1, 2])
    def test_a_search_pages_one_set_when_the_tiers_disagree(
        self, temp_db: SQLiteDB, page_size: int
    ) -> None:
        """A page boundary between tiers still partitions the matched set.

        The case the test above controls for: two of these books hold the term
        and the third only matches it, so a design serving typo matches out of
        a set of their own would apply the offset to a superset of the one the
        earlier pages came from — repeating a row already shown and never
        reaching The Caves of Steel. One matched set is what makes an offset
        past the substring matches mean anything.
        """
        self._seed(temp_db, self._LIBRARY)
        whole = temp_db.get_content_items(search="Asimov")

        assert _walk_pages(temp_db, page_size, search="Asimov") == [
            item.id for item in whole
        ]


class TestPagesPartitionALibraryOfTies:
    """Every sort keeps one order across its pages when its column ties.

    A bulk import is the case: it arrives in one second, so ``created_at`` and
    ``updated_at`` tie across the whole library, and a library holding the
    same title in several content types ties the title sort and the ``ci.title``
    tiebreak of the rating sort as well. SQL's ORDER BY is free to return tied
    rows in any order it likes, and free to choose differently per statement,
    so a page boundary falling inside a tie is where a row gets shown twice
    while another is never shown at all.
    """

    _TITLES = ("Dune", "Solaris", "Contact")

    # Every row shares this rating, so the rating sort falls through to its
    # own tiebreaks rather than ordering on a column that separates the rows.
    _SHARED_RATING = 4

    @classmethod
    def _seed(cls, temp_db: SQLiteDB) -> None:
        """Import the same three titles in all four content types.

        The timestamps are then flattened by hand: CURRENT_TIMESTAMP has
        second granularity and *usually* ties across twelve quick saves, and a
        test about ties cannot be left to usually.
        """
        for title in cls._TITLES:
            for content_type in ContentType:
                temp_db.save_content_item(
                    ContentItem(
                        id=f"{title.lower()}_{content_type.value}",
                        title=title,
                        content_type=content_type,
                        status=ConsumptionStatus.COMPLETED,
                        rating=cls._SHARED_RATING,
                        source="bulk_import",
                    )
                )
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE content_items SET updated_at = '2026-01-01 00:00:00',"
                " created_at = '2026-01-01 00:00:00'"
            )
            conn.commit()

    @pytest.mark.parametrize("sort_by", ["title", "updated_at", "created_at", "rating"])
    @pytest.mark.parametrize("page_size", [1, 5])
    def test_a_walk_of_the_pages_returns_the_whole_library_once(
        self, temp_db: SQLiteDB, sort_by: str, page_size: int
    ) -> None:
        """Each sort's pages concatenate back into its own unpaged ordering."""
        self._seed(temp_db)
        whole = temp_db.get_content_items(sort_by=sort_by)
        assert len(whole) == len(self._TITLES) * len(ContentType)

        walked = _walk_pages(temp_db, page_size, sort_by=sort_by)

        assert walked == [item.id for item in whole]


class TestTheTitleSortAgreesWithThePythonKey:
    """SQLite's ordering of the stored sort key matches Python's own.

    The title sort moved from ``sorted(key=get_sort_title)`` to an ORDER BY
    over a column holding that function's output. Python compares strings by
    code point and SQLite's default collation compares the UTF-8 bytes, so the
    two agree — but nothing in the code says so, and the whole library's order
    rests on it. These are the inputs where a collation difference would show:
    non-ASCII letters, an empty key, a key that is only an article, and keys
    differing only in case.
    """

    # Titles chosen so that no two share a sort key, since equal keys would
    # leave the expected order to the id tiebreak and prove nothing about the
    # comparison itself. The articles, the case and the scripts are the point.
    _TITLES = (
        "The Zebra",
        "an Almond",
        "APRICOT",
        "apple",
        "1984",
        "",
        "The",
        "Ångström",
        "Éclair",
        "Über Alles",
        "Ωμέγα",
        "日本語",
    )

    def test_the_stored_order_is_the_order_the_python_key_gives(
        self, temp_db: SQLiteDB
    ) -> None:
        """The read returns exactly what sorting the titles in Python returns."""
        for index, title in enumerate(self._TITLES):
            temp_db.save_content_item(
                ContentItem(
                    id=f"sortable_{index}",
                    title=title,
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                )
            )

        results = temp_db.get_content_items()

        assert [item.title for item in results] == sorted(
            self._TITLES, key=get_sort_title
        )


class TestEveryWriteDoorLeavesTheDerivedColumnsCurrent:
    """No door into ``content_items`` leaves the derived columns behind it.

    ``sort_title`` and ``search_text`` are read *instead of* the title and the
    creator, so a door that writes the row without recomputing them makes the
    library order and search on values that are no longer there. Two of those
    doors — the sync upsert and the title dedup — are covered a case at a time
    above; this walks every one of them and states the invariant itself, so a
    door added later is measured against the rule rather than against whichever
    examples happened to be written down.

    A row that never had the columns written carries NULL, which sorts ahead of
    every real title and matches no search. Asserting the recomputed value
    catches that as well as a stale one.
    """

    # One creator per content type, each stored in that type's own column.
    _CREATOR_OF_TYPE = (
        (ContentType.BOOK, "Ursula K. Le Guin"),
        (ContentType.MOVIE, "Denis Villeneuve"),
        (ContentType.TV_SHOW, "Vince Gilligan"),
        (ContentType.VIDEO_GAME, "CD Projekt Red"),
    )

    @staticmethod
    def _assert_columns_describe_the_library(temp_db: SQLiteDB) -> None:
        """Every row's stored columns are what its title and creator derive to.

        Read back through the ordinary read rather than recomputed from what a
        test handed in: the creator column is fill-only and the title is
        rewritten by merges, so what a caller offered is not always what the
        row ends up holding.
        """
        items = {
            item.db_id: item for item in temp_db.get_content_items(include_ignored=True)
        }
        assert items, "the library is empty, so the sweep asserts nothing"
        with temp_db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, sort_title, search_text FROM content_items")
            stored = {
                row["id"]: (row["sort_title"], row["search_text"])
                for row in cursor.fetchall()
            }
        assert stored.keys() == items.keys()
        for db_id, item in items.items():
            assert stored[db_id] == (
                get_sort_title(item.title),
                build_search_text(item.title, item.author),
            )

    @staticmethod
    def _book(external_id: str, title: str, author: str | None = None) -> ContentItem:
        """Build a book the doors below can be pointed at."""
        return ContentItem(
            id=external_id,
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source="calibre_web",
        )

    def test_the_sync_door_creating_and_then_rewriting_a_row(
        self, temp_db: SQLiteDB
    ) -> None:
        """The insert, the retitle, and the creator a later sync fills."""
        temp_db.save_content_item(self._book("dune", "Dune"))
        self._assert_columns_describe_the_library(temp_db)

        temp_db.save_content_item(self._book("dune", "Dune", "Frank Herbert"))
        self._assert_columns_describe_the_library(temp_db)

        temp_db.save_content_item(self._book("dune", "Dune Messiah", "Frank Herbert"))
        self._assert_columns_describe_the_library(temp_db)

    def test_the_dedup_door(self, temp_db: SQLiteDB) -> None:
        """The row landed on carries NULL in both columns, which is what one
        written before they existed looks like: dedup fills, not just refreshes."""
        _insert_raw_book(temp_db, "openlibrary", "hobbit_b", "J.R.R. Tolkien")

        temp_db.save_content_item(self._book("hobbit_a", "The Hobbit"))

        self._assert_columns_describe_the_library(temp_db)

    def test_every_content_type_derives_from_its_own_creator_column(
        self, temp_db: SQLiteDB
    ) -> None:
        """The creator lives in a different column per type, and all four count.

        The derived columns pick the creator by content type, the way the read
        does, so a type left out of that expression would store a search text
        with an empty creator half and hide the name. Searching each name back
        is what catches a type missing from *both* expressions: the sweep
        compares the stored text against the creator the read reports, and the
        read picks it with a CASE of its own, so a type neither one names
        agrees with itself on None.

        Each search returns that type's item and only that one, so a match on
        the wrong item — or on every item — fails here too.
        """
        for content_type, creator in self._CREATOR_OF_TYPE:
            temp_db.save_content_item(
                ContentItem(
                    id=f"creator_{content_type.value}",
                    title=f"Something {content_type.value}",
                    author=creator,
                    content_type=content_type,
                    status=ConsumptionStatus.COMPLETED,
                )
            )

        self._assert_columns_describe_the_library(temp_db)
        for content_type, creator in self._CREATOR_OF_TYPE:
            found = temp_db.get_content_items(search=creator)
            assert [item.content_type for item in found] == [content_type], creator


class TestTheUpgradeThatFillsTheDerivedColumns:
    """A row reaching an open without the derived columns leaves it with them.

    The columns are the only inputs the library orders and searches by, so a
    row that misses them is invisible to search and sorts ahead of everything
    else. Both cases here are about *when* the fill runs, which no assertion
    over a single save can reach: they open a database twice.
    """

    @staticmethod
    def _derived_columns(db: SQLiteDB, title: str) -> tuple[str | None, ...]:
        """The stored sort key and search text of one row."""
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sort_title, search_text FROM content_items WHERE title = ?",
                (title,),
            )
            row = cursor.fetchone()
        return (row["sort_title"], row["search_text"])

    def test_a_row_written_without_the_columns_is_repaired_on_the_next_open(
        self, tmp_path: Path
    ) -> None:
        """The fill is not spent by a version stamp this database already carries.

        Reachable by downgrade-then-upgrade: this build stamps the current
        version, an older one writes rows without the columns it does not know
        about, and re-opening on this build reads the stamp. Nothing here
        rewinds the version, so a fill guarded on it would never run and the
        row would stay unsearchable for the life of the database.
        """
        db_path = tmp_path / "downgraded.db"
        db = SQLiteDB(db_path)
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, title, normalized_title, content_type, status)
                   VALUES (1, 'Neuromancer', 'neuromancer', 'book', 'completed')"""
            )
            cursor.execute(
                "INSERT INTO book_details (content_item_id, author)"
                " VALUES (?, 'William Gibson')",
                (cursor.lastrowid,),
            )
            conn.commit()
        assert self._derived_columns(db, "Neuromancer") == (None, None)

        reopened = SQLiteDB(db_path)

        assert self._derived_columns(reopened, "Neuromancer") == (
            get_sort_title("Neuromancer"),
            build_search_text("Neuromancer", "William Gibson"),
        )
        found = reopened.get_content_items(search="Gibson")
        assert [item.title for item in found] == ["Neuromancer"]


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


class TestRatingSetOnce:
    """Tests that rating is set once and never overwritten.

    Bug reported: Re-syncing from a source without ratings would overwrite
    existing ratings with None. Even syncing a different rating would
    clobber user-curated data.

    Fix: Rating is only written when the existing rating is None and the
    incoming rating is not None.
    """

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


class TestBlankReviewNeverFillsTheColumn:
    @pytest.mark.parametrize("blank_review", ["", "   ", "\n"])
    def test_insert_stores_null_for_a_blank_review_regression(
        self, temp_db: SQLiteDB, blank_review: str
    ) -> None:
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
        assert retrieved.review is None, (
            "a whitespace review filled the column, and once filled it blocked "
            "every later value"
        )

    def test_a_blank_review_does_not_overwrite_a_stored_one(
        self, temp_db: SQLiteDB
    ) -> None:
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

    def test_status_does_not_regress_completed_to_unread(
        self, temp_db: SQLiteDB
    ) -> None:
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


class TestDateCompletedProtection:
    """Tests that date_completed only advances forward.

    Rule: date_completed is only updated when the incoming value is not None
    AND it is later than the existing value.
    """

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

    def test_remaining_metadata_json_merges_additively(self, temp_db: SQLiteDB) -> None:
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
        """All seasons watched, with no status supplied, derives completed."""
        item = ContentItem(
            id="ui_6",
            title="Short Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"seasons": 3},
        )
        db_id = temp_db.save_content_item(item)

        temp_db.update_item_from_ui(db_id=db_id, seasons_watched=[1, 2, 3])

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

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
    caller today refuses a blank (``PATCH /api/items/{id}`` and
    ``library edit``), and the edit dialog sends null once the box is
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

    @pytest.mark.parametrize("blank_review", ["", "   ", "\n"])
    def test_blank_review_does_not_replace_the_stored_one_regression(
        self, temp_db: SQLiteDB, blank_review: str
    ) -> None:
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
        assert retrieved.review == "Loved it", (
            "this door overwrites rather than fills, so a completion carrying a "
            "blank replaced the review the user wrote — and, stored, that blank "
            "then blocked the fill-only leg against every later one"
        )

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

    Bug reported: completing Dune with an explicit "last Tuesday" left an item
    that an import had dated later still carrying the import's date. The
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

    No interface names a date today; ``complete`` and
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

    def test_an_explicit_date_earlier_than_the_stored_one_is_written_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A named date replaces a later one too — that is the defect above."""
        db_id = self._seeded(temp_db, date(2026, 12, 1))

        self._complete_on(temp_db, date(2026, 7, 28))

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed == date(2026, 7, 28)


class TestCompletionDoorFutureDate:
    """Reported: a caller names a completion date and nothing bounded the
    value, so ``date.fromisoformat`` let 9999-12-31 through. Fixed at the door,
    which ``complete`` and ``POST /api/complete`` share, not at one surface.
    """

    def _complete_on(self, temp_db: SQLiteDB, supplied: date) -> None:
        """Complete one book, naming *supplied* as the date."""
        with patch("src.utils.dates.utc_now", return_value=FROZEN_NOW):
            temp_db.complete_content_item(
                ContentItem(
                    id="book-1",
                    title="Dune",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    date_completed=supplied,
                )
            )

    def test_the_day_after_tomorrow_is_refused(self, temp_db: SQLiteDB) -> None:
        """Past the skew allowance is a day nobody has lived."""
        with pytest.raises(sqlite_db.FutureCompletionDateError):
            self._complete_on(temp_db, FROZEN_TODAY + timedelta(days=2))

    def test_a_refused_completion_writes_nothing_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The refusal rolls back every write the door had already made."""
        db_id = temp_db.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                date_completed=None,
            )
        )

        with pytest.raises(sqlite_db.FutureCompletionDateError):
            self._complete_on(temp_db, date(9999, 12, 31))

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.date_completed is None
        assert retrieved.status == ConsumptionStatus.CURRENTLY_CONSUMING


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

    def test_zero_stored_season_count_keeps_completed_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A stored season count of 0 must not un-complete a show.

        Reported: import flipped it to currently_consuming on every run.
        Cause: the guard now asks ``all_seasons_watched``, False for an
        unknown total. Fix: a falsy total is nothing to compare against.
        """
        item = ContentItem(
            id="tv_zero_seasons",
            title="Zero Season Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            metadata={"seasons": 0, "seasons_watched": [1, 2]},
        )
        db_id = temp_db.save_content_item(item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

        temp_db.save_content_item(item)

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons_watched") == [1, 2]

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
        assert retrieved.metadata.get("seasons_watched") == [1, 2]

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


class TestSeasonsWatchedSyncUnionRegression:
    """A sync could not finish a season (#107).

    Symptom: Trakt never completed a watched season. Cause: existing-wins on
    key presence froze the empty list the first sync wrote. Fix: a union.
    """

    @staticmethod
    def _sync(temp_db: SQLiteDB, seasons_watched: list[int]) -> int:
        """Save the show as a Trakt sync does, reporting *seasons_watched*."""
        return temp_db.save_content_item(
            ContentItem(
                id="trakt:union",
                title="Union Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                source="trakt",
                metadata={"total_seasons": 5, "seasons_watched": seasons_watched},
            )
        )

    def test_sync_promotes_a_season_finished_since_the_last_sync_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A season Trakt now reports complete joins the stored list."""
        db_id = self._sync(temp_db, [])
        self._sync(temp_db, [1])
        self._sync(temp_db, [1, 2])

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata["seasons_watched"] == [1, 2]

    def test_sync_never_unticks_a_manual_check_off_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A season the sync omits survives it.

        The promotion test above passes just as well if the union regressed to
        a replace, so this is the half guarding the watch history.
        """
        db_id = self._sync(temp_db, [1])
        temp_db.update_item_from_ui(db_id, seasons_watched=[1, 5])
        self._sync(temp_db, [1, 2])

        retrieved = temp_db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.metadata["seasons_watched"] == [1, 2, 5]


class TestTvSeasonCountFromTraktMetadata:
    """``total_seasons`` used to land in the metadata blob, leaving the
    ``seasons`` column NULL, so a fully watched show never completed.
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

    def test_movie_year_and_runtime_populate_their_columns_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Radarr's ``year`` and ``runtime_minutes`` reach the movie columns."""
        db_id = self._save_radarr_movie(temp_db)

        retrieved = temp_db.get_content_item(db_id)

        assert retrieved is not None
        assert retrieved.metadata["release_year"] == 2010
        assert retrieved.metadata["runtime"] == 148

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

    def test_a_later_sync_does_not_overwrite_a_stored_creator(
        self, temp_db: SQLiteDB
    ) -> None:
        first = temp_db.save_content_item(
            ContentItem(
                id="movie-fill-only",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                source="radarr",
                author="Denis Villeneuve",
            )
        )
        # Same source and id, so the row matches its own: no creator veto.
        second = temp_db.save_content_item(
            ContentItem(
                id="movie-fill-only",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                source="radarr",
                author="Wrong Person",
            )
        )

        assert second == first
        retrieved = temp_db.get_content_item(first)
        assert retrieved is not None
        assert retrieved.author == "Denis Villeneuve"


class TestEachSourceHoldsItsOwnExternalId:
    @staticmethod
    def _game(source: str, external_id: str, title: str) -> ContentItem:
        return ContentItem(
            id=external_id,
            title=title,
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=source,
        )

    def test_re_syncing_either_source_after_a_merge_changes_nothing(
        self, temp_db: SQLiteDB
    ) -> None:
        """The id was recorded only on the INSERT, so the loser of the dedup
        race took the title path, and reported an update, on every sync."""
        temp_db.save_content_item(self._game("gog", "tf2", "Team Fortress 2"))
        temp_db.save_content_item(self._game("steam", "440", "Team Fortress 2"))

        outcomes = [
            temp_db.save_content_item_outcome(
                self._game(source, external_id, "Team Fortress 2")
            ).outcome
            for source, external_id in (("steam", "440"), ("gog", "tf2"))
        ]

        assert outcomes == [SaveOutcome.UNCHANGED, SaveOutcome.UNCHANGED]

    def test_two_sources_sharing_a_bare_numeric_id_stay_two_items(
        self, temp_db: SQLiteDB
    ) -> None:
        """Syncing GOG's product 440 found Steam's app 440 and overwrote it."""
        first = temp_db.save_content_item(self._game("steam", "440", "Team Fortress 2"))
        second = temp_db.save_content_item(self._game("gog", "440", "Cyberpunk 2077"))

        assert first != second
        assert sorted(
            item.title
            for item in temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        ) == ["Cyberpunk 2077", "Team Fortress 2"]

    def test_an_item_matched_by_its_own_id_deletes_no_other_row(
        self, temp_db: SQLiteDB
    ) -> None:
        """Steam's retitle found its own row, then absorbed every same-titled
        row beside it — taking ids only GOG held down with them."""
        gog_1993 = temp_db.save_content_item(self._game("gog", "doom-1993", "Doom"))
        gog_2016 = temp_db.save_content_item(self._game("gog", "doom-2016", "Doom"))
        steam = temp_db.save_content_item(self._game("steam", "379720", "DOOM (2016)"))

        temp_db.save_content_item(self._game("steam", "379720", "Doom"))

        # A deleted row's ids cascade away with it, so an empty list is a loss.
        assert [
            _external_ids(temp_db, db_id) for db_id in (gog_1993, gog_2016, steam)
        ] == [
            [("gog", "doom-1993")],
            [("gog", "doom-2016")],
            [("steam", "379720")],
        ]

    def test_a_source_whose_id_the_absorbed_row_holds_lands_beside_the_group(
        self, temp_db: SQLiteDB
    ) -> None:
        """The guard read one row while the SELECT answered as its survivor, so a
        source's second item resolved onto a group already holding its first and
        was dropped by the INSERT OR IGNORE that followed."""
        calibre = _insert_raw_item(temp_db, "dune", "Dune", "dune", source="calibre")
        goodreads = _insert_raw_item(
            temp_db, "234225", "Dune", "dune", source="goodreads"
        )
        temp_db.merge_content_items(calibre, goodreads, MergeEvidence.MANUAL)

        second_edition = temp_db.save_content_item(
            self._game("goodreads", "44767458", "Dune")
        )

        assert second_edition != calibre
        assert _external_ids(temp_db, second_edition) == [("goodreads", "44767458")]

    def test_a_title_collision_lands_on_the_oldest_row(self, temp_db: SQLiteDB) -> None:
        """Which duplicate a new source attaches to decides whose history it
        joins, and an unordered fetch made that a coin toss."""
        oldest = _insert_raw_item(temp_db, "old", "Doom", "doom")
        _insert_raw_item(temp_db, "new", "Doom", "doom")

        landed = temp_db.save_content_item(self._game("gog", "doom-gog", "Doom"))

        assert landed == oldest

    def test_a_read_lists_the_ids_other_sources_hold_and_invents_none(
        self, temp_db: SQLiteDB
    ) -> None:
        """A source emitting no id leaves its row unnamed in the id table, and
        reading a later source's id beside that source name fabricated a pair,
        which both interfaces then showed as the row's own."""
        db_id = temp_db.save_content_item(
            ContentItem(
                title="Doom",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                source="generic_csv",
            )
        )
        temp_db.save_content_item(self._game("gog", "doom-gog", "Doom"))
        stored = temp_db.get_content_item(db_id)
        assert stored is not None
        assert item_to_dict(stored)["external_ids"] == [
            {"source": "gog", "external_id": "doom-gog"}
        ]

        temp_db.save_content_item(stored)

        assert _external_ids(temp_db, db_id) == [("gog", "doom-gog")]

    def test_an_item_no_source_named_reads_back_with_an_empty_list(
        self, temp_db: SQLiteDB
    ) -> None:
        """The read parses the id list unconditionally, so an aggregate over no
        rows must be ``[]``."""
        db_id = temp_db.save_content_item(
            ContentItem(
                title="Doom",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                source="generic_csv",
            )
        )

        stored = temp_db.get_content_item(db_id)

        assert stored is not None
        assert stored.id is None
        assert item_to_dict(stored)["external_ids"] == []

    def test_an_id_carrying_json_punctuation_survives_the_round_trip(
        self, temp_db: SQLiteDB
    ) -> None:
        """An imported id is whatever the file's column held, so a delimited
        string would split one pair into garbage."""
        awkward = 'a"b\\c,d\ne'
        db_id = temp_db.save_content_item(self._game("generic_csv", awkward, "Doom"))

        stored = temp_db.get_content_item(db_id)

        assert stored is not None
        assert stored.id == awkward
        assert item_to_dict(stored)["external_ids"] == [
            {"source": "generic_csv", "external_id": awkward}
        ]


class TestTheIdTableIsSeekedNotScanned:
    def test_it_reaches_the_row_through_the_id_table_and_scans_nothing(
        self, temp_db: SQLiteDB
    ) -> None:
        """A sync runs this once per item, so a scan costs the product of two."""
        with temp_db.connection() as conn:
            plan = [
                row["detail"]
                for row in conn.execute(
                    f"EXPLAIN QUERY PLAN {sqlite_db._ITEM_ID_BY_SOURCE_EXTERNAL_ID}",
                    {
                        "user_id": 1,
                        "source": "steam",
                        "external_id": "440",
                        "content_type": "video_game",
                    },
                ).fetchall()
            ]

        assert plan[0].startswith("SEARCH x")
        assert [step for step in plan if "SCAN" in step] == []

    def test_the_library_read_seeks_the_ids_it_reports_for_each_row(
        self, temp_db: SQLiteDB
    ) -> None:
        """DEFECT: a COALESCE hid the indexed column, so every row re-scanned."""
        with temp_db.connection() as conn:
            plan = [
                row["detail"]
                for row in conn.execute(
                    f"EXPLAIN QUERY PLAN {sqlite_db._CONTENT_ITEM_SELECT}"
                    " WHERE ci.user_id = ?",
                    [1],
                ).fetchall()
            ]

        assert [step for step in plan if "SCAN x" in step] == []
        assert [step for step in plan if "SCAN owner" in step] == []

    def test_the_title_path_seeks_the_ids_of_the_group_it_weighs(
        self, temp_db: SQLiteDB
    ) -> None:
        """Flattening the OR into a join hides the indexed column, as a COALESCE
        did above. A first sync into an existing library takes this path per
        item, so a scan costs the product of two."""
        with temp_db.connection() as conn:
            plan = [
                row["detail"]
                for row in conn.execute(
                    f"EXPLAIN QUERY PLAN {sqlite_db._TITLE_MATCH_CANDIDATES}",
                    (1, "video_game", "portal 2", "steam", "620"),
                ).fetchall()
            ]

        assert [step for step in plan if "SCAN x" in step] == []
        assert [step for step in plan if "SEARCH x" in step] != []


class TestCrossSourceDuplicateDetectionRegression:
    """Whatever a merge fails to carry is what the library stops showing."""

    def test_merge_unions_seasons_watched_dates_with_later_date_winning_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """A keep-wins blob merge dropped a season only the absorbed row dated,
        and froze a shared season at the survivor's staler date."""
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
                   (user_id, title, normalized_title,
                    content_type, status, source)
                   VALUES (1, 'Regression Show',
                           'regression show', 'tv_show',
                           'currently_consuming', 'sonarr')""",
            )
            dup_id = cursor.lastrowid
            assert dup_id is not None
            _record_raw_external_id(cursor, dup_id, "sonarr", "sonarr-show")
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

        _merged(temp_db, keep_id, dup_id)

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

    def test_merge_keeps_later_date_completed_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """The merge keeps the later date_completed of the two rows."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-hades",
            title="Hades",
            normalized_title="hades",
            date_completed="2024-01-15",
            source="steam",
        )
        dup_id = _insert_raw_item(
            temp_db,
            external_id="blog-hades",
            title="Hades",
            normalized_title="hades",
            date_completed="2024-06-20",
            source="personal_site",
        )

        _merged(temp_db, keep_id, dup_id)

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        assert all_games[0].date_completed == date(2024, 6, 20)

    def test_the_survivor_carries_what_only_the_absorbed_row_held(
        self, temp_db: SQLiteDB
    ) -> None:
        """A rating the survivor lacks, its detail row when the survivor has
        none, and the id the absorbed row's own source re-attaches by."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="steam-hollow",
            title="Hollow Knight",
            normalized_title="hollow knight",
            source="steam",
        )
        dup_id = _insert_raw_item(
            temp_db,
            external_id="blog-hollow",
            title="Hollow Knight",
            normalized_title="hollow knight",
            rating=5,
            source="personal_site",
        )
        with temp_db.connection() as conn:
            conn.execute(
                "INSERT INTO video_game_details (content_item_id, developer, genres)"
                " VALUES (?, ?, ?)",
                (dup_id, "Team Cherry", '["Metroidvania"]'),
            )
            conn.commit()

        _merged(temp_db, keep_id, dup_id)

        survivor = temp_db.get_content_item(keep_id)
        assert survivor is not None
        assert survivor.rating == 5
        assert survivor.author == "Team Cherry"
        assert "Metroidvania" in (survivor.metadata.get("genres") or [])
        assert [(pair.source, pair.external_id) for pair in survivor.external_ids] == [
            ("personal_site", "blog-hollow"),
            ("steam", "steam-hollow"),
        ]

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
                   (user_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'Breaking Bad', 'breaking bad',
                           'tv_show', 'completed', 'sonarr')""",
            )
            keep_id = cursor.lastrowid
            assert keep_id is not None
            _record_raw_external_id(cursor, keep_id, "sonarr", "sonarr-bb")
            cursor.execute(
                """INSERT INTO tv_show_details
                   (content_item_id, seasons, episodes)
                   VALUES (?, 2, 20)""",
                (keep_id,),
            )
            # Insert duplicate row: seasons=4, episodes=15
            cursor.execute(
                """INSERT INTO content_items
                   (user_id, title, normalized_title, content_type,
                    status, source)
                   VALUES (1, 'Breaking Bad', 'breaking bad',
                           'tv_show', 'completed', 'blog')""",
            )
            dup_id = cursor.lastrowid
            assert dup_id is not None
            _record_raw_external_id(cursor, dup_id, "blog", "blog-bb")
            cursor.execute(
                """INSERT INTO tv_show_details
                   (content_item_id, seasons, episodes)
                   VALUES (?, 4, 15)""",
                (dup_id,),
            )
            conn.commit()

        _merged(temp_db, keep_id, dup_id)

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        # seasons: dup (4) > kept (2), so 4 wins
        assert retrieved.metadata is not None
        assert retrieved.metadata.get("seasons") == 4
        # episodes: kept (20) > dup (15), so 20 is preserved
        assert retrieved.metadata.get("episodes") == 20

    def test_a_merge_carries_the_detail_tables_onto_the_survivor(
        self, tmp_path: Path
    ) -> None:
        """Genres, tags and metadata the absorbed row held reach the survivor.

        That row leaves every read, so anything the detail merge drops here is
        what the library stops showing."""
        db_path = tmp_path / "migration_detail_test.db"
        db = SQLiteDB(db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        cursor = conn.cursor()

        # Insert kept row with some detail data
        cursor.execute(
            """INSERT INTO content_items
               (user_id, title, normalized_title, content_type,
                status, rating, source)
               VALUES (1, 'Dishonored',
                       'dishonored', 'video_game', 'completed', 5, 'steam')"""
        )
        keep_id = cursor.lastrowid
        assert keep_id is not None
        _record_raw_external_id(cursor, keep_id, "steam", "steam-dishonored")
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
               (user_id, title, normalized_title, content_type,
                status, review, source)
               VALUES (1, 'Dishonored',
                       'dishonored', 'video_game', 'completed',
                       'Masterpiece of level design', 'blog')"""
        )
        dup_id = cursor.lastrowid
        assert dup_id is not None
        _record_raw_external_id(cursor, dup_id, "blog", "blog-dishonored")
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

        _merged(db, keep_id, dup_id)

        # Should now show one row, the other hidden behind it
        cursor.execute("SELECT COUNT(*) FROM content_items WHERE merged_into IS NULL")
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

        # The dup's row is hidden behind the survivor, not deleted
        cursor.execute(
            "SELECT merged_into FROM content_items WHERE id = ?",
            (dup_id,),
        )
        assert cursor.fetchone()["merged_into"] == keep_id

        # And it keeps its own detail row for an unmerge to give back
        cursor.execute(
            "SELECT publisher FROM video_game_details WHERE content_item_id = ?",
            (dup_id,),
        )
        assert cursor.fetchone()["publisher"] == "Bethesda"


class TestDuplicateMergePreservesState:
    """A merge carried only rating, review and date_completed, so a completed
    duplicate reverted the kept row to unread and an ignored one un-ignored it,
    putting the item back among the recommendation candidates."""

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
        dup_id = _insert_raw_item(
            temp_db,
            external_id="blog-portal2",
            title="Portal 2",
            normalized_title="portal 2",
            status="completed",
            source="personal_site",
        )

        _merged(temp_db, keep_id, dup_id)

        all_games = temp_db.get_content_items(content_type=ContentType.VIDEO_GAME)
        assert len(all_games) == 1
        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED

    def test_completed_and_ignored_item_survives_dedupe_regression(
        self, temp_db: SQLiteDB
    ) -> None:
        """Each row held only its own state, so whichever the merge overwrote
        was the one the library stopped showing."""
        keep_id = _insert_raw_item(
            temp_db,
            external_id="early-sync",
            title="Portal 2",
            normalized_title="portal 2",
            status="unread",
            ignored=True,
            source="personal_site",
        )
        dup_id = _insert_raw_item(
            temp_db,
            external_id="steam-620",
            title="Portal 2™",
            normalized_title="portal 2",
            status="completed",
            ignored=False,
            source="steam",
        )

        _merged(temp_db, keep_id, dup_id)

        retrieved = temp_db.get_content_item(keep_id)
        assert retrieved is not None
        assert retrieved.status == ConsumptionStatus.COMPLETED
        assert retrieved.ignored is True


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
    """A NULL joined column reads as absent data, not as a missing column.

    ``_get_row_value`` swallowed KeyError, making a column absent from the
    SELECT indistinguishable from one holding NULL. The read path subscripts
    ``sqlite3.Row`` directly.
    """

    def test_null_joined_columns_still_read_as_absent_data(
        self, temp_db: SQLiteDB
    ) -> None:
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
