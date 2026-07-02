"""Tests for the DB-backed settings store (schema functions + StorageManager).

The ``settings`` table persists global/system configuration as namespaced
key -> JSON-encoded value pairs. Once a key exists in the DB it becomes the
source of truth; the YAML config seeds missing keys on boot but never
overwrites a value already present.
"""

import sqlite3
from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.storage.schema import (
    create_schema,
    get_setting,
    has_setting,
    list_settings,
    seed_setting,
    set_setting,
)


class TestSettingsCRUD:
    """Tests for low-level settings schema functions (raw JSON strings)."""

    @pytest.fixture()
    def conn(self) -> sqlite3.Connection:
        """Create an in-memory DB with schema."""
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        return connection

    def test_set_and_get_setting(self, conn: sqlite3.Connection) -> None:
        """Round-trip: set a setting then read its raw JSON back."""
        set_setting(conn, "logging", '{"level": "DEBUG"}')

        assert get_setting(conn, "logging") == '{"level": "DEBUG"}'

    def test_get_setting_returns_none_when_missing(
        self, conn: sqlite3.Connection
    ) -> None:
        """Returns None for a key that has never been written."""
        assert get_setting(conn, "missing") is None

    def test_has_setting(self, conn: sqlite3.Connection) -> None:
        """has_setting reflects row presence without returning the value."""
        assert has_setting(conn, "web") is False
        set_setting(conn, "web", "{}")
        assert has_setting(conn, "web") is True

    def test_set_setting_overwrites(self, conn: sqlite3.Connection) -> None:
        """Writing the same key again replaces the stored value (UPSERT)."""
        set_setting(conn, "web", '{"port": 1}')
        set_setting(conn, "web", '{"port": 2}')

        assert get_setting(conn, "web") == '{"port": 2}'

    def test_list_settings_returns_all(self, conn: sqlite3.Connection) -> None:
        """list_settings returns every stored key -> raw JSON pair."""
        set_setting(conn, "web", '{"port": 1}')
        set_setting(conn, "logging", '{"level": "INFO"}')

        result = list_settings(conn)

        assert result == {"web": '{"port": 1}', "logging": '{"level": "INFO"}'}

    def test_list_settings_empty(self, conn: sqlite3.Connection) -> None:
        """Returns an empty dict when no settings are stored."""
        assert list_settings(conn) == {}

    def test_seed_setting_inserts_when_absent(self, conn: sqlite3.Connection) -> None:
        """seed_setting writes a value for a key that does not exist yet."""
        seed_setting(conn, "web.port", "18473")

        assert get_setting(conn, "web.port") == "18473"

    def test_seed_setting_never_overwrites(self, conn: sqlite3.Connection) -> None:
        """seed_setting leaves an existing value untouched (INSERT OR IGNORE)."""
        set_setting(conn, "web.port", "9999")
        seed_setting(conn, "web.port", "18473")

        assert get_setting(conn, "web.port") == "9999"


class TestStorageManagerSettings:
    """Tests for StorageManager settings methods (JSON value serialization)."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_set_and_get_scalar(self, storage: StorageManager) -> None:
        """A scalar value round-trips through JSON encoding."""
        storage.set_setting("max_workers", 4)

        assert storage.get_setting("max_workers") == 4

    def test_set_and_get_nested_structure(self, storage: StorageManager) -> None:
        """Nested dicts and lists round-trip exactly."""
        value = {
            "scorer_weights": {"genre_match": 2.0, "tags": [1, 2, 3]},
            "default_count": 5,
        }
        storage.set_setting("recommendations", value)

        assert storage.get_setting("recommendations") == value

    def test_get_setting_returns_none_when_missing(
        self, storage: StorageManager
    ) -> None:
        """Returns None for an unset key."""
        assert storage.get_setting("missing") is None

    def test_has_setting(self, storage: StorageManager) -> None:
        """has_setting reports presence."""
        assert storage.has_setting("web") is False
        storage.set_setting("web", {"port": 18473})
        assert storage.has_setting("web") is True

    def test_list_settings_parses_values(self, storage: StorageManager) -> None:
        """list_settings returns decoded Python values for every key."""
        storage.set_setting("web", {"port": 18473})
        storage.set_setting("sync", {"max_workers": 4})

        result = storage.list_settings()

        assert result == {"web": {"port": 18473}, "sync": {"max_workers": 4}}

    def test_set_setting_is_idempotent(self, storage: StorageManager) -> None:
        """Re-setting a key does not create a duplicate row."""
        storage.set_setting("web", {"port": 1})
        storage.set_setting("web", {"port": 2})

        assert storage.list_settings() == {"web": {"port": 2}}

    def test_stored_null_is_present_but_reads_as_none(
        self, storage: StorageManager
    ) -> None:
        """A stored ``None`` is present (has_setting True) yet reads back as None.

        get_setting returns None for both a missing key and a stored null, so
        presence must be checked with has_setting — this asserts both halves.
        """
        storage.set_setting("ollama.conversation_model", None)

        assert storage.has_setting("ollama.conversation_model") is True
        assert storage.get_setting("ollama.conversation_model") is None

    def test_falsy_values_round_trip(self, storage: StorageManager) -> None:
        """Falsy scalars (False, 0) round-trip without being lost or coerced."""
        storage.set_setting("features.ai_enabled", False)
        storage.set_setting("sync.max_workers", 0)

        assert storage.get_setting("features.ai_enabled") is False
        assert storage.get_setting("sync.max_workers") == 0

    def test_seed_setting_never_overwrites(self, storage: StorageManager) -> None:
        """seed_setting inserts a missing key but preserves an existing one."""
        storage.seed_setting("web.port", 18473)
        storage.seed_setting("web.port", 9999)

        assert storage.get_setting("web.port") == 18473
