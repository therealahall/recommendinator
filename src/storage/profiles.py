"""The ``preference_profiles`` table: the generated taste profile per user."""

from __future__ import annotations

from src.storage.schema import (
    PreferenceProfileRow,
    get_preference_profile,
    save_preference_profile,
)
from src.storage.sqlite_db import SQLiteDB


class ProfileStore:
    """The user's one preference profile. ``StorageManager.profiles``."""

    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def get(self, user_id: int) -> PreferenceProfileRow | None:
        """Return the stored profile record, or ``None`` if none was generated."""
        with self._sqlite_db.connection() as conn:
            return get_preference_profile(conn, user_id)

    def save(self, user_id: int, profile_json: str) -> int:
        """Store the user's profile, replacing any earlier one, and return its id."""
        with self._sqlite_db.connection() as conn:
            return save_preference_profile(conn, user_id, profile_json)
