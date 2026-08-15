"""Tests for unified storage manager."""

import threading
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.storage.manager import StorageManager
from src.storage.schema import get_user_by_id, update_user_settings


@pytest.fixture
def temp_storage_manager(tmp_path: Path) -> StorageManager:
    """Create a temporary storage manager for testing."""
    return StorageManager(tmp_path / "test.db")


def test_save_content_item(temp_storage_manager: StorageManager) -> None:
    """Test saving a content item."""
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


# ---------------------------------------------------------------------------
# User preference config persistence tests (Phase 5)
# ---------------------------------------------------------------------------


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
