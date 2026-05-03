"""Tests for source_configs CRUD operations and StorageManager integration.

The ``source_configs`` table stores the *non-sensitive* per-source config
overrides that move to the database after a user clicks "Migrate to DB" in
the web UI. Sensitive fields keep going through the existing encrypted
``credentials`` table; this table holds the rest of the config (paths,
content_type, plugin-specific scalars/lists).
"""

import sqlite3
from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.storage.schema import (
    create_schema,
    delete_source_config,
    get_source_config,
    list_source_configs,
    set_source_config_enabled,
    upsert_source_config,
)


class TestSourceConfigCRUD:
    """Tests for low-level source_configs schema functions."""

    @pytest.fixture()
    def conn(self) -> sqlite3.Connection:
        """Create an in-memory DB with schema."""
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        return connection

    def test_upsert_and_get_source_config(self, conn: sqlite3.Connection) -> None:
        """Round-trip: upsert a source config then retrieve it."""
        upsert_source_config(
            conn,
            user_id=1,
            source_id="steam",
            plugin="steam",
            config_json='{"vanity_url": "myname", "min_playtime_minutes": 0}',
            enabled=True,
        )

        result = get_source_config(conn, user_id=1, source_id="steam")
        assert result is not None
        assert result["source_id"] == "steam"
        assert result["plugin"] == "steam"
        assert result["config_json"] == (
            '{"vanity_url": "myname", "min_playtime_minutes": 0}'
        )
        assert result["enabled"] == 1
        assert result["migrated_at"] is not None

    def test_get_source_config_returns_none_when_missing(
        self, conn: sqlite3.Connection
    ) -> None:
        """Returns None for a source that hasn't been migrated."""
        assert get_source_config(conn, user_id=1, source_id="missing") is None

    def test_upsert_overwrites_existing(self, conn: sqlite3.Connection) -> None:
        """Upserting the same source_id updates fields but PRESERVES migrated_at.

        ``migrated_at`` is the immutable first-migration timestamp. Only
        ``updated_at`` advances on subsequent upserts.
        """
        upsert_source_config(conn, 1, "steam", "steam", "{}", True)
        first = get_source_config(conn, 1, "steam")
        assert first is not None
        original_migrated_at = first["migrated_at"]

        upsert_source_config(conn, 1, "steam", "steam", '{"vanity_url": "new"}', False)

        result = get_source_config(conn, 1, "steam")
        assert result is not None
        assert result["config_json"] == '{"vanity_url": "new"}'
        assert result["enabled"] == 0
        assert result["migrated_at"] == original_migrated_at

    def test_delete_source_config_returns_false_when_missing(
        self, conn: sqlite3.Connection
    ) -> None:
        """Delete on a missing row returns False instead of raising."""
        assert delete_source_config(conn, 1, "never_existed") is False

    def test_set_source_config_enabled_toggles(self, conn: sqlite3.Connection) -> None:
        """Enabled toggle flips without touching config_json."""
        upsert_source_config(conn, 1, "steam", "steam", '{"x": 1}', True)
        set_source_config_enabled(conn, 1, "steam", enabled=False)

        result = get_source_config(conn, 1, "steam")
        assert result is not None
        assert result["enabled"] == 0
        assert result["config_json"] == '{"x": 1}'

    def test_set_source_config_enabled_no_row(self, conn: sqlite3.Connection) -> None:
        """Toggling enabled on a non-migrated source is a no-op (no row created)."""
        set_source_config_enabled(conn, 1, "missing", enabled=True)
        assert get_source_config(conn, 1, "missing") is None

    def test_delete_source_config(self, conn: sqlite3.Connection) -> None:
        """Delete removes the row entirely."""
        upsert_source_config(conn, 1, "steam", "steam", "{}", True)
        delete_source_config(conn, 1, "steam")

        assert get_source_config(conn, 1, "steam") is None

    def test_list_source_configs_returns_all_for_user(
        self, conn: sqlite3.Connection
    ) -> None:
        """List returns every migrated source for the user."""
        upsert_source_config(conn, 1, "steam", "steam", "{}", True)
        upsert_source_config(conn, 1, "books", "goodreads", "{}", False)

        result = list_source_configs(conn, user_id=1)

        ids = {row["source_id"] for row in result}
        assert ids == {"steam", "books"}

    def test_list_source_configs_empty_when_no_rows(
        self, conn: sqlite3.Connection
    ) -> None:
        """Returns empty list when user has no migrated sources."""
        assert list_source_configs(conn, user_id=1) == []

    def test_source_configs_scoped_by_user(self, conn: sqlite3.Connection) -> None:
        """Different users have separate source_configs namespaces."""
        conn.cursor().execute("INSERT INTO users (id, username) VALUES (2, 'user2')")
        conn.commit()

        upsert_source_config(conn, 1, "steam", "steam", '{"u": 1}', True)
        upsert_source_config(conn, 2, "steam", "steam", '{"u": 2}', True)

        u1 = get_source_config(conn, 1, "steam")
        u2 = get_source_config(conn, 2, "steam")
        assert u1 is not None and u1["config_json"] == '{"u": 1}'
        assert u2 is not None and u2["config_json"] == '{"u": 2}'


class TestStorageManagerSourceConfigs:
    """Tests for StorageManager source_config methods (dict serialization)."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_upsert_and_get_round_trips_dict(self, storage: StorageManager) -> None:
        """StorageManager accepts/returns dicts and handles JSON serialization."""
        storage.upsert_source_config(
            user_id=1,
            source_id="steam",
            plugin="steam",
            config={"vanity_url": "myname", "min_playtime_minutes": 0},
            enabled=True,
        )

        result = storage.get_source_config(1, "steam")
        assert result is not None
        assert result["source_id"] == "steam"
        assert result["plugin"] == "steam"
        assert result["config"] == {"vanity_url": "myname", "min_playtime_minutes": 0}
        assert result["enabled"] is True
        assert result["migrated_at"] is not None

    def test_get_source_config_returns_none_when_missing(
        self, storage: StorageManager
    ) -> None:
        """Returns None for a source that hasn't been migrated."""
        assert storage.get_source_config(1, "missing") is None

    def test_set_enabled_toggles_without_touching_config(
        self, storage: StorageManager
    ) -> None:
        """set_source_config_enabled flips the bool without altering config dict."""
        storage.upsert_source_config(1, "steam", "steam", {"a": 1}, enabled=True)
        storage.set_source_config_enabled(1, "steam", enabled=False)

        result = storage.get_source_config(1, "steam")
        assert result is not None
        assert result["enabled"] is False
        assert result["config"] == {"a": 1}

    def test_delete_source_config(self, storage: StorageManager) -> None:
        """Delete removes the migration entirely."""
        storage.upsert_source_config(1, "steam", "steam", {}, True)
        storage.delete_source_config(1, "steam")

        assert storage.get_source_config(1, "steam") is None

    def test_list_source_configs_returns_dicts(self, storage: StorageManager) -> None:
        """List returns parsed dicts for every migrated source."""
        storage.upsert_source_config(1, "steam", "steam", {"a": 1}, True)
        storage.upsert_source_config(1, "books", "goodreads", {"path": "x"}, False)

        result = storage.list_source_configs(1)

        by_id = {row["source_id"]: row for row in result}
        assert by_id["steam"]["config"] == {"a": 1}
        assert by_id["books"]["config"] == {"path": "x"}
        assert by_id["steam"]["enabled"] is True
        assert by_id["books"]["enabled"] is False
