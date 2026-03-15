"""Tests for credential CRUD operations and StorageManager integration."""

import sqlite3
from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.storage.schema import (
    create_schema,
    get_credential,
    get_credentials_for_source,
    save_credential,
)


class TestCredentialCRUD:
    """Tests for low-level credential schema functions."""

    @pytest.fixture()
    def conn(self) -> sqlite3.Connection:
        """Create an in-memory DB with schema."""
        connection = sqlite3.connect(":memory:")
        create_schema(connection)
        return connection

    def test_save_and_get_credential(self, conn: sqlite3.Connection) -> None:
        """Round-trip: save a credential then retrieve it."""
        save_credential(
            conn,
            user_id=1,
            source_id="gog",
            credential_key="refresh_token",
            credential_value="encrypted_abc",
        )

        result = get_credential(
            conn, user_id=1, source_id="gog", credential_key="refresh_token"
        )
        assert result == "encrypted_abc"

    def test_get_credential_returns_none_when_missing(
        self, conn: sqlite3.Connection
    ) -> None:
        """Returns None for a key that doesn't exist."""
        result = get_credential(
            conn, user_id=1, source_id="gog", credential_key="nonexistent"
        )
        assert result is None

    def test_upsert_overwrites_existing(self, conn: sqlite3.Connection) -> None:
        """Saving the same key again updates the value."""
        save_credential(conn, 1, "gog", "refresh_token", "old_value")
        save_credential(conn, 1, "gog", "refresh_token", "new_value")

        result = get_credential(conn, 1, "gog", "refresh_token")
        assert result == "new_value"

    def test_get_credentials_for_source(self, conn: sqlite3.Connection) -> None:
        """Returns all key-value pairs for a source."""
        save_credential(conn, 1, "steam", "api_key", "steam_key_enc")
        save_credential(conn, 1, "steam", "steam_id", "steam_id_enc")
        save_credential(conn, 1, "gog", "refresh_token", "gog_token_enc")

        result = get_credentials_for_source(conn, 1, "steam")
        assert result == {"api_key": "steam_key_enc", "steam_id": "steam_id_enc"}

    def test_get_credentials_for_source_returns_empty_when_none(
        self, conn: sqlite3.Connection
    ) -> None:
        """Returns empty dict when source has no credentials."""
        result = get_credentials_for_source(conn, user_id=1, source_id="nonexistent")
        assert result == {}

    def test_credentials_scoped_by_user(self, conn: sqlite3.Connection) -> None:
        """Different users have separate credential namespaces."""
        # Create a second user for isolation test
        conn.cursor().execute("INSERT INTO users (id, username) VALUES (2, 'user2')")
        conn.commit()

        save_credential(conn, 1, "gog", "refresh_token", "user1_token")
        save_credential(conn, 2, "gog", "refresh_token", "user2_token")

        assert get_credential(conn, 1, "gog", "refresh_token") == "user1_token"
        assert get_credential(conn, 2, "gog", "refresh_token") == "user2_token"


class TestStorageManagerCredentials:
    """Tests for StorageManager credential methods (with encryption)."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_save_and_get_encrypted(self, storage: StorageManager) -> None:
        """Values are encrypted in DB but decrypted when read."""
        storage.save_credential(1, "gog", "refresh_token", "plain_token_123")

        # Read back through StorageManager (decrypted)
        result = storage.get_credential(1, "gog", "refresh_token")
        assert result == "plain_token_123"

        # Verify raw DB value is encrypted (not plaintext)
        with storage.connection() as conn:
            raw = get_credential(conn, 1, "gog", "refresh_token")
        assert raw is not None
        assert raw != "plain_token_123"
        # Fernet tokens start with "gAAAAA" (version byte + timestamp)
        assert raw.startswith("gAAAAA")

    def test_get_credentials_for_source_decrypts(self, storage: StorageManager) -> None:
        """get_credentials_for_source returns decrypted values."""
        storage.save_credential(1, "steam", "api_key", "my_steam_key")
        storage.save_credential(1, "steam", "steam_id", "my_steam_id")

        result = storage.get_credentials_for_source(1, "steam")
        assert result == {"api_key": "my_steam_key", "steam_id": "my_steam_id"}

    def test_get_credential_returns_none_when_missing(
        self, storage: StorageManager
    ) -> None:
        """Returns None for missing credentials."""
        assert storage.get_credential(1, "gog", "nonexistent") is None

    def test_upsert_updates_encrypted_value(self, storage: StorageManager) -> None:
        """Overwriting a credential re-encrypts the new value."""
        storage.save_credential(1, "gog", "refresh_token", "old_token")
        storage.save_credential(1, "gog", "refresh_token", "new_token")

        assert storage.get_credential(1, "gog", "refresh_token") == "new_token"

    def test_decrypt_failure_returns_none(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """Corrupted ciphertext returns None instead of crashing."""
        # Save a credential, then corrupt it directly in the DB
        storage.save_credential(1, "gog", "refresh_token", "good_token")
        with storage.connection() as conn:
            conn.execute(
                "UPDATE credentials SET credential_value = 'corrupted_garbage' "
                "WHERE source_id = 'gog' AND credential_key = 'refresh_token'"
            )
            conn.commit()

        # Should return None (logged), not raise InvalidToken
        assert storage.get_credential(1, "gog", "refresh_token") is None
