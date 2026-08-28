import logging
from pathlib import Path
from typing import Any

import pytest

from src.ingestion.registry import get_registry
from src.sources.service import (
    clear_source_secret_value,
    create_source,
    resolve_inputs,
    set_source_secret_value,
    update_source_config_values,
)
from src.storage.credential_migration import migrate_config_credentials
from src.storage.manager import StorageManager


class TestMigrateConfigCredentials:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_migrates_sensitive_field_to_db(self, storage: StorageManager) -> None:
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "my_gog_token",
                }
            }
        }

        migrate_config_credentials(config, storage)

        assert storage.credentials.get(1, "gog", "refresh_token") == "my_gog_token"

    def test_scrubs_config_after_migration(self, storage: StorageManager) -> None:
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "my_gog_token",
                }
            }
        }

        migrate_config_credentials(config, storage)

        assert "refresh_token" not in config["inputs"]["gog"]

    def test_does_not_overwrite_existing_db_credential(
        self, storage: StorageManager
    ) -> None:
        storage.credentials.save(1, "gog", "refresh_token", "db_token")

        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "config_token",
                }
            }
        }

        migrate_config_credentials(config, storage)

        assert storage.credentials.get(1, "gog", "refresh_token") == "db_token"

    def test_duplicate_plaintext_stripped_when_db_credential_wins(
        self, storage: StorageManager
    ) -> None:
        storage.credentials.save(1, "gog", "refresh_token", "db_token")
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "config_token",
                }
            }
        }

        migrate_config_credentials(config, storage)

        assert "refresh_token" not in config["inputs"]["gog"]

    def test_secret_in_config_logs_deprecation_warning(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "my_gog_token",
                }
            }
        }

        with caplog.at_level(
            logging.WARNING, logger="src.storage.credential_migration"
        ):
            migrate_config_credentials(config, storage)

        deprecations = [m for m in caplog.messages if "DEPRECATED" in m]
        assert len(deprecations) == 1
        assert "'gog.refresh_token'" in deprecations[0]
        assert all("my_gog_token" not in message for message in caplog.messages)

    def test_empty_config_value_not_migrated(self, storage: StorageManager) -> None:
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "",
                }
            }
        }

        migrate_config_credentials(config, storage)

        assert storage.credentials.get(1, "gog", "refresh_token") is None

    def test_stale_credential_re_encrypted_from_config(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (1, 'gog', 'refresh_token', 'stale_garbage')"
            )
            conn.commit()

        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "rotated-plaintext-value",
                }
            }
        }

        with caplog.at_level(
            logging.WARNING, logger="src.storage.credential_migration"
        ):
            migrate_config_credentials(config, storage)

        assert storage.credentials.get(1, "gog", "refresh_token") == (
            "rotated-plaintext-value"
        )
        assert "refresh_token" not in config["inputs"]["gog"]
        deprecations = [m for m in caplog.messages if "DEPRECATED" in m]
        assert len(deprecations) == 1
        assert "'gog.refresh_token'" in deprecations[0]
        assert "re-encrypted" in deprecations[0]
        assert all(
            "rotated-plaintext-value" not in message for message in caplog.messages
        )

    def test_stale_credential_preserved_when_no_config_value(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (1, 'gog', 'refresh_token', 'stale_garbage')"
            )
            conn.commit()

        config: dict[str, Any] = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                }
            }
        }

        with caplog.at_level(logging.WARNING):
            migrate_config_credentials(config, storage)

        assert storage.credentials.exists(1, "gog", "refresh_token")
        assert "Cannot decrypt" in caplog.text

    def test_a_migrated_source_is_never_re_seeded_from_the_file(
        self, storage: StorageManager
    ) -> None:
        plugin = get_registry().get_plugin("sonarr")
        assert plugin is not None
        create_source(
            "sonarr",
            "sonarr",
            {"url": "http://sonarr.internal:8989"},
            storage,
        )
        set_source_secret_value("sonarr", plugin, storage, "api_key", "issued-secret")

        clear_source_secret_value("sonarr", plugin, storage, "api_key")
        update_source_config_values(
            "sonarr", plugin, storage, {"url": "http://attacker.example"}
        )
        config = {
            "inputs": {
                "sonarr": {
                    "plugin": "sonarr",
                    "enabled": True,
                    "api_key": "issued-secret",
                }
            }
        }
        migrate_config_credentials(config, storage)

        resolved = resolve_inputs(config, storage=storage)
        assert not any(entry.config.get("api_key") for entry in resolved)
        assert storage.credentials.get(1, "sonarr", "api_key") is None
        assert "api_key" not in config["inputs"]["sonarr"]

    def test_a_revoked_secret_is_not_resurrected_by_the_next_reload(
        self, storage: StorageManager
    ) -> None:
        plugin = get_registry().get_plugin("sonarr")
        assert plugin is not None
        create_source("sonarr", "sonarr", {"url": "http://sonarr.internal"}, storage)
        set_source_secret_value("sonarr", plugin, storage, "api_key", "issued-secret")
        clear_source_secret_value("sonarr", plugin, storage, "api_key")

        config = {
            "inputs": {
                "sonarr": {
                    "plugin": "sonarr",
                    "enabled": True,
                    "api_key": "issued-secret",
                }
            }
        }
        migrate_config_credentials(config, storage)

        assert storage.credentials.get(1, "sonarr", "api_key") is None

    def test_multiple_sources_migrated(self, storage: StorageManager) -> None:
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "gog_token",
                },
                "my_steam": {
                    "plugin": "steam",
                    "enabled": True,
                    "api_key": "steam_key",
                    "steam_id": "12345",
                },
            }
        }

        migrate_config_credentials(config, storage)

        assert storage.credentials.get(1, "gog", "refresh_token") == "gog_token"
        assert storage.credentials.get(1, "my_steam", "api_key") == "steam_key"
        assert storage.credentials.get(1, "my_steam", "steam_id") is None
