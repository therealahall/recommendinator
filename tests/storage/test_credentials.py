from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.storage.schema import get_credential


class TestStorageManagerCredentials:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_save_and_get_encrypted(self, storage: StorageManager) -> None:
        storage.credentials.save(1, "gog", "refresh_token", "plain_token_123")

        result = storage.credentials.get(1, "gog", "refresh_token")
        assert result == "plain_token_123"

        with storage.connection() as conn:
            raw = get_credential(conn, 1, "gog", "refresh_token")
        assert raw is not None
        assert raw != "plain_token_123"
        assert raw.startswith("gAAAAA")

    def test_get_credentials_for_source_decrypts(self, storage: StorageManager) -> None:
        storage.credentials.save(1, "steam", "api_key", "my_steam_key")
        storage.credentials.save(1, "steam", "steam_id", "my_steam_id")

        result = storage.credentials.get_for_source(1, "steam")
        assert result == {"api_key": "my_steam_key", "steam_id": "my_steam_id"}

    def test_a_credential_write_takes_the_manager_lock(
        self, storage: StorageManager
    ) -> None:
        assert storage.credentials._save_lock is storage._save_lock

    def test_decrypt_failure_returns_none(self, storage: StorageManager) -> None:
        storage.credentials.save(1, "gog", "refresh_token", "good_token")
        with storage.connection() as conn:
            conn.execute(
                "UPDATE credentials SET credential_value = 'corrupted_garbage' "
                "WHERE source_id = 'gog' AND credential_key = 'refresh_token'"
            )
            conn.commit()

        assert storage.credentials.get(1, "gog", "refresh_token") is None
