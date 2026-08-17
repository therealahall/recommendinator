"""Tests for source_configs CRUD operations and StorageManager integration.

The ``source_configs`` table stores the *non-sensitive* per-source config
overrides that move to the database after a user clicks "Migrate to DB" in
the web UI. Sensitive fields keep going through the existing encrypted
``credentials`` table; this table holds the rest of the config (paths,
content_type, plugin-specific scalars/lists).
"""

from pathlib import Path

import pytest

from src.storage.manager import StorageManager


class TestStorageManagerSourceConfigs:
    """Tests for StorageManager source_config methods (dict serialization)."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_upsert_and_get_round_trips_dict(self, storage: StorageManager) -> None:
        """StorageManager accepts/returns dicts and handles JSON serialization."""
        storage.sources.upsert(
            user_id=1,
            source_id="steam",
            plugin="steam",
            config={"vanity_url": "myname", "min_playtime_minutes": 0},
            enabled=True,
        )

        result = storage.sources.get(1, "steam")
        assert result is not None
        assert result["source_id"] == "steam"
        assert result["plugin"] == "steam"
        assert result["config"] == {"vanity_url": "myname", "min_playtime_minutes": 0}
        assert result["enabled"] is True
        assert result["migrated_at"] is not None

    def test_set_enabled_toggles_without_touching_config(
        self, storage: StorageManager
    ) -> None:
        """set_source_config_enabled flips the bool without altering config dict."""
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, enabled=True)
        storage.sources.set_enabled(1, "steam", enabled=False)

        result = storage.sources.get(1, "steam")
        assert result is not None
        assert result["enabled"] is False
        assert result["config"] == {"a": 1}

    def test_delete_source_config(self, storage: StorageManager) -> None:
        """Delete removes the migration entirely."""
        storage.sources.upsert(1, "steam", "steam", {}, True)
        storage.sources.delete(1, "steam")

        assert storage.sources.get(1, "steam") is None

    def test_schedule_survives_reopening_the_database(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """A cadence is stored, not held in memory, and starts unset.

        Clearing it back to ``None`` is asserted too: a write that declines to
        store one would leave a cadence the user removed running for good.
        """
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, True)
        before = storage.sources.get(1, "steam")
        assert before is not None
        assert before["sync_interval"] is None

        assert storage.sources.set_schedule(1, "steam", "6h") is True

        reopened = StorageManager(sqlite_path=tmp_path / "test.db").sources.get(
            1, "steam"
        )
        assert reopened is not None
        assert reopened["sync_interval"] == "6h"

        assert storage.sources.set_schedule(1, "steam", None) is True
        cleared = storage.sources.get(1, "steam")
        assert cleared is not None
        assert cleared["sync_interval"] is None

    def test_upsert_keeps_the_stored_schedule(self, storage: StorageManager) -> None:
        """Editing a config carries no cadence, and must not clear the stored one.

        A clobbering upsert would stop automatic syncing every time a user
        changed a path.
        """
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, True)
        storage.sources.set_schedule(1, "steam", "6h")

        storage.sources.upsert(1, "steam", "steam", {"a": 2}, True)

        result = storage.sources.get(1, "steam")
        assert result is not None
        assert result["config"] == {"a": 2}
        assert result["sync_interval"] == "6h"

    def test_set_schedule_reports_an_unmigrated_source(
        self, storage: StorageManager
    ) -> None:
        """No row means no schedule was set, which the caller surfaces as a 404."""
        assert storage.sources.set_schedule(1, "steam", "6h") is False

    def test_list_source_configs_returns_dicts(self, storage: StorageManager) -> None:
        """List returns parsed dicts for every migrated source."""
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, True)
        storage.sources.upsert(1, "books", "goodreads", {"path": "x"}, False)

        result = storage.sources.list(1)

        by_id = {row["source_id"]: row for row in result}
        assert by_id["steam"]["config"] == {"a": 1}
        assert by_id["books"]["config"] == {"path": "x"}
        assert by_id["steam"]["enabled"] is True
        assert by_id["books"]["enabled"] is False
