"""Tests for the goodreads -> goodreads_csv source and plugin migrations."""

import logging
from pathlib import Path

import pytest

from src.ingestion.registry import get_registry
from src.ingestion.sources.goodreads_csv.goodreads_csv import GoodreadsCsvPlugin
from src.storage.manager import StorageManager
from src.storage.source_migration import (
    migrate_source_config_plugins,
    migrate_source_labels,
)


def _insert_user(storage: StorageManager, user_id: int) -> None:
    """Insert a users row so content_items FK constraints are satisfied."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (user_id, f"user{user_id}"),
        )
        conn.commit()


def _insert_item(storage: StorageManager, source: str, user_id: int = 1) -> None:
    """Insert a minimal content item with the given source label."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, source) "
            "VALUES (?, 'Some Title', 'book', 'completed', ?)",
            (user_id, source),
        )
        conn.commit()


def _insert_source_config(
    storage: StorageManager, source_id: str, plugin: str, user_id: int = 1
) -> None:
    """Insert a migrated source_configs row with the given plugin name."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO source_configs (user_id, source_id, plugin, config_json) "
            "VALUES (?, ?, ?, '{}')",
            (user_id, source_id, plugin),
        )
        conn.commit()


def _sources(storage: StorageManager) -> list[str]:
    """Return every stored ``source`` value ordered by row id."""
    with storage.connection() as conn:
        cursor = conn.execute("SELECT source FROM content_items ORDER BY id")
        return [row[0] for row in cursor.fetchall()]


def _plugins(storage: StorageManager) -> list[tuple[int, str, str]]:
    """Return every source_configs ``(user_id, source_id, plugin)`` by source_id."""
    with storage.connection() as conn:
        cursor = conn.execute(
            "SELECT user_id, source_id, plugin FROM source_configs "
            "ORDER BY user_id, source_id"
        )
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]


class TestMigrateSourceLabels:
    """Tests for migrate_source_labels."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_relabels_goodreads_source(self, storage: StorageManager) -> None:
        """An item with source='goodreads' is relabeled to 'goodreads_csv'."""
        _insert_item(storage, "goodreads")

        migrate_source_labels(storage)

        assert _sources(storage) == ["goodreads_csv"]

    def test_logs_count_on_migration(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The migration logs at INFO with the number of rows it updated."""
        _insert_item(storage, "goodreads")
        _insert_item(storage, "goodreads")

        with caplog.at_level(logging.INFO):
            migrate_source_labels(storage)

        assert "Relabeled 2 content item(s)" in caplog.text

    def test_idempotent_second_run_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Running twice yields the same result and the second run reports nothing."""
        _insert_item(storage, "goodreads")

        migrate_source_labels(storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_labels(storage)

        assert _sources(storage) == ["goodreads_csv"]
        # Second run matches no rows, so nothing is logged.
        assert "Relabeled" not in caplog.text

    def test_other_sources_untouched(self, storage: StorageManager) -> None:
        """Items with other source labels are left exactly as-is."""
        _insert_item(storage, "steam")
        _insert_item(storage, "mybooks")
        _insert_item(storage, "goodreads")
        # An arbitrary user-chosen config-block key must not be rewritten.
        _insert_item(storage, "goodreads_rss")

        migrate_source_labels(storage)

        assert _sources(storage) == [
            "steam",
            "mybooks",
            "goodreads_csv",
            "goodreads_rss",
        ]

    def test_scopes_to_requested_user(self, storage: StorageManager) -> None:
        """Only the target user's goodreads rows are relabeled.

        The migration is user-scoped (default user 1). A goodreads row owned by
        another user must be left untouched when migrating user 1, and relabeled
        only when that user is explicitly migrated.
        """
        _insert_user(storage, 2)
        _insert_item(storage, "goodreads", user_id=1)
        _insert_item(storage, "goodreads", user_id=2)

        migrate_source_labels(storage)  # defaults to user_id=1

        assert _sources(storage) == ["goodreads_csv", "goodreads"]

        migrate_source_labels(storage, user_id=2)

        assert _sources(storage) == ["goodreads_csv", "goodreads_csv"]

    def test_empty_db_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An empty database with no matching rows completes as a silent no-op."""
        with caplog.at_level(logging.INFO):
            migrate_source_labels(storage)

        assert _sources(storage) == []
        assert "Relabeled" not in caplog.text


class TestMigrateSourceConfigPlugins:
    """Tests for migrate_source_config_plugins."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_relabels_goodreads_plugin(self, storage: StorageManager) -> None:
        """A source_configs row with plugin='goodreads' becomes 'goodreads_csv'."""
        _insert_source_config(storage, "my_books", "goodreads")

        migrate_source_config_plugins(storage)

        assert _plugins(storage) == [(1, "my_books", "goodreads_csv")]

    def test_logs_count_on_migration(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The migration logs at INFO with the number of rows it updated."""
        _insert_source_config(storage, "books_a", "goodreads")
        _insert_source_config(storage, "books_b", "goodreads")

        with caplog.at_level(logging.INFO):
            migrate_source_config_plugins(storage)

        assert "Relabeled 2 source config(s)" in caplog.text

    def test_idempotent_second_run_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Running twice yields the same result and the second run logs nothing."""
        _insert_source_config(storage, "my_books", "goodreads")

        migrate_source_config_plugins(storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_config_plugins(storage)

        assert _plugins(storage) == [(1, "my_books", "goodreads_csv")]
        assert "Relabeled" not in caplog.text

    def test_other_plugins_untouched(self, storage: StorageManager) -> None:
        """Rows with other plugin names are left exactly as-is."""
        _insert_source_config(storage, "games", "steam")
        _insert_source_config(storage, "books", "goodreads")
        _insert_source_config(storage, "shelves", "goodreads_rss")

        migrate_source_config_plugins(storage)

        assert _plugins(storage) == [
            (1, "books", "goodreads_csv"),
            (1, "games", "steam"),
            (1, "shelves", "goodreads_rss"),
        ]

    def test_scopes_to_requested_user(self, storage: StorageManager) -> None:
        """Only the target user's goodreads rows are relabeled.

        A goodreads plugin row owned by another user must be left untouched when
        migrating user 1, and relabeled only when that user is migrated.
        """
        _insert_user(storage, 2)
        _insert_source_config(storage, "books", "goodreads", user_id=1)
        _insert_source_config(storage, "books", "goodreads", user_id=2)

        migrate_source_config_plugins(storage)  # defaults to user_id=1

        assert _plugins(storage) == [
            (1, "books", "goodreads_csv"),
            (2, "books", "goodreads"),
        ]

        migrate_source_config_plugins(storage, user_id=2)

        assert _plugins(storage) == [
            (1, "books", "goodreads_csv"),
            (2, "books", "goodreads_csv"),
        ]

    def test_empty_db_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An empty database with no matching rows completes as a silent no-op."""
        with caplog.at_level(logging.INFO):
            migrate_source_config_plugins(storage)

        assert _plugins(storage) == []
        assert "Relabeled" not in caplog.text


class TestMigratedPluginResolvesThroughRegistry:
    """End-to-end: a migrated source_config must resolve through the registry.

    The rename is a hard cutover — the old ``goodreads`` plugin name no longer
    exists, so a stored ``plugin='goodreads'`` row would resolve to ``None`` and
    silently stop syncing. These tests prove the migration rewrites the stored
    plugin name to the value the registry actually serves, closing the loop.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_old_goodreads_plugin_name_does_not_resolve(self) -> None:
        """The historical ``goodreads`` plugin name no longer resolves.

        Proves the hard cutover: only ``goodreads_csv`` exists in the registry,
        so an un-migrated ``plugin='goodreads'`` row would vanish.
        """
        registry = get_registry()

        assert registry.get_plugin("goodreads") is None
        assert isinstance(registry.get_plugin("goodreads_csv"), GoodreadsCsvPlugin)

    def test_migrated_plugin_value_resolves_to_csv_plugin(
        self, storage: StorageManager
    ) -> None:
        """After migration the stored plugin value resolves to GoodreadsCsvPlugin.

        Reads the plugin name back out of the DB exactly as the sync path would
        and looks it up in the registry, proving the round-trip: a previously
        DB-configured Goodreads source keeps working after the rename instead of
        silently vanishing.
        """
        _insert_source_config(storage, "my_books", "goodreads")

        migrate_source_config_plugins(storage)

        stored_plugin = _plugins(storage)[0][2]
        assert stored_plugin == "goodreads_csv"
        resolved = get_registry().get_plugin(stored_plugin)
        assert isinstance(resolved, GoodreadsCsvPlugin)

    def test_combined_source_and_config_relabel_consistently(
        self, storage: StorageManager
    ) -> None:
        """A user with BOTH a goodreads source_config AND goodreads items.

        Startup runs both migrations. Afterwards the source_config plugin must
        resolve through the registry AND every content item must be re-attributed
        to the same ``goodreads_csv`` label, so the source and its items stay
        consistent rather than drifting apart.
        """
        _insert_source_config(storage, "goodreads", "goodreads")
        _insert_item(storage, "goodreads")
        _insert_item(storage, "goodreads")

        # Mirrors the startup order in cli/main.py, app.py and state.py.
        migrate_source_labels(storage)
        migrate_source_config_plugins(storage)

        assert _sources(storage) == ["goodreads_csv", "goodreads_csv"]
        assert _plugins(storage) == [(1, "goodreads", "goodreads_csv")]
        resolved = get_registry().get_plugin(_plugins(storage)[0][2])
        assert isinstance(resolved, GoodreadsCsvPlugin)
