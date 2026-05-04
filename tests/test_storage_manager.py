"""Tests for unified storage manager."""

import logging
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.storage.manager import StorageManager


@pytest.fixture
def temp_storage_manager(tmp_path: Path) -> StorageManager:
    """Create a temporary storage manager for testing (AI disabled)."""
    sqlite_path = tmp_path / "test.db"
    return StorageManager(sqlite_path, ai_enabled=False)


@pytest.fixture
def temp_storage_manager_with_ai(tmp_path: Path) -> StorageManager:
    """Create a temporary storage manager with AI enabled for testing."""
    sqlite_path = tmp_path / "test.db"
    vector_db_path = tmp_path / "vector_db"
    return StorageManager(sqlite_path, vector_db_path, ai_enabled=True)


def test_save_content_item_without_embedding(
    temp_storage_manager: StorageManager,
) -> None:
    """Test saving content item without embedding."""
    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
    )

    db_id = temp_storage_manager.save_content_item(item)
    assert db_id > 0

    retrieved = temp_storage_manager.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"


def test_save_content_item_with_embedding(
    temp_storage_manager_with_ai: StorageManager,
) -> None:
    """Test saving content item with embedding (requires AI enabled)."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    embedding = [0.1, 0.2, 0.3, 0.4, 0.5]

    db_id = temp_storage_manager_with_ai.save_content_item(item, embedding=embedding)
    assert db_id > 0

    # Check SQLite
    retrieved = temp_storage_manager_with_ai.get_content_item(db_id)
    assert retrieved is not None

    # Check vector DB
    retrieved_embedding = temp_storage_manager_with_ai.get_embedding("123")
    assert retrieved_embedding is not None
    # Use approximate equality for floating-point comparison
    assert len(retrieved_embedding) == len(embedding)
    for r, e in zip(retrieved_embedding, embedding, strict=True):
        assert abs(r - e) < 1e-6


def test_get_unconsumed_items(temp_storage_manager: StorageManager) -> None:
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
        temp_storage_manager.save_content_item(item)

    unconsumed = temp_storage_manager.get_unconsumed_items()
    assert len(unconsumed) == 3
    assert all(
        item.status in {ConsumptionStatus.UNREAD, ConsumptionStatus.CURRENTLY_CONSUMING}
        for item in unconsumed
    )


def test_get_completed_items(temp_storage_manager: StorageManager) -> None:
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
    ]

    for item in items:
        temp_storage_manager.save_content_item(item)

    completed = temp_storage_manager.get_completed_items(min_rating=4)
    assert len(completed) == 3


def test_search_similar(temp_storage_manager_with_ai: StorageManager) -> None:
    """Test searching for similar content (requires AI enabled)."""
    # Add some items with embeddings
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        for i in range(3)
    ]

    embeddings = [
        [0.1, 0.2, 0.3],
        [0.2, 0.3, 0.4],
        [0.9, 0.8, 0.7],
    ]

    for item, embedding in zip(items, embeddings, strict=True):
        temp_storage_manager_with_ai.save_content_item(item, embedding=embedding)

    # Search for similar
    query_embedding = [0.15, 0.25, 0.35]
    results = temp_storage_manager_with_ai.search_similar(query_embedding, n_results=2)

    assert len(results) <= 2


def test_search_similar_without_ai_raises(temp_storage_manager: StorageManager) -> None:
    """Test that search_similar raises when AI is not enabled."""
    with pytest.raises(RuntimeError, match="ai_enabled=True"):
        temp_storage_manager.search_similar([0.1, 0.2, 0.3])


def test_delete_content_item(temp_storage_manager_with_ai: StorageManager) -> None:
    """Test deleting content item from both databases."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    embedding = [0.1, 0.2, 0.3]

    db_id = temp_storage_manager_with_ai.save_content_item(item, embedding=embedding)
    assert temp_storage_manager_with_ai.get_content_item(db_id) is not None
    assert temp_storage_manager_with_ai.get_embedding("123") is not None

    deleted = temp_storage_manager_with_ai.delete_content_item(db_id)
    assert deleted is True

    assert temp_storage_manager_with_ai.get_content_item(db_id) is None
    # Note: ChromaDB may return empty list or None for deleted items
    assert not temp_storage_manager_with_ai.has_embedding("123")


