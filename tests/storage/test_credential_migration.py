"""Tests for config-to-DB credential migration."""

import logging
from pathlib import Path
from typing import Any

import pytest

from src.storage.credential_migration import migrate_config_credentials
from src.storage.manager import StorageManager


class TestMigrateConfigCredentials:
    """Tests for migrate_config_credentials."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_migrates_sensitive_field_to_db(self, storage: StorageManager) -> None:
        """Config credential with sensitive=True is migrated to DB on first run."""
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

        assert storage.get_credential(1, "gog", "refresh_token") == "my_gog_token"

    def test_scrubs_config_after_migration(self, storage: StorageManager) -> None:
        """Sensitive value is removed from in-memory config after migration."""
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
        """Existing DB credentials are never overwritten by config values."""
        # Pre-populate DB with a different token
        storage.save_credential(1, "gog", "refresh_token", "db_token")

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

        # DB value should be unchanged
        assert storage.get_credential(1, "gog", "refresh_token") == "db_token"

    def test_duplicate_plaintext_stripped_when_db_credential_wins(
        self, storage: StorageManager
    ) -> None:
        """A redundant config copy is scrubbed even when the DB value wins.

        Regression: the DB-credential-exists branch returned early without
        popping the field, so a plaintext secret the app had already superseded
        stayed in ``app_state.config`` for the lifetime of the process.
        """
        storage.save_credential(1, "gog", "refresh_token", "db_token")
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
        """A sensitive field found in config.yaml warns that the path is deprecated.

        Secrets belong in encrypted storage; the file copy is a legacy path kept
        only so existing installs keep working. The warning has to name the
        source and field so the user knows exactly what to delete.
        """
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
        # The quoted dotted key, not a bare "gog" — that substring also appears
        # in the plugin name and the module path, so it proves nothing about
        # which field the user is being told to delete.
        assert "'gog.refresh_token'" in deprecations[0]
        # The warning fires on every startup and every hot-reload for anyone
        # still holding a secret in config.yaml — exactly the logs that get
        # pasted into bug reports. The value must never appear in one.
        assert all("my_gog_token" not in message for message in caplog.messages)

    def test_ignored_config_secret_does_not_claim_it_was_migrated(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the DB credential wins, the warning must not claim a migration.

        Regression: the deprecation warning was emitted before the branch
        resolved, so a config value that was DISCARDED (because a readable DB
        credential already took precedence) was still announced as "moved to the
        database — delete it from config.yaml". A user rotating a credential the
        legacy way would follow that instruction and destroy the new value while
        the app kept using the stale one — unrecoverable for an OAuth token.
        """
        storage.save_credential(1, "gog", "refresh_token", "db_token")
        config = {
            "inputs": {
                "gog": {
                    "plugin": "gog",
                    "enabled": True,
                    "refresh_token": "rotated_token",
                }
            }
        }

        with caplog.at_level(
            logging.WARNING, logger="src.storage.credential_migration"
        ):
            migrate_config_credentials(config, storage)

        deprecations = [m for m in caplog.messages if "DEPRECATED" in m]
        assert len(deprecations) == 1
        assert "IGNORED, not migrated" in deprecations[0]
        # The DB value is untouched and the rotated file value was never stored.
        assert storage.get_credential(1, "gog", "refresh_token") == "db_token"
        assert all("rotated_token" not in message for message in caplog.messages)

    def test_no_deprecation_warning_when_config_has_no_secret(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A config carrying no secret must not warn — the common case is silent."""
        storage.save_credential(1, "gog", "refresh_token", "db_token")
        config = {"inputs": {"gog": {"plugin": "gog", "enabled": True}}}

        with caplog.at_level(
            logging.WARNING, logger="src.storage.credential_migration"
        ):
            migrate_config_credentials(config, storage)

        assert [m for m in caplog.messages if "DEPRECATED" in m] == []

    def test_empty_config_value_not_migrated(self, storage: StorageManager) -> None:
        """Empty or whitespace-only config values are skipped."""
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

        assert storage.get_credential(1, "gog", "refresh_token") is None

    def test_unknown_plugin_skipped(self, storage: StorageManager) -> None:
        """Sources with unknown plugins are silently skipped."""
        config = {
            "inputs": {
                "unknown_source": {
                    "plugin": "nonexistent_plugin",
                    "enabled": True,
                    "api_key": "some_key",
                }
            }
        }

        # Should not raise
        migrate_config_credentials(config, storage)

    def test_missing_plugin_field_skipped(self, storage: StorageManager) -> None:
        """Entry with no 'plugin' key is silently skipped."""
        config = {"inputs": {"broken": {"enabled": True, "api_key": "some_key"}}}

        migrate_config_credentials(config, storage)

        assert storage.get_credential(1, "broken", "api_key") is None

    def test_non_dict_entries_skipped(self, storage: StorageManager) -> None:
        """Non-dict entries in inputs are skipped."""
        config = {"inputs": {"bad_entry": "not_a_dict"}}

        # Should not raise
        migrate_config_credentials(config, storage)

    def test_empty_inputs_is_noop(self, storage: StorageManager) -> None:
        """Config with empty or missing inputs section completes without error."""
        migrate_config_credentials({"inputs": {}}, storage)
        migrate_config_credentials({}, storage)

    def test_stale_credential_re_encrypted_from_config(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stale (unreadable) credential is re-encrypted when config has a value.

        Regression: encryption key change left stale ciphertext in DB.
        Migration should detect the unreadable row and overwrite it with
        a freshly encrypted value from config.
        """
        # Write a stale (unreadable) row directly to DB
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (1, 'gog', 'refresh_token', 'stale_garbage')"
            )
            conn.commit()

        # Deliberately not "fresh_token": that is a substring of the field name
        # "refresh_token", so a no-leak assertion against it would false-positive
        # on the field name the warning legitimately prints.
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

        assert storage.get_credential(1, "gog", "refresh_token") == (
            "rotated-plaintext-value"
        )
        # This branch migrates, so it must also scrub the plaintext copy and say
        # accurately what it did — the same contract the other two branches have.
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
        """Stale credential with no config fallback is preserved, not purged.

        Bug reported: GOG credential was silently deleted during startup
        when decryption failed and config had no fallback value.
        Root cause: migration purged unreadable credentials instead of
        leaving them for the user to fix (e.g. by restoring the key file).
        Fix: log a warning but never delete credentials automatically.
        """
        # Write a stale row (can't be decrypted — raw garbage, not encrypted)
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (1, 'gog', 'refresh_token', 'stale_garbage')"
            )
            conn.commit()

        # Config has no refresh_token
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

        # Row must still exist — never silently delete credentials
        assert storage.credential_row_exists(1, "gog", "refresh_token")
        assert "Cannot decrypt" in caplog.text

    def test_multiple_sources_migrated(self, storage: StorageManager) -> None:
        """Multiple sources with sensitive fields are all migrated."""
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

        assert storage.get_credential(1, "gog", "refresh_token") == "gog_token"
        assert storage.get_credential(1, "my_steam", "api_key") == "steam_key"
        # steam_id is not sensitive, so should NOT be migrated
        assert storage.get_credential(1, "my_steam", "steam_id") is None
