from pathlib import Path

import pytest

from src.storage.global_secrets import (
    GLOBAL_SECRET_USER_ID,
    read_secret,
    secret_ref,
)
from src.storage.manager import StorageManager

_TMDB_KEY = "enrichment.providers.tmdb.api_key"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


class TestGlobalSecretAccessors:
    def test_set_and_read_round_trip_through_encryption(
        self, storage: StorageManager
    ) -> None:
        storage.secrets.set(_TMDB_KEY, "round_trip")

        assert read_secret(storage, _TMDB_KEY) == "round_trip"

        source_id, credential_key = secret_ref(_TMDB_KEY)
        with storage.connection() as conn:
            row = conn.execute(
                "SELECT credential_value FROM credentials "
                "WHERE user_id = ? AND source_id = ? AND credential_key = ?",
                (GLOBAL_SECRET_USER_ID, source_id, credential_key),
            ).fetchone()
        assert row["credential_value"] != "round_trip"

    def test_clear_removes_secret(self, storage: StorageManager) -> None:
        storage.secrets.set(_TMDB_KEY, "to_clear")

        assert storage.secrets.clear(_TMDB_KEY) is True
        assert storage.secrets.has(_TMDB_KEY) is False
        assert read_secret(storage, _TMDB_KEY) is None
        assert storage.secrets.clear(_TMDB_KEY) is False
