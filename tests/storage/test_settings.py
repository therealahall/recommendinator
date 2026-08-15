"""Tests for the DB-backed settings store (schema functions + StorageManager).

The ``settings`` table persists global/system configuration as namespaced
key -> JSON-encoded value pairs. The table holds only leaves a user explicitly
set; boot never writes to it. When a leaf is present it wins over the YAML/const
layers during config assembly.
"""

from pathlib import Path

import pytest

from src.storage.manager import StorageManager


class TestStorageManagerSettings:
    """Tests for StorageManager settings methods (JSON value serialization)."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_set_and_get_nested_structure(self, storage: StorageManager) -> None:
        """Nested dicts and lists round-trip exactly."""
        value = {
            "scorer_weights": {"genre_match": 2.0, "tags": [1, 2, 3]},
            "default_count": 5,
        }
        storage.set_setting("recommendations", value)

        assert storage.get_setting("recommendations") == value

    def test_stored_null_reads_as_none_but_appears_in_list(
        self, storage: StorageManager
    ) -> None:
        """A stored ``None`` reads back as None yet still appears in list_settings.

        get_setting returns None for both a missing key and a stored null, so
        the null-valued leaf is only observable via list_settings — which the
        config overlay relies on to apply a deliberately-nulled leaf.
        """
        storage.set_setting("logging.file", None)

        assert storage.get_setting("logging.file") is None
        assert storage.list_settings() == {"logging.file": None}

    def test_falsy_values_round_trip(self, storage: StorageManager) -> None:
        """Falsy scalars (False, 0) round-trip without being lost or coerced."""
        storage.set_setting("enrichment.enabled", False)
        storage.set_setting("sync.max_workers", 0)

        assert storage.get_setting("enrichment.enabled") is False
        assert storage.get_setting("sync.max_workers") == 0

    def test_delete_setting_falls_back_to_missing(
        self, storage: StorageManager
    ) -> None:
        """delete_setting removes an override so the leaf reads back as unset."""
        storage.set_setting("web.port", 65000)

        storage.delete_setting("web.port")

        assert storage.get_setting("web.port") is None
        assert storage.list_settings() == {}