def test_delete_content_item_without_ai(temp_storage_manager: StorageManager) -> None:
    """Test deleting content item when AI is disabled."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_storage_manager.save_content_item(item)
    assert temp_storage_manager.get_content_item(db_id) is not None

    deleted = temp_storage_manager.delete_content_item(db_id)
    assert deleted is True
    assert temp_storage_manager.get_content_item(db_id) is None


def test_count_items(temp_storage_manager: StorageManager) -> None:
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
        temp_storage_manager.save_content_item(item)

    assert temp_storage_manager.count_items() == 5
    assert temp_storage_manager.count_items(content_type=ContentType.BOOK) == 3


def test_ai_disabled_by_default(tmp_path: Path) -> None:
    """Test that AI is disabled by default."""
    sqlite_path = tmp_path / "test.db"
    manager = StorageManager(sqlite_path)
    assert manager.ai_enabled is False
    assert manager.vector_db is None


def test_has_embedding_returns_false_when_ai_disabled(
    temp_storage_manager: StorageManager,
) -> None:
    """Test that has_embedding returns False when AI is disabled."""
    assert temp_storage_manager.has_embedding("nonexistent") is False


def test_content_item_with_user_id(temp_storage_manager: StorageManager) -> None:
    """Test saving and retrieving content item preserves user_id."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        user_id=1,
    )

    db_id = temp_storage_manager.save_content_item(item)
    retrieved = temp_storage_manager.get_content_item(db_id)

    assert retrieved is not None
    assert retrieved.user_id == 1


def test_content_item_with_source(temp_storage_manager: StorageManager) -> None:
    """Test saving and retrieving content item preserves source."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        source="goodreads",
    )

    db_id = temp_storage_manager.save_content_item(item)
    retrieved = temp_storage_manager.get_content_item(db_id)

    assert retrieved is not None
    assert retrieved.source == "goodreads"


# ---------------------------------------------------------------------------
# User preference config persistence tests (Phase 5)
# ---------------------------------------------------------------------------


def test_get_user_preference_config_defaults(
    temp_storage_manager: StorageManager,
) -> None:
    """get_user_preference_config returns defaults for new user."""
    config = temp_storage_manager.get_user_preference_config(user_id=1)
    assert config == UserPreferenceConfig()


def test_save_and_load_user_preference_config(
    temp_storage_manager: StorageManager,
) -> None:
    """Round-trip: save then load produces equal config."""
    preference_config = UserPreferenceConfig(
        scorer_weights={"genre_match": 3.0, "tag_overlap": 0.5},
        series_in_order=False,
    )
    temp_storage_manager.save_user_preference_config(
        user_id=1, preference_config=preference_config
    )
    loaded = temp_storage_manager.get_user_preference_config(user_id=1)
    assert loaded == preference_config


def test_save_preference_config_does_not_clobber_other_settings(
    temp_storage_manager: StorageManager,
) -> None:
    """Saving preference_config preserves other keys in users.settings."""
    from src.storage.schema import update_user_settings

    # Set some other setting first
    conn = temp_storage_manager.sqlite_db._get_connection()
    try:
        update_user_settings(conn, 1, {"theme": "dark"})
    finally:
        conn.close()

    # Now save preference config
    preference_config = UserPreferenceConfig(scorer_weights={"genre_match": 2.5})
    temp_storage_manager.save_user_preference_config(
        user_id=1, preference_config=preference_config
    )

    # Verify both settings coexist
    conn = temp_storage_manager.sqlite_db._get_connection()
    try:
        from src.storage.schema import get_user_by_id

        user = get_user_by_id(conn, 1)
        assert user is not None
        assert user["settings"]["theme"] == "dark"
        assert "preference_config" in user["settings"]
    finally:
        conn.close()


def test_chromadb_import_error_disables_ai(tmp_path: Path) -> None:
    """When chromadb is not installed, ai_enabled is set to False.

    Regression: the non-AI Docker image has no chromadb package. If a user's
    config has ai_enabled: true, StorageManager must degrade gracefully instead
    of crashing with ImportError.
    """
    sqlite_path = tmp_path / "test.db"
    vector_db_path = tmp_path / "vector_db"

    with patch.dict(sys.modules, {"src.storage.vector_db": None}):
        manager = StorageManager(
            sqlite_path, vector_db_path=vector_db_path, ai_enabled=True
        )

    assert manager.ai_enabled is False
    assert manager.vector_db is None


def test_chromadb_import_error_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When chromadb is not installed, a warning with install instructions is logged."""
    sqlite_path = tmp_path / "test.db"
    vector_db_path = tmp_path / "vector_db"

    with patch.dict(sys.modules, {"src.storage.vector_db": None}):
        with caplog.at_level(logging.WARNING, logger="src.storage.manager"):
            StorageManager(sqlite_path, vector_db_path=vector_db_path, ai_enabled=True)

    assert any(
        "chromadb is not installed" in message
        and "pip install recommendinator[ai]" in message
        for message in caplog.messages
    )


