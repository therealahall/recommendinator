from __future__ import annotations

from src.storage.schema import (
    PreferenceProfileRow,
    get_preference_profile,
    save_preference_profile,
)
from src.storage.sqlite_db import SQLiteDB


class ProfileStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def get(self, user_id: int) -> PreferenceProfileRow | None:
        with self._sqlite_db.connection() as conn:
            return get_preference_profile(conn, user_id)

    def save(self, user_id: int, profile_json: str) -> int:
        with self._sqlite_db.connection() as conn:
            return save_preference_profile(conn, user_id, profile_json)
