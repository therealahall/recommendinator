from __future__ import annotations

from src.storage.schema import UnknownUserError, get_user_theme, set_user_theme
from src.storage.sqlite_db import SQLiteDB


class UiSettingsStore:
    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def get_theme(self, user_id: int) -> str:
        """One indexed read and no session, so a response rendered before the
        browser has signed in can still carry the stored theme.
        """
        with self._sqlite_db.connection() as conn:
            return get_user_theme(conn, user_id)

    def set_theme(self, user_id: int, theme_id: str) -> None:
        with self._sqlite_db.connection() as conn:
            if not set_user_theme(conn, user_id, theme_id):
                raise UnknownUserError(f"No user with id {user_id}.")
