from __future__ import annotations

import json
from typing import Any

from src.storage.schema import (
    SourceConfigDict,
    SourceConfigRow,
    delete_source_config,
    get_source_config,
    list_source_configs,
    set_source_config_enabled,
    set_source_config_schedule,
    upsert_source_config,
)
from src.storage.sqlite_db import SQLiteDB


def _to_dict(row: SourceConfigRow) -> SourceConfigDict:
    return SourceConfigDict(
        source_id=row["source_id"],
        plugin=row["plugin"],
        config=json.loads(row["config_json"]),
        enabled=bool(row["enabled"]),
        sync_interval=row["sync_interval"],
        migrated_at=row["migrated_at"],
        updated_at=row["updated_at"],
    )


class SourceConfigStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def get(self, user_id: int, source_id: str) -> SourceConfigDict | None:
        with self._sqlite_db.connection() as conn:
            row = get_source_config(conn, user_id, source_id)
        return _to_dict(row) if row else None

    def upsert(
        self,
        user_id: int,
        source_id: str,
        plugin: str,
        config: dict[str, Any],
        enabled: bool = True,
    ) -> None:
        """Sensitive values must NOT be passed in *config* — they live in the
        encrypted ``credentials`` table.
        """
        config_json = json.dumps(config, sort_keys=True)
        with self._sqlite_db.connection() as conn:
            upsert_source_config(conn, user_id, source_id, plugin, config_json, enabled)

    def set_enabled(self, user_id: int, source_id: str, enabled: bool) -> bool:
        with self._sqlite_db.connection() as conn:
            return set_source_config_enabled(conn, user_id, source_id, enabled)

    def set_schedule(self, user_id: int, source_id: str, interval: str) -> bool:
        with self._sqlite_db.connection() as conn:
            return set_source_config_schedule(conn, user_id, source_id, interval)

    def delete(self, user_id: int, source_id: str) -> bool:
        with self._sqlite_db.connection() as conn:
            return delete_source_config(conn, user_id, source_id)

    def list(self, user_id: int) -> list[SourceConfigDict]:
        with self._sqlite_db.connection() as conn:
            rows = list_source_configs(conn, user_id)
        return [_to_dict(row) for row in rows]
