"""Tests for enrichment-related storage functionality."""

from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


class TestEnrichmentSchema:
    """Tests for enrichment schema changes."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

    def test_enrichment_status_table_created(
        self, storage_manager: StorageManager
    ) -> None:
        """Test that enrichment_status table is created."""
        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='enrichment_status'"
            )
            result = cursor.fetchone()
            assert result is not None
        finally:
            conn.close()

    def test_tags_column_added_to_book_details(
        self, storage_manager: StorageManager
    ) -> None:
        """Test that tags column is added to book_details."""
        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(book_details)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "tags" in columns
            assert "description" in columns
        finally:
            conn.close()

    def test_tags_column_added_to_movie_details(
        self, storage_manager: StorageManager
    ) -> None:
        """Test that tags column is added to movie_details."""
        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(movie_details)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "tags" in columns
            assert "description" in columns
        finally:
            conn.close()

    def test_tags_column_added_to_tv_show_details(
        self, storage_manager: StorageManager
    ) -> None:
        """Test that tags column is added to tv_show_details."""
        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tv_show_details)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "tags" in columns
            assert "description" in columns
        finally:
            conn.close()

    def test_tags_column_added_to_video_game_details(
        self, storage_manager: StorageManager
    ) -> None:
        """Test that tags column is added to video_game_details."""
        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(video_game_details)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "tags" in columns
            assert "description" in columns
        finally:
            conn.close()


class TestEnrichmentStatusMethods:
    """Tests for enrichment status storage methods."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

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

    def test_get_enrichment_status_not_found(
        self, storage_manager: StorageManager
    ) -> None:
        """Test getting enrichment status for non-existent item."""
        status = storage_manager.get_enrichment_status(999)
        assert status is None

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
        """Test marking an item's enrichment as failed."""
        db_id = storage_manager.save_content_item(sample_item)

        storage_manager.mark_enrichment_failed(db_id, "API rate limit exceeded")

        status = storage_manager.get_enrichment_status(db_id)
        assert status is not None
        assert status["enrichment_error"] == "API rate limit exceeded"
        assert status["needs_enrichment"] is False

    def test_mark_item_needs_enrichment(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        """Test marking an item as needing enrichment."""
        db_id = storage_manager.save_content_item(sample_item)

        storage_manager.mark_item_needs_enrichment(db_id)

        status = storage_manager.get_enrichment_status(db_id)
        assert status is not None
        assert status["needs_enrichment"] is True

    def test_reset_enrichment_status_all(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        """Test resetting all enrichment status."""
        db_id = storage_manager.save_content_item(sample_item)
        storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")

        # Reset all
        count = storage_manager.reset_enrichment_status()

        assert count == 1
        status = storage_manager.get_enrichment_status(db_id)
        assert status["needs_enrichment"] is True

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
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

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

    def test_get_items_needs_enrichment_true(
        self, storage_manager: StorageManager
    ) -> None:
        """Test getting items with needs_enrichment=TRUE."""
        item = ContentItem(
            id="test1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = storage_manager.save_content_item(item)
        storage_manager.mark_item_needs_enrichment(db_id)

        items = storage_manager.get_items_needing_enrichment()

        assert len(items) == 1

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

    def test_get_items_with_limit(self, storage_manager: StorageManager) -> None:
        """Test limiting the number of items returned."""
        for index in range(5):
            item = ContentItem(
                id=f"movie{index}",
                title=f"Movie {index}",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
            storage_manager.save_content_item(item)

        items = storage_manager.get_items_needing_enrichment(limit=3)

        assert len(items) == 3


class TestCountItemsNeedingEnrichment:
    """Tests for counting items needing enrichment.

    The count and get methods share ``_build_enrichment_query`` so the same
    WHERE clause drives both. These tests verify count parity with the get
    path and exercise the COUNT(*) -> int cursor branch.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

    def test_count_empty_database_returns_zero(
        self, storage_manager: StorageManager
    ) -> None:
        """Empty database returns 0 (covers the `if row else 0` guard)."""
        assert storage_manager.count_items_needing_enrichment() == 0

    def test_count_includes_new_and_pending_items(
        self, storage_manager: StorageManager
    ) -> None:
        """Items without an enrichment_status row are counted (NULL branch)."""
        for index in range(4):
            storage_manager.save_content_item(
                ContentItem(
                    id=f"movie{index}",
                    title=f"Movie {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )

        assert storage_manager.count_items_needing_enrichment() == 4

    def test_count_includes_items_marked_needs_enrichment(
        self, storage_manager: StorageManager
    ) -> None:
        """Items with `needs_enrichment = 1` are counted (the second WHERE branch).

        The shared `_build_enrichment_query` matches both `content_item_id IS NULL`
        and `needs_enrichment = 1`. Without an explicit test here, the count path
        only exercises the NULL branch even though the get path covers both.
        """
        # Save the item, then mark needs_enrichment so it has a status row
        # with needs_enrichment = 1 (no longer NULL on the JOIN side).
        item = ContentItem(
            id="movie1",
            title="Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = storage_manager.save_content_item(item)
        storage_manager.mark_item_needs_enrichment(db_id)

        assert (
            storage_manager.get_enrichment_status(db_id) is not None
        ), "expected enrichment_status row to exist for the needs_enrichment=1 branch"
        assert storage_manager.count_items_needing_enrichment() == 1

    def test_count_excludes_enriched_items(
        self, storage_manager: StorageManager
    ) -> None:
        """Items already enriched are not counted."""
        item = ContentItem(
            id="movie1",
            title="Movie 1",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = storage_manager.save_content_item(item)
        storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")

        assert storage_manager.count_items_needing_enrichment() == 0

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
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

    def test_stats_empty_database(self, storage_manager: StorageManager) -> None:
        """Test stats on empty database."""
        stats = storage_manager.get_enrichment_stats()

        assert stats["total"] == 0
        assert stats["enriched"] == 0
        assert stats["pending"] == 0
        assert stats["failed"] == 0

    def test_stats_with_items(self, storage_manager: StorageManager) -> None:
        """Test stats with various item states."""
        # Create items with different states
        items = [
            ContentItem(
                id=f"item{i}",
                title=f"Item {i}",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
            for i in range(5)
        ]

        db_ids = [storage_manager.save_content_item(item) for item in items]

        # Item 0: enriched (high quality)
        storage_manager.mark_enrichment_complete(db_ids[0], "tmdb", "high")
        # Item 1: enriched (medium quality)
        storage_manager.mark_enrichment_complete(db_ids[1], "tmdb", "medium")
        # Item 2: failed
        storage_manager.mark_enrichment_failed(db_ids[2], "Not found")
        # Item 3: needs enrichment (explicitly marked)
        storage_manager.mark_item_needs_enrichment(db_ids[3])
        # Item 4: no status (untracked)

        stats = storage_manager.get_enrichment_stats()

        assert stats["total"] == 5
        assert stats["enriched"] == 2
        assert stats["failed"] == 1
        # pending includes both explicitly marked and untracked items
        assert stats["pending"] == 2  # 1 marked + 1 untracked
        assert stats["by_provider"]["tmdb"] == 2
        assert stats["by_quality"]["high"] == 1
        assert stats["by_quality"]["medium"] == 1


class TestTagsAndDescriptionStorage:
    """Tests for storing and retrieving tags and description."""

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        """Create a storage manager with a temporary database."""
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

    def test_save_and_load_book_with_tags(
        self, storage_manager: StorageManager
    ) -> None:
        """Test saving and loading a book with tags and description."""
        item = ContentItem(
            id="book1",
            title="Test Book",
            author="Test Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Fiction", "Mystery"],
                "tags": ["bestseller", "award-winning"],
                "description": "A thrilling mystery novel.",
            },
        )

        db_id = storage_manager.save_content_item(item)
        loaded = storage_manager.get_content_item(db_id)

        assert loaded is not None
        assert loaded.metadata.get("tags") == ["bestseller", "award-winning"]
        assert loaded.metadata.get("description") == "A thrilling mystery novel."

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

    def test_save_and_load_tv_show_with_tags(
        self, storage_manager: StorageManager
    ) -> None:
        """Test saving and loading a TV show with tags and description."""
        item = ContentItem(
            id="show1",
            title="Test Show",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Drama"],
                "tags": ["critically-acclaimed"],
                "description": "A gripping drama series.",
            },
        )

        db_id = storage_manager.save_content_item(item)
        loaded = storage_manager.get_content_item(db_id)

        assert loaded is not None
        assert loaded.metadata.get("tags") == ["critically-acclaimed"]
        assert loaded.metadata.get("description") == "A gripping drama series."

    def test_save_and_load_game_with_tags(
        self, storage_manager: StorageManager
    ) -> None:
        """Test saving and loading a video game with tags and description."""
        item = ContentItem(
            id="game1",
            title="Test Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["RPG", "Action"],
                "tags": ["open-world", "single-player"],
                "description": "An immersive RPG experience.",
                "developer": "Test Studio",
            },
        )

        db_id = storage_manager.save_content_item(item)
        loaded = storage_manager.get_content_item(db_id)

        assert loaded is not None
        assert loaded.metadata.get("tags") == ["open-world", "single-player"]
        assert loaded.metadata.get("description") == "An immersive RPG experience."

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

    def test_get_content_item_db_id_not_found(
        self, storage_manager: StorageManager
    ) -> None:
        """Test getting database ID for non-existent item."""
        found_id = storage_manager.get_content_item_db_id(
            "nonexistent", ContentType.MOVIE
        )

        assert found_id is None


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
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

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

    def test_no_filter_returns_all_with_enriched_flag(
        self, storage_manager: StorageManager
    ) -> None:
        """No filter returns every item, each carrying its enriched flag."""
        pending = self._save(storage_manager, "pending")
        enriched = self._save(storage_manager, "enriched")
        storage_manager.mark_enrichment_complete(enriched, "tmdb", "high")

        items = storage_manager.get_content_items()
        flags = {item.db_id: item.enriched for item in items}

        assert flags == {pending: False, enriched: True}

    def test_single_item_carries_enriched_flag(
        self, storage_manager: StorageManager
    ) -> None:
        """get_content_item exposes the enriched flag for the detail view."""
        db_id = self._save(storage_manager, "movie")

        assert storage_manager.get_content_item(db_id).enriched is False

        storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")
        assert storage_manager.get_content_item(db_id).enriched is True

    def test_failed_item_is_not_enriched(self, storage_manager: StorageManager) -> None:
        """A failed item carries enriched=False even with a provider row.

        Isolates the ``enrichment_error IS NOT NULL`` exclusion: the row has a
        recorded error, so it must not count as enriched regardless of having
        an enrichment_status row at all.
        """
        db_id = self._save(storage_manager, "failed")
        storage_manager.mark_enrichment_failed(db_id, "boom")

        assert storage_manager.get_content_item(db_id).enriched is False
        enriched = storage_manager.get_content_items(enrichment="enriched")
        assert db_id not in {item.db_id for item in enriched}

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
        return StorageManager(sqlite_path=db_path, ai_enabled=False)

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

    def test_manual_edit_drops_item_from_not_enriched_filter(
        self, storage_manager: StorageManager
    ) -> None:
        """After a manual edit the item appears under enriched, not not_enriched."""
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        storage_manager.update_item_from_ui(
            db_id=db_id,
            status="unread",
            description="Manually written.",
        )

        not_enriched = storage_manager.get_content_items(enrichment="not_enriched")
        enriched = storage_manager.get_content_items(enrichment="enriched")
        assert [item.db_id for item in not_enriched] == []
        assert [item.db_id for item in enriched] == [db_id]

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

    def test_unknown_content_type_raises_and_does_not_mark_enriched(
        self, storage_manager: StorageManager
    ) -> None:
        """An unknown content_type raises and never marks the item enriched.

        Guards the bug where _write_manual_metadata silently returned for an
        unrecognized content_type while the caller went on to mark the item
        enriched with no metadata written. The stored content_type is forced to
        a bogus value to drive the defensive branch.
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        conn = storage_manager.sqlite_db._get_connection()
        try:
            conn.execute(
                "UPDATE content_items SET content_type = ? WHERE id = ?",
                ("bogus_type", db_id),
            )
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(ValueError, match="Unknown content_type"):
            storage_manager.update_item_from_ui(
                db_id=db_id, status="unread", genres=["Drama"]
            )

        # No enrichment row was written, so the item is not marked enriched.
        assert storage_manager.get_enrichment_status(db_id) is None

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

    def test_manual_edit_overrides_prior_auto_enrichment(
        self, storage_manager: StorageManager
    ) -> None:
        """A previously auto-enriched item flips to the manual provider.

        After tmdb enrichment, a manual edit updates the enrichment row to
        provider "manual" and the item still appears under enriched.
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        storage_manager.mark_enrichment_complete(db_id, "tmdb", "high")
        assert storage_manager.get_enrichment_status(db_id)["enrichment_provider"] == (
            "tmdb"
        )

        storage_manager.update_item_from_ui(
            db_id=db_id, status="unread", genres=["Drama"]
        )

        status = storage_manager.get_enrichment_status(db_id)
        assert status["enrichment_provider"] == "manual"
        enriched = storage_manager.get_content_items(enrichment="enriched")
        assert db_id in {item.db_id for item in enriched}

    def test_manual_edit_inserts_detail_row_for_book(
        self, storage_manager: StorageManager
    ) -> None:
        """Manual edit creates the detail row when the item has none yet.

        Exercises the INSERT branch of _write_manual_metadata for a non-movie
        type: the book_details row is removed to simulate an item without a
        detail row, then a manual edit must insert it.
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book1",
                title="Test Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        conn = storage_manager.sqlite_db._get_connection()
        try:
            conn.execute("DELETE FROM book_details WHERE content_item_id = ?", (db_id,))
            conn.commit()
        finally:
            conn.close()

        storage_manager.update_item_from_ui(
            db_id=db_id,
            status="unread",
            genres=["Fantasy"],
            tags=["epic"],
            description="A sweeping tale.",
        )

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres") == ["Fantasy"]
        assert loaded.metadata.get("tags") == ["epic"]
        assert loaded.metadata.get("description") == "A sweeping tale."
        assert loaded.enriched is True
