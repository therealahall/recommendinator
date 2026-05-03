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
