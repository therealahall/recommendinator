"""The ``user_ui_settings`` table: how the app looks, per user."""

from __future__ import annotations

from src.storage.schema import UnknownUserError, get_user_theme, set_user_theme
from src.storage.sqlite_db import SQLiteDB


class UiSettingsStore:
    """A user's interface settings. ``StorageManager.ui_settings``."""

    def __init__(self, sqlite_db: SQLiteDB) -> None:
        self._sqlite_db = sqlite_db

    def get_theme(self, user_id: int) -> str:
        """Return the user's theme id, empty when they have not picked one.

        One indexed read and no session, so a response rendered before the
        browser has signed in can still carry the stored theme.
        """
        with self._sqlite_db.connection() as conn:
            return get_user_theme(conn, user_id)

    def set_theme(self, user_id: int, theme_id: str) -> None:
        """Store the user's theme id.

        Raises:
            UnknownUserError: nobody carries *user_id*.
        """
        with self._sqlite_db.connection() as conn:
            if not set_user_theme(conn, user_id, theme_id):
                raise UnknownUserError(f"No user with id {user_id}.")
