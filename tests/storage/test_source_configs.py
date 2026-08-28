from pathlib import Path

import pytest

from src.storage.manager import StorageManager


class TestStorageManagerSourceConfigs:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_upsert_and_get_round_trips_dict(self, storage: StorageManager) -> None:
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
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, enabled=True)
        storage.sources.set_enabled(1, "steam", enabled=False)

        result = storage.sources.get(1, "steam")
        assert result is not None
        assert result["enabled"] is False
        assert result["config"] == {"a": 1}

    def test_delete_source_config(self, storage: StorageManager) -> None:
        storage.sources.upsert(1, "steam", "steam", {}, True)
        storage.sources.delete(1, "steam")

        assert storage.sources.get(1, "steam") is None

    def test_schedule_survives_reopening_the_database(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, True)

        assert storage.sources.set_schedule(1, "steam", "6h") is True

        reopened = StorageManager(sqlite_path=tmp_path / "test.db").sources.get(
            1, "steam"
        )
        assert reopened is not None
        assert reopened["sync_interval"] == "6h"

    def test_upsert_keeps_the_stored_schedule(self, storage: StorageManager) -> None:
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
        assert storage.sources.set_schedule(1, "steam", "6h") is False

    def test_list_source_configs_returns_dicts(self, storage: StorageManager) -> None:
        storage.sources.upsert(1, "steam", "steam", {"a": 1}, True)
        storage.sources.upsert(1, "books", "goodreads", {"path": "x"}, False)

        result = storage.sources.list(1)

        by_id = {row["source_id"]: row for row in result}
        assert by_id["steam"]["config"] == {"a": 1}
        assert by_id["books"]["config"] == {"path": "x"}
        assert by_id["steam"]["enabled"] is True
        assert by_id["books"]["enabled"] is False
