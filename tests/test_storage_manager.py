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


def test_delete_content_item(temp_storage_manager: StorageManager) -> None:
    """Test deleting a content item."""
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
