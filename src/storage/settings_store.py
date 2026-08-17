"""The ``settings`` table: global config leaves, stored JSON-encoded."""

from __future__ import annotations

import json
from typing import Any

from src.storage.schema import delete_setting, get_setting, list_settings, set_setting
from src.storage.sqlite_db import SQLiteDB


class SettingsStore:
    """The DB layer of the global config. ``StorageManager.settings``."""

    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def get(self, key: str) -> Any | None:
        """Return the decoded value for a settings key, or ``None`` if unset.

        Returns ``None`` for BOTH a missing key and a stored null value; a
        stored null is still returned by :meth:`list`.
        """
        with self._sqlite_db.connection() as conn:
            value_json = get_setting(conn, key)
        return json.loads(value_json) if value_json is not None else None

    def set(self, key: str, value: Any) -> None:
        """JSON-encode and persist a settings value (UPSERT)."""
        value_json = json.dumps(value, sort_keys=True)
        with self._sqlite_db.connection() as conn:
            set_setting(conn, key, value_json)

    def list(self) -> dict[str, Any]:
        """Return every stored setting as a key -> decoded value mapping."""
        with self._sqlite_db.connection() as conn:
            raw = list_settings(conn)
        return {key: json.loads(value_json) for key, value_json in raw.items()}

    def delete(self, key: str) -> None:
        """Delete a settings leaf so it falls back to the YAML/const layers.

        No-op when the key is not stored — used to reset a leaf to default.
        """
        with self._sqlite_db.connection() as conn:
            delete_setting(conn, key)
