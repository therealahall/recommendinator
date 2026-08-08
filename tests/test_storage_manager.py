"""Tests for unified storage manager."""

import logging
import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.storage.manager import StorageManager, stored_embedding_key
from src.storage.schema import create_user, get_user_by_id, update_user_settings
from src.storage.vector_db import VectorDB


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


def test_delete_removes_db_keyed_embedding_regression(tmp_path: Path) -> None:
    """Regression: deleting an id-less item deletes the embedding it wrote.

    Bug reported: ChromaDB accumulated embeddings for items SQLite no longer
    held, and only ever for items imported without an external id.
    Root cause: the embedding was written under the synthetic ``db_<db_id>``
    key while ``delete_content_item`` deleted ``item.id``, which is ``None``
    for exactly those items, so nothing was deleted.
    Fix: the write and the delete derive the key the same way.
    """
    manager = StorageManager(tmp_path / "delete.db", ai_enabled=False)
    manager.vector_db = Mock(spec=VectorDB)
    item = ContentItem(
        id=None,
        title="CSV Import",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = manager.save_content_item(item, embedding=[0.1, 0.2, 0.3])
    written_key = manager.vector_db.add_embedding.call_args.args[0]

    assert manager.delete_content_item(db_id) is True

    assert written_key == f"db_{db_id}"
    manager.vector_db.delete_embedding.assert_called_once_with(written_key)


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


def test_merge_user_preference_config_edits_the_stored_config(
    temp_storage_manager: StorageManager,
) -> None:
    """``apply`` is handed what was stored, and its edit round-trips."""
    temp_storage_manager.save_user_preference_config(
        user_id=1,
        preference_config=UserPreferenceConfig(
            scorer_weights={"genre_match": 2.0}, series_in_order=False
        ),
    )

    def add_a_weight(existing: UserPreferenceConfig) -> None:
        assert existing.scorer_weights == {"genre_match": 2.0}
        existing.scorer_weights["tag_overlap"] = 0.5

    merged = temp_storage_manager.merge_user_preference_config(1, add_a_weight)

    assert merged.scorer_weights == {"genre_match": 2.0, "tag_overlap": 0.5}
    assert merged.series_in_order is False
    assert temp_storage_manager.get_user_preference_config(user_id=1) == merged


def test_merge_preference_config_does_not_clobber_other_settings(
    temp_storage_manager: StorageManager,
) -> None:
    """Merging preference_config preserves other keys in users.settings."""
    conn = temp_storage_manager.sqlite_db._get_connection()
    try:
        update_user_settings(conn, 1, {"theme": "dark"})
    finally:
        conn.close()

    def set_a_weight(existing: UserPreferenceConfig) -> None:
        existing.scorer_weights["genre_match"] = 2.5

    temp_storage_manager.merge_user_preference_config(1, set_a_weight)

    conn = temp_storage_manager.sqlite_db._get_connection()
    try:
        user = get_user_by_id(conn, 1)
        assert user is not None
        assert user["settings"]["theme"] == "dark"
        assert user["settings"]["preference_config"]["scorer_weights"] == {
            "genre_match": 2.5
        }
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
        and "uv sync --locked --extra ai" in message
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

    def test_repeat_external_id_merges_in_place_per_field(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """Pins the semantics left behind when the conflict module was deleted.

        ``src/ingestion/conflict.py`` and its 523-line test file were removed on
        this branch. That is behaviour-preserving because the default
        ``LAST_WRITE_WINS`` strategy returned the incoming item unchanged and was
        the only reachable branch — the real merge has always lived in
        ``SQLiteDB.save_content_item``. But nothing at the manager layer pinned
        the surviving contract afterwards: the nearest test asserts
        ``count_items() <= 16 and >= 1``, far too loose to catch a regression to
        insert-always, or to a blanket overwrite.

        And the contract is NOT "last write wins" — it is field-level:

        * rating/review — set once, never overwritten (the user's own judgement
          must survive a re-sync from a source that does not know it)
        * status — forward-only (a stale export cannot un-complete an item)
        * ``None`` incoming values never clobber stored data

        Held at the manager layer, not only in ``tests/test_sqlite_db.py``,
        because ``save_content_item`` is the seam the deleted strategy sat on.
        """
        db_id = temp_storage_manager.save_content_item(
            ContentItem(
                id="steam_440",
                title="Team Fortress 2",
                source="steam",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=3.0,
                review="Held up better than I expected.",
            )
        )

        # A later sync of the same external id, carrying a different rating, an
        # earlier status, and no review.
        second_db_id = temp_storage_manager.save_content_item(
            ContentItem(
                id="steam_440",
                title="Team Fortress 2",
                source="steam",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                rating=5.0,
                review=None,
            )
        )

        assert second_db_id == db_id
        assert temp_storage_manager.count_items() == 1

        stored = temp_storage_manager.get_content_item(db_id)
        assert stored is not None
        # Set-once: the first rating and review survive the re-sync.
        assert stored.rating == 3.0
        assert stored.review == "Held up better than I expected."
        # Forward-only: COMPLETED is not walked back to UNREAD.
        assert stored.status == ConsumptionStatus.COMPLETED

    def test_status_advances_forward_on_a_repeat_save(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """The other half of forward-only: real progress IS applied.

        Without this, "forward-only" could be implemented as "never change
        status" and the test above would still pass.
        """
        db_id = temp_storage_manager.save_content_item(
            ContentItem(
                id="steam_440",
                title="Team Fortress 2",
                source="steam",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            )
        )

        temp_storage_manager.save_content_item(
            ContentItem(
                id="steam_440",
                title="Team Fortress 2",
                source="steam",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.COMPLETED,
                rating=4.0,
            )
        )

        stored = temp_storage_manager.get_content_item(db_id)
        assert stored is not None
        assert stored.status == ConsumptionStatus.COMPLETED
        # Rating was unset before, so set-once applies it now.
        assert stored.rating == 4.0

    def test_busy_timeout_pragma_set_on_connections(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """PRAGMA busy_timeout is applied so writers block instead of raising."""
        with temp_storage_manager.connection() as conn:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row is not None
        assert row[0] >= 5000


class TestGetSignalItemsRegression:
    """Bug reported: ignored/unrated items leaked into the recommendation signal.

    Bug reported: every recommendation surface re-derived "completed, rated,
    and not ignored" (or forgot to), so ignored items and completed-but-unrated
    items contaminated preferences, scoring, similarity, and explanations.
    Root cause: there was no single accessor for the taste-signal set; call
    sites used ``get_completed_items`` with default filters.
    Fix: ``StorageManager.get_signal_items`` centralizes the three-part filter
    (completed AND rated AND not ignored) so every consumer routes through it.
    """

    @staticmethod
    def _book(item_id, title, status, rating):
        return ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=status,
            rating=rating,
        )

    def test_signal_items_keeps_only_completed_rated_non_ignored_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """get_signal_items returns completed, rated, non-ignored items only."""
        keep = self._book("keep", "Signal Book", ConsumptionStatus.COMPLETED, rating=5)
        unrated = self._book(
            "unrated", "Unrated Book", ConsumptionStatus.COMPLETED, rating=None
        )
        unread = self._book("unread", "Backlog Book", ConsumptionStatus.UNREAD, None)
        ignored = self._book(
            "ignored", "Ignored Book", ConsumptionStatus.COMPLETED, rating=5
        )

        temp_storage_manager.save_content_item(keep)
        temp_storage_manager.save_content_item(unrated)
        temp_storage_manager.save_content_item(unread)
        ignored_db_id = temp_storage_manager.save_content_item(ignored)
        temp_storage_manager.set_item_ignored(ignored_db_id, True)

        titles = {item.title for item in temp_storage_manager.get_signal_items()}
        assert titles == {"Signal Book"}

    def test_signal_items_excludes_item_that_is_both_ignored_and_unrated_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """An item that is both ignored and unrated is excluded."""
        keep = self._book("keep", "Signal Book", ConsumptionStatus.COMPLETED, 4)
        ignored_unrated = self._book(
            "combo", "Ignored Unrated", ConsumptionStatus.COMPLETED, rating=None
        )
        temp_storage_manager.save_content_item(keep)
        combo_db_id = temp_storage_manager.save_content_item(ignored_unrated)
        temp_storage_manager.set_item_ignored(combo_db_id, True)

        titles = {item.title for item in temp_storage_manager.get_signal_items()}
        assert titles == {"Signal Book"}

    def test_signal_items_empty_when_all_unrated_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """The accessor returns [] when every completed item is unrated."""
        for index in range(2):
            temp_storage_manager.save_content_item(
                self._book(
                    f"u{index}",
                    f"Unrated {index}",
                    ConsumptionStatus.COMPLETED,
                    rating=None,
                )
            )

        assert temp_storage_manager.get_signal_items() == []

    def test_signal_items_empty_when_all_ignored_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """The accessor returns [] when every completed item is ignored."""
        for index in range(2):
            db_id = temp_storage_manager.save_content_item(
                self._book(
                    f"i{index}",
                    f"Ignored {index}",
                    ConsumptionStatus.COMPLETED,
                    rating=5,
                )
            )
            temp_storage_manager.set_item_ignored(db_id, True)

        assert temp_storage_manager.get_signal_items() == []

    def test_signal_items_forwards_params_to_get_completed_items_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """user_id, content_type, limit, and include_ignored are forwarded intact.

        Guards against a user_id/content_type delegation swap and confirms the
        ignore filter is expressed via the SQL flag.
        """
        captured: dict[str, object] = {}

        def fake_completed(**kwargs: object) -> list[ContentItem]:
            captured.update(kwargs)
            return []

        temp_storage_manager.get_completed_items = fake_completed  # type: ignore[method-assign]

        result = temp_storage_manager.get_signal_items(
            user_id=7, content_type=ContentType.MOVIE, limit=3
        )

        assert result == []
        assert captured["user_id"] == 7
        assert captured["content_type"] == ContentType.MOVIE
        assert captured["limit"] == 3
        assert captured["include_ignored"] is False


class TestGetConsumptionItemsRegression:
    """Bug reported: finishing six books without rating them caused no fatigue.

    Bug reported: the genre-fatigue variety ladder is documented as reacting to
    what the user recently completed, but a user who finished six fantasy
    novels and rated none of them got no fantasy fatigue at all — the slider
    moved and nothing happened.
    Root cause: the ladder was fed ``get_signal_items``, the taste-learning set
    (completed AND rated AND not ignored), so an unrated completion never
    claimed a rung.
    Fix: ``get_consumption_items`` returns what the user has consumed (not
    ignored, rating irrelevant) and feeds the ladder, while ``get_signal_items``
    keeps its rating filter for preference learning. It inherits
    ``get_completed_items``' status set, so in-progress items come with it and
    the ladder does its own completion-event narrowing.
    """

    @staticmethod
    def _book(item_id, title, status, rating):
        return ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=status,
            rating=rating,
        )

    @classmethod
    def _seed_library(cls, manager: StorageManager) -> None:
        manager.save_content_item(
            cls._book("rated", "Rated Book", ConsumptionStatus.COMPLETED, rating=5)
        )
        manager.save_content_item(
            cls._book("unrated", "Unrated Book", ConsumptionStatus.COMPLETED, None)
        )
        manager.save_content_item(
            cls._book(
                "reading", "In Progress", ConsumptionStatus.CURRENTLY_CONSUMING, None
            )
        )
        manager.save_content_item(
            cls._book("unread", "Backlog Book", ConsumptionStatus.UNREAD, None)
        )
        ignored_db_id = manager.save_content_item(
            cls._book("ignored", "Ignored Book", ConsumptionStatus.COMPLETED, rating=5)
        )
        manager.set_item_ignored(ignored_db_id, True)

    def test_consumption_items_keep_unrated_and_in_progress_items_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """Unrated and in-progress items count; ignored and unread do not.

        In-progress items ride along from ``get_completed_items``, which is the
        accessor's status set. The ladder narrows them itself, so a caller that
        wants finished-only must not read this one as if it already had.
        """
        self._seed_library(temp_storage_manager)

        titles = {item.title for item in temp_storage_manager.get_consumption_items()}
        assert titles == {"Rated Book", "Unrated Book", "In Progress"}

    def test_consumption_items_exclude_ignored_unrated_completion_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """Ignoring something keeps it off the ladder even though it was finished."""
        db_id = temp_storage_manager.save_content_item(
            self._book("combo", "Ignored Unrated", ConsumptionStatus.COMPLETED, None)
        )
        temp_storage_manager.set_item_ignored(db_id, True)

        assert temp_storage_manager.get_consumption_items() == []

    def test_consumption_items_keep_an_unrated_show_mid_run_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """An unrated show with a finished season survives the accessor.

        A finished season of an ongoing show is a completion event for the
        variety ladder, so the show has to reach the ladder before its seasons
        can be read. Under the signal set an unrated one never did.
        """
        temp_storage_manager.save_content_item(
            ContentItem(
                id="show",
                title="Ongoing Show",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                rating=None,
                metadata={"genre": "Drama", "seasons_watched": [1]},
            )
        )

        consumption = temp_storage_manager.get_consumption_items()
        assert [item.title for item in consumption] == ["Ongoing Show"]
        assert consumption[0].metadata["seasons_watched"] == [1]
        assert temp_storage_manager.get_signal_items() == []

    def test_consumption_set_is_wider_than_the_signal_set_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """The two collections are provably distinct: only rating separates them.

        Preference learning still excludes unrated completions — widening the
        signal accessor instead of adding this one would have corrupted it.
        """
        self._seed_library(temp_storage_manager)

        consumption = {
            item.title for item in temp_storage_manager.get_consumption_items()
        }
        signal = {item.title for item in temp_storage_manager.get_signal_items()}

        assert signal == {"Rated Book"}
        # Proper subset: widening must add to the signal set, never trade an
        # item away, which "signal == {...} and consumption - signal == {...}"
        # alone would not catch.
        assert signal < consumption
        assert consumption - signal == {"Unrated Book", "In Progress"}

    def test_consumption_items_forward_params_to_get_completed_items_regression(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """user_id, content_type and limit are forwarded, ignored items excluded."""
        captured: dict[str, object] = {}

        def fake_completed(**kwargs: object) -> list[ContentItem]:
            captured.update(kwargs)
            return []

        temp_storage_manager.get_completed_items = fake_completed  # type: ignore[method-assign]

        result = temp_storage_manager.get_consumption_items(
            user_id=7, content_type=ContentType.MOVIE, limit=3
        )

        assert result == []
        assert captured["user_id"] == 7
        assert captured["content_type"] == ContentType.MOVIE
        assert captured["limit"] == 3
        assert captured["include_ignored"] is False


class TestGetItemsByEmbeddingKeys:
    """Resolving vector-DB keys back to the items they were stored for."""

    @staticmethod
    def _save(
        manager: StorageManager,
        item_id: str | None,
        title: str,
        ignored: bool = False,
        content_type: ContentType = ContentType.BOOK,
    ) -> int:
        db_id = manager.save_content_item(
            ContentItem(
                id=item_id,
                title=title,
                content_type=content_type,
                status=ConsumptionStatus.UNREAD,
            )
        )
        if ignored:
            manager.set_item_ignored(db_id, True)
        return db_id

    def test_returns_empty_for_no_keys(
        self, temp_storage_manager: StorageManager
    ) -> None:
        assert temp_storage_manager.get_items_by_embedding_keys([]) == {}

    def test_resolves_both_key_forms(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """An external id and a synthetic ``db_`` key resolve in one call."""
        self._save(temp_storage_manager, "ext-1", "With Id")
        db_id = self._save(temp_storage_manager, None, "Without Id")

        found = temp_storage_manager.get_items_by_embedding_keys(
            ["ext-1", f"db_{db_id}"]
        )

        assert {key: item.title for key, item in found.items()} == {
            "ext-1": "With Id",
            f"db_{db_id}": "Without Id",
        }

    def test_omits_keys_naming_nothing(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """A stale embedding key is absent rather than raising."""
        self._save(temp_storage_manager, "ext-1", "With Id")

        found = temp_storage_manager.get_items_by_embedding_keys(
            ["ext-1", "gone", "db_9999"]
        )

        assert list(found) == ["ext-1"]

    def test_omits_a_db_key_only_int_would_read_as_a_row(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """Only a plain run of decimal digits names a row.

        ``int()`` also accepts a sign, surrounding whitespace and PEP 515
        underscores, none of which ``stored_embedding_key`` ever writes. Each
        spelling below carries the digits of a row that really exists, and
        none of them may resolve to it.
        """
        db_id = self._save(temp_storage_manager, None, "Without Id")

        found = temp_storage_manager.get_items_by_embedding_keys(
            [f"db_+{db_id}", f"db_ {db_id}", f"db_{db_id} "]
        )

        assert found == {}

    def test_an_external_id_shaped_like_the_synthetic_form_wins(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """A real external id beats the synthetic reading of the same key."""
        self._save(temp_storage_manager, "db_1", "Awkwardly Named")

        found = temp_storage_manager.get_items_by_embedding_keys(["db_1"])

        assert found["db_1"].title == "Awkwardly Named"

    def test_excludes_ignored_items_when_asked(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """include_ignored=False drops an ignored item under either key form."""
        self._save(temp_storage_manager, "ext-1", "Ignored With Id", ignored=True)
        db_id = self._save(
            temp_storage_manager, None, "Ignored Without Id", ignored=True
        )

        found = temp_storage_manager.get_items_by_embedding_keys(
            ["ext-1", f"db_{db_id}"], include_ignored=False
        )

        assert found == {}

    def test_does_not_cross_users(self, temp_storage_manager: StorageManager) -> None:
        """Another user's row is not resolved for the current user's search."""
        with temp_storage_manager.connection() as conn:
            second_user_id = create_user(conn, "second")
        db_id = temp_storage_manager.save_content_item(
            ContentItem(
                user_id=second_user_id,
                id=None,
                title="Someone Else's Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=second_user_id,
        )

        assert temp_storage_manager.get_items_by_embedding_keys([f"db_{db_id}"]) == {}

    def test_resolves_an_external_id_within_the_requested_type(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """One external id naming two types resolves to the type asked for."""
        self._save(temp_storage_manager, "shared", "Dune")
        self._save(
            temp_storage_manager, "shared", "Dune", content_type=ContentType.MOVIE
        )

        found = temp_storage_manager.get_items_by_embedding_keys(
            ["shared"], content_type=ContentType.MOVIE
        )

        assert found["shared"].content_type == ContentType.MOVIE

    def test_omits_a_db_key_of_another_type(
        self, temp_storage_manager: StorageManager
    ) -> None:
        """A ``db_`` key naming a movie is absent from a book lookup."""
        db_id = self._save(
            temp_storage_manager, None, "CSV Film", content_type=ContentType.MOVIE
        )

        found = temp_storage_manager.get_items_by_embedding_keys(
            [f"db_{db_id}"], content_type=ContentType.BOOK
        )

        assert found == {}


class TestStoredEmbeddingKey:
    """The vector-DB key an item's own embedding is stored under."""

    @staticmethod
    def _item(item_id: str | None, db_id: int | None) -> ContentItem:
        return ContentItem(
            id=item_id,
            db_id=db_id,
            title="Hyperion",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

    def test_uses_the_external_id_when_there_is_one(self) -> None:
        assert stored_embedding_key(self._item("ext-1", 7)) == "ext-1"

    def test_falls_back_to_the_database_row(self) -> None:
        assert stored_embedding_key(self._item(None, 7)) == "db_7"

    def test_is_none_when_the_item_has_neither(self) -> None:
        """An item with no row and no id has no embedding of its own."""
        assert stored_embedding_key(self._item(None, None)) is None
