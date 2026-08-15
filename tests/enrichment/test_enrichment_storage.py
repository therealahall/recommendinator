"""Tests for enrichment-related storage functionality."""

from pathlib import Path

import pytest

from src.enrichment.manager import merge_enrichment
from src.enrichment.provider_base import EnrichmentResult
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.content_length import (
    LengthPreference,
    classify_length,
    score_length_match,
)
from src.storage.manager import StorageManager
from src.storage.schema import create_user


class TestEnrichmentStatusMethods:
    """Tests for enrichment status storage methods."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    @pytest.fixture
    def sample_item(self) -> ContentItem:
        """Create a sample content item."""
        return ContentItem(
            id="test123",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Action"]},
        )

    def test_mark_enrichment_complete(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        """Test marking an item as successfully enriched."""
        db_id = storage_manager.save_content_item(sample_item)

        storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")

        status = storage_manager.get_enrichment_status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "tmdb"
        assert status["enrichment_quality"] == "high"
        assert status["needs_enrichment"] is False
        assert status["enrichment_error"] is None

    def test_mark_enrichment_failed(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        """A failure records the error and leaves the item queued for retry.

        A failed provider never said whether it has the item, so the outcome is
        unknown: ``needs_enrichment`` stays set and the item is still returned
        by ``get_items_needing_enrichment``.
        """
        db_id = storage_manager.save_content_item(sample_item)

        storage_manager.mark_enrichment_failed(db_id, "API rate limit exceeded")

        status = storage_manager.get_enrichment_status(db_id)
        assert status is not None
        assert status["enrichment_error"] == "API rate limit exceeded"
        assert status["needs_enrichment"] is True
        queued = storage_manager.get_items_needing_enrichment()
        assert [db for db, _item in queued] == [db_id]

    def test_reset_enrichment_status_by_provider(
        self, storage_manager: StorageManager
    ) -> None:
        """Test resetting enrichment status by provider."""
        # Create and enrich two items with different providers
        item1 = ContentItem(
            id="movie1",
            title="Movie 1",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        item2 = ContentItem(
            id="movie2",
            title="Movie 2",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        db_id1 = storage_manager.save_content_item(item1)
        db_id2 = storage_manager.save_content_item(item2)

        storage_manager.mark_enrichment_complete(db_id1, "tmdb", "high")
        storage_manager.mark_enrichment_complete(db_id2, "other", "high")

        # Reset only tmdb items
        count = storage_manager.reset_enrichment_status(provider="tmdb")

        assert count == 1
        assert storage_manager.get_enrichment_status(db_id1)["needs_enrichment"] is True
        assert (
            storage_manager.get_enrichment_status(db_id2)["needs_enrichment"] is False
        )


class TestGetItemsNeedingEnrichment:
    """Tests for getting items that need enrichment."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_get_items_no_status(self, storage_manager: StorageManager) -> None:
        """Test getting items with no enrichment status (new items)."""
        item = ContentItem(
            id="test1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        storage_manager.save_content_item(item)

        # New items should need enrichment
        items = storage_manager.get_items_needing_enrichment()

        assert len(items) == 1
        assert items[0][1].title == "Test Movie"

    def test_get_items_excludes_enriched(self, storage_manager: StorageManager) -> None:
        """Test that already-enriched items are excluded."""
        item = ContentItem(
            id="test1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = storage_manager.save_content_item(item)
        storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")

        items = storage_manager.get_items_needing_enrichment()

        assert len(items) == 0

    def test_get_items_by_content_type(self, storage_manager: StorageManager) -> None:
        """Test filtering by content type."""
        movie = ContentItem(
            id="movie1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        book = ContentItem(
            id="book1",
            title="Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        storage_manager.save_content_item(movie)
        storage_manager.save_content_item(book)

        movie_items = storage_manager.get_items_needing_enrichment(
            content_type=ContentType.MOVIE
        )
        book_items = storage_manager.get_items_needing_enrichment(
            content_type=ContentType.BOOK
        )

        assert len(movie_items) == 1
        assert movie_items[0][1].title == "Test Movie"

        assert len(book_items) == 1
        assert book_items[0][1].title == "Test Book"

    def test_after_db_id_pages_forward_through_the_queue(
        self, storage_manager: StorageManager
    ) -> None:
        """``after_db_id`` excludes handled items in SQL, not in the caller.

        The enrichment manager leaves an item queued when its provider errored,
        so it walks this cursor forward to reach the items behind it without
        re-fetching — and re-hydrating — the ones it already attempted.
        """
        db_ids = [
            storage_manager.save_content_item(
                ContentItem(
                    id=f"movie{index}",
                    title=f"Movie {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )
            for index in range(4)
        ]

        page = storage_manager.get_items_needing_enrichment(
            limit=2, after_db_id=db_ids[1]
        )

        assert [db_id for db_id, _item in page] == db_ids[2:]


class TestCountItemsNeedingEnrichment:
    """Tests for counting items needing enrichment.

    The count and get methods share ``_build_enrichment_query`` so the same
    WHERE clause drives both. These tests verify count parity with the get
    path and exercise the COUNT(*) -> int cursor branch.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_count_filters_by_content_type(
        self, storage_manager: StorageManager
    ) -> None:
        """`content_type` parameter scopes the count to a single type."""
        storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        storage_manager.save_content_item(
            ContentItem(
                id="book1",
                title="Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        assert (
            storage_manager.count_items_needing_enrichment(
                content_type=ContentType.MOVIE
            )
            == 1
        )
        assert (
            storage_manager.count_items_needing_enrichment(
                content_type=ContentType.BOOK
            )
            == 1
        )

    def test_count_matches_get_length(self, storage_manager: StorageManager) -> None:
        """Count and get must agree — they share the same WHERE clause."""
        for index in range(3):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"movie{index}",
                    title=f"Movie {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )
            if index == 0:
                # Enrich one item — both methods should agree it's excluded.
                storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")

        items = storage_manager.get_items_needing_enrichment(limit=100)
        count = storage_manager.count_items_needing_enrichment()

        assert count == 2
        assert len(items) == 2


class TestEnrichmentStats:
    """Tests for enrichment statistics."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_the_four_states_partition_the_library_regression(
        self, storage_manager: StorageManager
    ) -> None:
        """Every item is counted once across enriched/pending/not_found/failed.

        Reported symptom: a failed item was listed under both "Pending" and
        "Failed", and the CLI and the Data page print the four counts as a flat
        list — so a library of five items read as six.

        Root cause: a failure keeps ``needs_enrichment = 1`` so the item is
        retried, and ``pending`` counted every such row regardless of whether
        its last attempt had errored.

        Fix: ``pending`` excludes rows carrying an enrichment error, which the
        ``failed`` count already covers. The two remain the same item — it is
        still queued for retry — but it is reported once, under the state that
        tells the operator more.
        """
        db_ids = [
            storage_manager.save_content_item(
                ContentItem(
                    id=f"item{index}",
                    title=f"Item {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )
            for index in range(5)
        ]
        storage_manager.mark_enrichment_complete(db_ids[0], "tmdb", "high")
        storage_manager.mark_enrichment_complete(db_ids[1], "none", "not_found")
        storage_manager.mark_enrichment_failed(db_ids[2], "tmdb: HTTP 503")
        storage_manager.mark_item_needs_enrichment(db_ids[3])
        # Item 4 is untracked.

        stats = storage_manager.get_enrichment_stats()

        assert stats["enriched"] == 1
        assert stats["not_found"] == 1
        assert stats["failed"] == 1
        assert stats["pending"] == 2  # 1 marked + 1 untracked
        assert (
            stats["enriched"] + stats["pending"] + stats["not_found"] + stats["failed"]
            == stats["total"]
        ), "the four reported states must account for each item exactly once"

    def test_stats_for_one_user_exclude_another_users_items(
        self, storage_manager: StorageManager
    ) -> None:
        """The user-filtered stats join content_items and alias enrichment_status.

        Every count and both breakdowns take a different query shape under a
        user filter, so a mistake there is invisible to the unfiltered tests.
        """
        with storage_manager.sqlite_db.connection() as conn:
            other_user = create_user(conn, username="other")
        mine = [
            storage_manager.save_content_item(
                ContentItem(
                    id=f"mine{index}",
                    title=f"Mine {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                ),
                user_id=1,
            )
            for index in range(3)
        ]
        theirs = storage_manager.save_content_item(
            ContentItem(
                id="theirs",
                title="Theirs",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=other_user,
        )
        storage_manager.mark_enrichment_complete(mine[0], "tmdb", "high")
        storage_manager.mark_enrichment_failed(mine[1], "tmdb: HTTP 503")
        storage_manager.mark_enrichment_complete(theirs, "rawg", "medium")
        # mine[2] is untracked.

        stats = storage_manager.get_enrichment_stats(user_id=1)

        assert stats["total"] == 3
        assert stats["enriched"] == 1
        assert stats["failed"] == 1
        assert stats["pending"] == 1
        assert stats["by_provider"] == {"tmdb": 1}
        assert stats["by_quality"] == {"high": 1}


class TestTagsAndDescriptionStorage:
    """Tests for storing and retrieving tags and description."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_save_and_load_movie_with_tags(
        self, storage_manager: StorageManager
    ) -> None:
        """Test saving and loading a movie with tags and description."""
        item = ContentItem(
            id="movie1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Action", "Sci-Fi"],
                "tags": ["blockbuster", "franchise"],
                "description": "An epic space adventure.",
                "director": "Test Director",
            },
        )

        db_id = storage_manager.save_content_item(item)
        loaded = storage_manager.get_content_item(db_id)

        assert loaded is not None
        assert loaded.metadata.get("tags") == ["blockbuster", "franchise"]
        assert loaded.metadata.get("description") == "An epic space adventure."

    def test_get_content_item_db_id(self, storage_manager: StorageManager) -> None:
        """Test getting content item database ID by external ID."""
        item = ContentItem(
            id="movie123",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        db_id = storage_manager.save_content_item(item)
        found_id = storage_manager.get_content_item_db_id("movie123", ContentType.MOVIE)

        assert found_id == db_id


class TestEnrichmentFilter:
    """Tests for the enrichment-state filter on get_content_items.

    The filter joins enrichment_status. An item is enriched when it has a row
    with needs_enrichment=0, enrichment_error IS NULL, and a real provider.
    Everything else (no row, needs_enrichment=1, not_found, failed) is not
    enriched. ``enriched`` and ``not_enriched`` partition the library.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def _save(self, storage: StorageManager, external_id: str) -> int:
        return storage.save_content_item(
            ContentItem(
                id=external_id,
                title=f"Movie {external_id}",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

    def test_not_enriched_includes_all_four_subcases(
        self, storage_manager: StorageManager
    ) -> None:
        """not_enriched returns: no row, needs_enrichment=1, not_found, failed."""
        no_row = self._save(storage_manager, "no_row")
        pending = self._save(storage_manager, "pending")
        not_found = self._save(storage_manager, "not_found")
        failed = self._save(storage_manager, "failed")
        enriched = self._save(storage_manager, "enriched")

        storage_manager.mark_item_needs_enrichment(pending)
        storage_manager.mark_enrichment_complete(not_found, "tmdb", "not_found")
        storage_manager.mark_enrichment_failed(failed, "boom")
        storage_manager.mark_enrichment_complete(enriched, "tmdb", "high")

        items = storage_manager.get_content_items(enrichment="not_enriched")
        db_ids = {item.db_id for item in items}

        assert db_ids == {no_row, pending, not_found, failed}
        assert all(item.enriched is False for item in items)

    def test_enriched_returns_complement(self, storage_manager: StorageManager) -> None:
        """enriched returns only items with a clean enrichment_status row."""
        self._save(storage_manager, "no_row")
        high = self._save(storage_manager, "high")
        medium = self._save(storage_manager, "medium")

        storage_manager.mark_enrichment_complete(high, "tmdb", "high")
        storage_manager.mark_enrichment_complete(medium, "tmdb", "medium")

        items = storage_manager.get_content_items(enrichment="enriched")
        db_ids = {item.db_id for item in items}

        assert db_ids == {high, medium}
        assert all(item.enriched is True for item in items)

    def test_not_enriched_combines_with_content_type_filter(
        self, storage_manager: StorageManager
    ) -> None:
        """not_enriched AND a content_type filter compose correctly.

        Guards SQL precedence of the AND/OR predicate composition: the
        not_enriched fragment is parenthesized so the content_type filter
        narrows it rather than widening via a stray OR.
        """
        movie_pending = self._save(storage_manager, "movie_pending")
        movie_enriched = self._save(storage_manager, "movie_enriched")
        storage_manager.mark_enrichment_complete(movie_enriched, "tmdb", "high")

        book_pending = storage_manager.save_content_item(
            ContentItem(
                id="book_pending",
                title="Book Pending",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        items = storage_manager.get_content_items(
            content_type=ContentType.MOVIE, enrichment="not_enriched"
        )
        db_ids = {item.db_id for item in items}

        # Only the not-enriched movie; the enriched movie and the book are out.
        assert db_ids == {movie_pending}
        assert movie_enriched not in db_ids
        assert book_pending not in db_ids


class TestManualMetadataEdit:
    """Tests for persisting manual genres/tags/description via update_item_from_ui.

    Manual edits overwrite the detail-table values and mark the item enriched
    with the ``manual`` provider so it drops out of the not_enriched filter and
    is never re-queued for automatic enrichment.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_manual_edit_persists_and_marks_enriched(
        self, storage_manager: StorageManager
    ) -> None:
        """Manual fields persist and the item becomes enriched."""
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        updated = storage_manager.update_item_from_ui(
            db_id=db_id,
            status="unread",
            genres=["Drama", "Thriller"],
            tags=["slow-burn"],
            description="A tense character study.",
        )
        assert updated is True

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres") == ["Drama", "Thriller"]
        assert loaded.metadata.get("tags") == ["slow-burn"]
        assert loaded.metadata.get("description") == "A tense character study."
        assert loaded.enriched is True

        status = storage_manager.get_enrichment_status(db_id)
        assert status["enrichment_provider"] == "manual"
        assert status["needs_enrichment"] is False

    def test_manual_edit_overwrites_existing_values(
        self, storage_manager: StorageManager
    ) -> None:
        """Manual genres replace prior values rather than merging additively.

        Also proves a None field (description here) leaves the stored value
        as-is: only supplied fields are overwritten.
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={
                    "genres": ["Action", "Comedy"],
                    "tags": ["old"],
                    "description": "Original synopsis.",
                },
            )
        )

        storage_manager.update_item_from_ui(
            db_id=db_id,
            status="unread",
            genres=["Drama"],
            tags=["new"],
        )

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres") == ["Drama"]
        assert loaded.metadata.get("tags") == ["new"]
        # description was omitted (None) so it must be left untouched.
        assert loaded.metadata.get("description") == "Original synopsis."

    def test_status_only_edit_does_not_mark_enriched(
        self, storage_manager: StorageManager
    ) -> None:
        """Editing only status/rating leaves enrichment state untouched."""
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        storage_manager.update_item_from_ui(db_id=db_id, status="completed", rating=5)

        assert storage_manager.get_enrichment_status(db_id) is None
        assert storage_manager.get_content_item(db_id).enriched is False

    def test_genres_empty_list_clears_while_none_leaves_as_is(
        self, storage_manager: StorageManager
    ) -> None:
        """genres=[] clears all genres; genres=None leaves them untouched.

        An empty list is a deliberate "clear" that stores an empty JSON array
        and still marks the item enriched; None means "no change".
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"genres": ["Action", "Comedy"]},
            )
        )

        # genres=[] clears; tags omitted (None) so they are left as-is.
        storage_manager.update_item_from_ui(db_id=db_id, status="unread", genres=[])

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres", []) == []
        assert loaded.enriched is True

        # The stored column is an empty JSON array, not NULL.
        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT genres FROM movie_details WHERE content_item_id = ?",
                (db_id,),
            )
            assert cursor.fetchone()["genres"] == "[]"
        finally:
            conn.close()


class TestGameLengthAfterEnrichment:
    """The length scorer reads what RAWG enrichment leaves in the database.

    ``average_playtime_hours`` has no detail-table column, so it rides in the
    free-form metadata blob. These tests take the whole path a real game walks
    (Steam item, RAWG result, gap-filling merge, save, load) and score the item
    that comes back, so a blob that dropped the key would fail here rather than
    quietly leaving every game unclassified.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    @staticmethod
    def _steam_game() -> ContentItem:
        """A game Steam ingested, carrying the user's own 300 hours."""
        return ContentItem(
            id="game1",
            title="Vampire Survivors",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            metadata={"playtime_minutes": 18000, "playtime_hours": 300.0},
        )

    def test_rawg_average_survives_the_round_trip_and_classifies(
        self, storage_manager: StorageManager
    ) -> None:
        item = self._steam_game()
        result = EnrichmentResult(
            external_id="rawg:301566",
            extra_metadata={"average_playtime_hours": 6, "metacritic": 77},
            provider="rawg",
        )
        merged = merge_enrichment(item.metadata, result)
        item.metadata = merged

        loaded = storage_manager.get_content_item(
            storage_manager.save_content_item(item)
        )

        assert loaded.metadata.get("average_playtime_hours") == 6
        assert classify_length(loaded) == LengthPreference.SHORT
        assert score_length_match(loaded, {"video_game": "short"}) == 1.0
        # The user's own hours are stored and exported unchanged beside it
        assert loaded.metadata.get("playtime_hours") == 300.0

    def test_enrichment_never_fills_the_users_own_hours(
        self, storage_manager: StorageManager
    ) -> None:
        """RAWG's average stays out of the field the export calls hours_played.

        A GOG or Epic game has no own playtime, so the gap-filling merge used to
        drop RAWG's average straight into ``playtime_hours`` and export it as
        hours the user never played. The average now has its own key and that
        field stays empty.
        """
        item = ContentItem(
            id="game2",
            title="Cyberpunk 2077",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"platform": "GOG"},
        )
        result = EnrichmentResult(
            external_id="rawg:41494",
            extra_metadata={"average_playtime_hours": 60},
            provider="rawg",
        )
        item.metadata = merge_enrichment(item.metadata, result)

        loaded = storage_manager.get_content_item(
            storage_manager.save_content_item(item)
        )

        assert "playtime_hours" not in loaded.metadata
        assert classify_length(loaded) == LengthPreference.LONG
