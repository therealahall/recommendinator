from pathlib import Path
from typing import Any

import pytest

from src.storage.global_secrets import (
    GLOBAL_SECRET_USER_ID,
    migrate_config_secrets,
    read_secret,
    secret_ref,
)
from src.storage.manager import StorageManager

_TMDB_KEY = "enrichment.providers.tmdb.api_key"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


class TestMigrateConfigSecrets:
    def test_sweeps_sensitive_leaf_into_encrypted_credentials(
        self, storage: StorageManager
    ) -> None:
        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        assert read_secret(storage, _TMDB_KEY) == "yaml_key"

        source_id, credential_key = secret_ref(_TMDB_KEY)
        with storage.connection() as conn:
            row = conn.execute(
                "SELECT credential_value FROM credentials "
                "WHERE user_id = ? AND source_id = ? AND credential_key = ?",
                (GLOBAL_SECRET_USER_ID, source_id, credential_key),
            ).fetchone()
        assert row is not None
        assert row["credential_value"] != "yaml_key"

    def test_strips_secret_from_config_after_migration(
        self, storage: StorageManager
    ) -> None:
        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        provider = config["enrichment"]["providers"]["tmdb"]
        assert "api_key" not in provider
        assert provider["enabled"] is True

    def test_never_persists_plaintext_in_settings_table(
        self, storage: StorageManager
    ) -> None:
        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        assert "yaml_key" not in str(storage.settings.list())

    def test_existing_db_secret_not_clobbered_by_stale_yaml(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage.secrets.set(_TMDB_KEY, "db_key")

        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "stale_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        assert read_secret(storage, _TMDB_KEY) == "db_key"
        assert "api_key" not in config["enrichment"]["providers"]["tmdb"]
        assert "stale_key" not in caplog.text, "config plaintext reached the log"
        assert "db_key" not in caplog.text, "the decrypted secret reached the log"

    def test_idempotent_across_repeated_boots(self, storage: StorageManager) -> None:
        first = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }
        migrate_config_secrets(first, storage)

        second: dict[str, Any] = {
            "enrichment": {"providers": {"tmdb": {"enabled": True, "api_key": ""}}}
        }
        migrate_config_secrets(second, storage)

        assert read_secret(storage, _TMDB_KEY) == "yaml_key"
        assert "api_key" not in second["enrichment"]["providers"]["tmdb"]

    def test_empty_value_is_not_migrated(self, storage: StorageManager) -> None:
        config = {
            "enrichment": {"providers": {"tmdb": {"enabled": True, "api_key": "   "}}}
        }

        migrate_config_secrets(config, storage)

        assert read_secret(storage, _TMDB_KEY) is None

    def test_stale_row_re_encrypted_from_config(self, storage: StorageManager) -> None:
        source_id, credential_key = secret_ref(_TMDB_KEY)
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (?, ?, ?, 'stale_garbage')",
                (GLOBAL_SECRET_USER_ID, source_id, credential_key),
            )
            conn.commit()

        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "fresh_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        assert read_secret(storage, _TMDB_KEY) == "fresh_key"


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
