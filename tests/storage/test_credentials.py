"""Tests for credential CRUD operations and StorageManager integration."""

from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.storage.schema import get_credential


class TestStorageManagerCredentials:
    """Tests for StorageManager credential methods (with encryption)."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_save_and_get_encrypted(self, storage: StorageManager) -> None:
        """Values are encrypted in DB but decrypted when read."""
        storage.credentials.save(1, "gog", "refresh_token", "plain_token_123")

        # Read back through StorageManager (decrypted)
        result = storage.credentials.get(1, "gog", "refresh_token")
        assert result == "plain_token_123"

        # Verify raw DB value is encrypted (not plaintext)
        with storage.connection() as conn:
            raw = get_credential(conn, 1, "gog", "refresh_token")
        assert raw is not None
        assert raw != "plain_token_123"
        # Fernet tokens start with "gAAAAA" (version byte + timestamp)
        assert raw.startswith("gAAAAA")

    def test_get_credentials_for_source_decrypts(self, storage: StorageManager) -> None:
        """get_for_source returns decrypted values."""
        storage.credentials.save(1, "steam", "api_key", "my_steam_key")
        storage.credentials.save(1, "steam", "steam_id", "my_steam_id")

        result = storage.credentials.get_for_source(1, "steam")
        assert result == {"api_key": "my_steam_key", "steam_id": "my_steam_id"}

    def test_a_credential_write_takes_the_manager_lock(
        self, storage: StorageManager
    ) -> None:
        """Catches the store minting a lock of its own.

        ARCHITECTURE.md promises a credential write serialises against a
        content-item write, and nothing else here fails when it stops to.
        """
        assert storage.credentials._save_lock is storage._save_lock

    def test_decrypt_failure_returns_none(self, storage: StorageManager) -> None:
        """Corrupted ciphertext returns None instead of crashing."""
        # Save a credential, then corrupt it directly in the DB
        storage.credentials.save(1, "gog", "refresh_token", "good_token")
        with storage.connection() as conn:
            conn.execute(
                "UPDATE credentials SET credential_value = 'corrupted_garbage' "
                "WHERE source_id = 'gog' AND credential_key = 'refresh_token'"
            )
            conn.commit()

        # Should return None (logged), not raise InvalidToken
        assert storage.credentials.get(1, "gog", "refresh_token") is None