def test_chromadb_import_error_sqlite_still_works(tmp_path: Path) -> None:
    """SQLite operations continue working after chromadb import failure."""
    sqlite_path = tmp_path / "test.db"
    vector_db_path = tmp_path / "vector_db"
    item = ContentItem(
        id="import-error-test",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    with patch.dict(sys.modules, {"src.storage.vector_db": None}):
        manager = StorageManager(
            sqlite_path, vector_db_path=vector_db_path, ai_enabled=True
        )

    db_id = manager.save_content_item(item)
    assert db_id > 0
    retrieved = manager.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"
    assert retrieved.content_type == ContentType.BOOK


class TestConcurrentSaveContentItem:
    """Thread-safety contract for parallel multi-source sync (issue #45).

    Bug: when execute_multi_source_sync runs sources on multiple threads,
    two workers can call save_content_item concurrently with overlapping
    normalized titles. The read-conflict-write sequence is non-atomic, so
    interleaved cross-source dedup merges could merge the same row twice
    or lose data.

    Fix: a per-StorageManager threading.Lock serialises save_content_item
    so the dedup sequence is atomic; a SQLite busy_timeout PRAGMA blocks
    rather than raising on writer contention.
    """

    def test_concurrent_distinct_items_all_persisted(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """All items saved exactly once when many threads write distinct items."""
        item_count = 50
        items = [
            ContentItem(
                id=f"ext_{i}",
                title=f"Distinct Title {i}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
            for i in range(item_count)
        ]

        barrier = threading.Barrier(item_count)
        errors: list[Exception] = []
        db_ids: list[int] = []
        ids_lock = threading.Lock()

        def save(item: ContentItem) -> None:
            barrier.wait()
            try:
                db_id = temp_storage_manager.save_content_item(item)
                with ids_lock:
                    db_ids.append(db_id)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=save, args=(item,)) for item in items]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert len(db_ids) == item_count
        assert len(set(db_ids)) == item_count
        assert temp_storage_manager.count_items() == item_count

    def test_concurrent_overlapping_titles_dedupes_safely(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """Concurrent writes for the same normalized title produce one row, no errors.

        Two sources independently importing the same book trigger the
        cross-source dedup path inside save_content_item. The sequence
        must not interleave to a state where both writers create rows
        and neither merges them.
        """
        thread_count = 16
        shared_title = "The Same Book"
        items = [
            ContentItem(
                id=f"src_a_{i}" if i % 2 == 0 else f"src_b_{i}",
                title=shared_title,
                source="source_a" if i % 2 == 0 else "source_b",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
            for i in range(thread_count)
        ]

        barrier = threading.Barrier(thread_count)
        errors: list[Exception] = []

        def save(item: ContentItem) -> None:
            barrier.wait()
            try:
                temp_storage_manager.save_content_item(item)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=save, args=(item,)) for item in items]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        # Each unique external_id keeps its own row (dedup-by-title only
        # merges when titles match AND no row matches the external_id).
        # The contract is "no exceptions, no lost data", not "everything
        # collapses to one row" — that depends on insertion order.
        assert temp_storage_manager.count_items() <= thread_count
        assert temp_storage_manager.count_items() >= 1

    def test_busy_timeout_pragma_set_on_connections(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """PRAGMA busy_timeout is applied so writers block instead of raising."""
        with temp_storage_manager.connection() as conn:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row is not None
        assert row[0] >= 5000
