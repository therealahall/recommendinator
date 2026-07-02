"""Tests for relocating global provider secrets into encrypted storage."""

import logging
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
    """Create a StorageManager backed by an isolated temp DB."""
    return StorageManager(sqlite_path=tmp_path / "test.db")


class TestSecretRef:
    """Tests for the dotted-key -> credentials-coordinate scheme."""

    def test_maps_dotted_key_to_namespaced_source_and_leaf(self) -> None:
        """A registry key splits into a settings: source_id and its leaf key."""
        source_id, credential_key = secret_ref(_TMDB_KEY)

        assert source_id == "settings:enrichment.providers.tmdb"
        assert credential_key == "api_key"

    def test_rejects_non_dotted_key(self) -> None:
        """A bare (dotless) key has no parent path and is rejected."""
        with pytest.raises(ValueError):
            secret_ref("apikey")


class TestMigrateConfigSecrets:
    """Tests for the boot-time secret sweep."""

    def test_sweeps_sensitive_leaf_into_encrypted_credentials(
        self, storage: StorageManager
    ) -> None:
        """A sensitive YAML leaf is stored (encrypted, decryptable) in credentials."""
        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        # Decryptable via the credentials store.
        assert read_secret(storage, _TMDB_KEY) == "yaml_key"

        # Stored ciphertext must not be the plaintext value.
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
        """The plaintext leaf is removed from the in-memory config, siblings kept."""
        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        provider = config["enrichment"]["providers"]["tmdb"]
        assert "api_key" not in provider
        # Non-sensitive siblings survive the sweep.
        assert provider["enabled"] is True

    def test_never_persists_plaintext_in_settings_table(
        self, storage: StorageManager
    ) -> None:
        """The secret must not leak into the plaintext settings store."""
        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        assert "yaml_key" not in str(storage.list_settings())

    def test_existing_db_secret_not_clobbered_by_stale_yaml(
        self, storage: StorageManager
    ) -> None:
        """A readable DB secret wins; a stale YAML copy neither overwrites nor lingers."""
        storage.set_global_secret(_TMDB_KEY, "db_key")

        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "stale_key"}}
            }
        }

        migrate_config_secrets(config, storage)

        assert read_secret(storage, _TMDB_KEY) == "db_key"
        assert "api_key" not in config["enrichment"]["providers"]["tmdb"]

    def test_idempotent_across_repeated_boots(self, storage: StorageManager) -> None:
        """Re-running the sweep leaves the stored secret unchanged."""
        first = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }
        migrate_config_secrets(first, storage)

        # A later boot re-injects the const default (empty api_key); the sweep
        # must keep the migrated value and strip the empty placeholder.
        second: dict[str, Any] = {
            "enrichment": {"providers": {"tmdb": {"enabled": True, "api_key": ""}}}
        }
        migrate_config_secrets(second, storage)

        assert read_secret(storage, _TMDB_KEY) == "yaml_key"
        assert "api_key" not in second["enrichment"]["providers"]["tmdb"]

    def test_empty_value_is_not_migrated(self, storage: StorageManager) -> None:
        """An empty/whitespace secret is skipped, not written to the DB."""
        config = {
            "enrichment": {"providers": {"tmdb": {"enabled": True, "api_key": "   "}}}
        }

        migrate_config_secrets(config, storage)

        assert read_secret(storage, _TMDB_KEY) is None

    def test_missing_section_is_noop(self, storage: StorageManager) -> None:
        """A config without the sensitive section completes without error."""
        migrate_config_secrets({}, storage)

        assert read_secret(storage, _TMDB_KEY) is None

    def test_stale_row_re_encrypted_from_config(self, storage: StorageManager) -> None:
        """An undecryptable row is re-encrypted when config supplies a value."""
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

    def test_stale_row_preserved_without_config_value(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An undecryptable row with no config fallback is preserved, not deleted."""
        source_id, credential_key = secret_ref(_TMDB_KEY)
        with storage.connection() as conn:
            conn.execute(
                "INSERT INTO credentials "
                "(user_id, source_id, credential_key, credential_value) "
                "VALUES (?, ?, ?, 'stale_garbage')",
                (GLOBAL_SECRET_USER_ID, source_id, credential_key),
            )
            conn.commit()

        config: dict[str, Any] = {
            "enrichment": {"providers": {"tmdb": {"enabled": True}}}
        }

        with caplog.at_level(logging.WARNING):
            migrate_config_secrets(config, storage)

        assert storage.has_global_secret(_TMDB_KEY)
        assert "Cannot decrypt" in caplog.text


class TestGlobalSecretAccessors:
    """Tests for the write-only StorageManager global-secret surface."""

    def test_set_and_read_round_trip_through_encryption(
        self, storage: StorageManager
    ) -> None:
        """A set secret is decryptable and stored as ciphertext."""
        storage.set_global_secret(_TMDB_KEY, "round_trip")

        assert read_secret(storage, _TMDB_KEY) == "round_trip"

        source_id, credential_key = secret_ref(_TMDB_KEY)
        with storage.connection() as conn:
            row = conn.execute(
                "SELECT credential_value FROM credentials "
                "WHERE user_id = ? AND source_id = ? AND credential_key = ?",
                (GLOBAL_SECRET_USER_ID, source_id, credential_key),
            ).fetchone()
        assert row["credential_value"] != "round_trip"

    def test_has_reflects_presence(self, storage: StorageManager) -> None:
        """Presence check is accurate before and after setting."""
        assert storage.has_global_secret(_TMDB_KEY) is False

        storage.set_global_secret(_TMDB_KEY, "present")

        assert storage.has_global_secret(_TMDB_KEY) is True

    def test_clear_removes_secret(self, storage: StorageManager) -> None:
        """Clearing removes the secret; a second clear reports nothing removed."""
        storage.set_global_secret(_TMDB_KEY, "to_clear")

        assert storage.clear_global_secret(_TMDB_KEY) is True
        assert storage.has_global_secret(_TMDB_KEY) is False
        assert read_secret(storage, _TMDB_KEY) is None
        assert storage.clear_global_secret(_TMDB_KEY) is False

    def test_set_does_not_touch_settings_table(self, storage: StorageManager) -> None:
        """Setting a global secret never lands plaintext in the settings store."""
        storage.set_global_secret(_TMDB_KEY, "settings_leak_check")

        assert "settings_leak_check" not in str(storage.list_settings())
