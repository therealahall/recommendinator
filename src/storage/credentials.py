"""The encrypted ``credentials`` table: every secret a source is configured with."""

from __future__ import annotations

import functools
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from src.storage.schema import (
    credential_row_exists,
    delete_credential,
    delete_credentials_for_source,
    get_credential,
    get_credentials_for_source,
    save_credential,
)
from src.storage.sqlite_db import SQLiteDB

if TYPE_CHECKING:
    from src.storage.encryption import CredentialEncryptor

logger = logging.getLogger(__name__)


def _resolve_key_path(sqlite_path: Path) -> Path:
    """Return the path of the Fernet key file.

    It sits beside the database so both survive a container restart when
    ``data/`` is the persistent volume. An operator who wants the key out of
    the database backup points ``RECOMMENDINATOR_KEY_PATH`` elsewhere.
    """
    env_path = os.environ.get("RECOMMENDINATOR_KEY_PATH")
    if env_path:
        return Path(env_path)
    return Path(sqlite_path).parent / ".credential_key"


class CredentialStore:
    """Per-source credentials, encrypted at rest. ``StorageManager.credentials``."""

    def __init__(
        self, sqlite_db: SQLiteDB, sqlite_path: Path, save_lock: threading.Lock
    ) -> None:
        self._sqlite_db = sqlite_db
        self._key_path = _resolve_key_path(sqlite_path)
        # The manager's lock rather than one of our own: a credential write
        # and a content-item write are the same read-then-write race.
        self._save_lock = save_lock

    @functools.cached_property
    def _encryptor(self) -> CredentialEncryptor:
        """Deferred so building a store touches neither the key file nor
        ``cryptography``, which nothing but a credential access needs."""
        from src.storage.encryption import CredentialEncryptor

        return CredentialEncryptor(self._key_path)

    def get(self, user_id: int, source_id: str, key: str) -> str | None:
        """Return the decrypted value, or ``None`` when none is stored."""
        with self._sqlite_db.connection() as conn:
            encrypted = get_credential(conn, user_id, source_id, key)
        if encrypted is None:
            return None
        from cryptography.fernet import InvalidToken

        try:
            return self._encryptor.decrypt(encrypted)
        except InvalidToken:
            # An unreadable row reads as "not configured", which the caller
            # recovers from by re-authenticating; raising would take a whole
            # sync down. The operator is told why here.
            logger.error(
                "Failed to decrypt credential for source=%s key=%s — "
                "possible key mismatch or data corruption",
                source_id,
                key,
            )
            return None

    def save(self, user_id: int, source_id: str, key: str, value: str) -> None:
        """Encrypt *value* and store it."""
        encrypted = self._encryptor.encrypt(value)
        with self._save_lock, self._sqlite_db.connection() as conn:
            save_credential(conn, user_id, source_id, key, encrypted)

    def get_for_source(self, user_id: int, source_id: str) -> dict[str, str]:
        """Return every decrypted credential for a source, keyed by field name.

        An undecryptable row is logged and left out, on :meth:`get`'s reasoning.
        """
        with self._sqlite_db.connection() as conn:
            encrypted_map = get_credentials_for_source(conn, user_id, source_id)
        from cryptography.fernet import InvalidToken

        result: dict[str, str] = {}
        for k, v in encrypted_map.items():
            try:
                result[k] = self._encryptor.decrypt(v)
            except InvalidToken:
                logger.error(
                    "Failed to decrypt credential key=%s for source=%s",
                    k,
                    source_id,
                )
        return result

    def exists(self, user_id: int, source_id: str, key: str) -> bool:
        """Report whether a row is stored, without decrypting it."""
        with self._sqlite_db.connection() as conn:
            return credential_row_exists(conn, user_id, source_id, key)

    def delete(self, user_id: int, source_id: str, key: str) -> bool:
        """Delete one credential row, reporting whether there was one."""
        with self._sqlite_db.connection() as conn:
            return delete_credential(conn, user_id, source_id, key)

    def delete_for_source(self, user_id: int, source_id: str) -> int:
        """Delete every stored credential for a source, returning the count.

        Keyed by source, not by a plugin's current schema: an unregistered
        plugin or a no-longer-sensitive field must not leave a row behind.
        """
        with self._sqlite_db.connection() as conn:
            return delete_credentials_for_source(conn, user_id, source_id)
