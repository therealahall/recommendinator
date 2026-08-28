from pathlib import Path

import pytest

from src.storage.manager import StorageManager


class TestStorageManagerSettings:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_set_and_get_nested_structure(self, storage: StorageManager) -> None:
        value = {
            "scorer_weights": {"genre_match": 2.0, "tags": [1, 2, 3]},
            "default_count": 5,
        }
        storage.settings.set("recommendations", value)

        assert storage.settings.get("recommendations") == value

    def test_stored_null_reads_as_none_but_appears_in_list(
        self, storage: StorageManager
    ) -> None:
        storage.settings.set("logging.file", None)

        assert storage.settings.get("logging.file") is None
        assert storage.settings.list() == {"logging.file": None}

    def test_falsy_values_round_trip(self, storage: StorageManager) -> None:
        storage.settings.set("enrichment.enabled", False)
        storage.settings.set("sync.max_workers", 0)

        assert storage.settings.get("enrichment.enabled") is False
        assert storage.settings.get("sync.max_workers") == 0

    def test_delete_setting_falls_back_to_missing(
        self, storage: StorageManager
    ) -> None:
        storage.settings.set("web.port", 65000)

        storage.settings.delete("web.port")

        assert storage.settings.get("web.port") is None
        assert storage.settings.list() == {}
