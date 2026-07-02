"""Tests for the boot config assembly (const default < YAML < DB).

``migrate_config_settings`` rebuilds each in-scope global/system section by
starting from the registry const defaults, deep-merging the YAML section on top
(YAML overrides consts), then overlaying the database ``settings`` leaves (the
DB is authoritative). It never writes to the database — the ``settings`` table
holds only leaves a user explicitly set later, so a fresh install boots on
const defaults (plus whatever YAML the operator kept) with an empty table.

Granularity is **key-level**: DB leaves are dotted paths (e.g. ``"web.port"``,
``"recommendations.scorer_weights.genre_match"``) overlaid onto the merged
section, so a stored leaf wins at its own path while unstored leaves resolve
from YAML/const. Out-of-scope sections (``storage``, ``inputs``) are untouched.
"""

from pathlib import Path
from typing import Any

import pytest

from src.settings.metadata import default_config
from src.storage.manager import StorageManager
from src.storage.settings_migration import IN_SCOPE_SECTIONS, migrate_config_settings


class TestMigrateConfigSettings:
    """Tests for migrate_config_settings assembly."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_fresh_install_uses_defaults_and_leaves_db_empty(
        self, storage: StorageManager
    ) -> None:
        """Empty config + empty DB → effective config equals registry defaults.

        The core no-seed guarantee: boot writes nothing to the ``settings``
        table, and every in-scope section resolves from the const defaults.
        """
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)

        defaults = default_config()
        for section in IN_SCOPE_SECTIONS:
            assert config[section] == defaults[section]
        # Nothing was written to the database on boot.
        assert storage.list_settings() == {}

    def test_yaml_overrides_const_default(self, storage: StorageManager) -> None:
        """A YAML leaf overrides the registry const default for that leaf."""
        # Const default for web.port is 18473.
        config: dict[str, Any] = {"web": {"port": 20000}}

        migrate_config_settings(config, storage)

        assert config["web"]["port"] == 20000
        # Sibling leaves still resolve from const defaults.
        assert config["web"]["host"] == default_config()["web"]["host"]
        assert storage.list_settings() == {}

    def test_db_leaf_overrides_yaml_and_const(self, storage: StorageManager) -> None:
        """A stored DB leaf wins over both the YAML value and the const default."""
        storage.set_setting("web.port", 9999)
        config: dict[str, Any] = {"web": {"port": 20000}}

        migrate_config_settings(config, storage)

        assert config["web"]["port"] == 9999

    def test_section_absent_from_yaml_resolves_from_defaults(
        self, storage: StorageManager
    ) -> None:
        """An in-scope section missing from the YAML still resolves fully.

        A user may trim config.yaml to bootstrap-only; the const defaults must
        supply every in-scope section so the app still works.
        """
        config: dict[str, Any] = {"storage": {"database_path": "data/x.db"}}

        migrate_config_settings(config, storage)

        assert config["conversation"]["enabled"] is True
        assert config["sync"]["max_workers"] == 4
        assert config["recommendations"]["default_count"] == 5

    def test_db_leaf_resolves_section_absent_from_yaml(
        self, storage: StorageManager
    ) -> None:
        """A DB leaf overlays even when its section is absent from the YAML.

        Regression: the old overlay only touched sections present in the YAML,
        so a stored leaf in an omitted section never resolved. It must now win
        over the const default regardless of the YAML shape.
        """
        storage.set_setting("conversation.enabled", False)
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)

        assert config["conversation"]["enabled"] is False

    def test_new_yaml_leaf_not_in_db_flows_through(
        self, storage: StorageManager
    ) -> None:
        """A YAML leaf with no DB row flows into the effective config."""
        config: dict[str, Any] = {"recommendations": {"default_count": 12}}

        migrate_config_settings(config, storage)

        assert config["recommendations"]["default_count"] == 12
        # Other recommendation leaves still resolve from const defaults.
        assert config["recommendations"]["max_count"] == 20
        assert storage.list_settings() == {}

    def test_nested_yaml_leaf_deep_merges_over_defaults(
        self, storage: StorageManager
    ) -> None:
        """A nested YAML leaf overrides its default while siblings persist."""
        config: dict[str, Any] = {
            "recommendations": {"scorer_weights": {"genre_match": 5.0}}
        }

        migrate_config_settings(config, storage)

        weights = config["recommendations"]["scorer_weights"]
        assert weights["genre_match"] == 5.0
        # Untouched sibling weights keep their const defaults.
        assert weights["creator_match"] == 1.5

    def test_db_leaf_wins_while_new_yaml_leaf_appears(
        self, storage: StorageManager
    ) -> None:
        """A DB leaf wins per-key while other YAML leaves still flow through."""
        storage.set_setting("web.port", 9999)
        config: dict[str, Any] = {"web": {"port": 20000, "host": "0.0.0.0"}}

        migrate_config_settings(config, storage)

        assert config["web"]["port"] == 9999
        assert config["web"]["host"] == "0.0.0.0"
        # Still no writes — only the pre-existing leaf lives in the DB.
        assert storage.list_settings() == {"web.port": 9999}

    def test_unknown_legacy_db_leaf_overlays(self, storage: StorageManager) -> None:
        """A DB leaf with no registry entry still overlays onto its section."""
        storage.set_setting("web.legacy_option", "kept")
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)

        assert config["web"]["legacy_option"] == "kept"

    def test_idempotent_reboot(self, storage: StorageManager) -> None:
        """Running assembly twice keeps the DB empty and the config stable."""
        config: dict[str, Any] = {"sync": {"max_workers": 8}}
        migrate_config_settings(config, storage)

        config2: dict[str, Any] = {"sync": {"max_workers": 8}}
        migrate_config_settings(config2, storage)

        assert config2["sync"]["max_workers"] == 8
        assert storage.list_settings() == {}

    def test_out_of_scope_sections_untouched(self, storage: StorageManager) -> None:
        """The ``storage`` and ``inputs`` sections are left exactly as-is."""
        config: dict[str, Any] = {
            "storage": {"database_path": "data/recommendations.db"},
            "inputs": {"steam": {"plugin": "steam"}},
        }

        migrate_config_settings(config, storage)

        assert config["storage"] == {"database_path": "data/recommendations.db"}
        assert config["inputs"] == {"steam": {"plugin": "steam"}}
        assert storage.list_settings() == {}

    def test_non_dict_yaml_section_falls_back_to_defaults_and_db(
        self, storage: StorageManager
    ) -> None:
        """A malformed non-dict YAML section resolves from const defaults + DB.

        The section cannot deep-merge onto a dict, so it falls back to the const
        defaults, and any DB leaf still overlays without raising.
        """
        storage.set_setting("web.port", 9999)
        config: dict[str, Any] = {"web": "broken"}

        migrate_config_settings(config, storage)

        assert config["web"]["port"] == 9999
        assert config["web"]["host"] == default_config()["web"]["host"]

    def test_does_not_mutate_shared_default_config(
        self, storage: StorageManager
    ) -> None:
        """Assembly must not leak edits back into the const default registry."""
        config: dict[str, Any] = {
            "recommendations": {"scorer_weights": {"genre_match": 99.0}}
        }

        migrate_config_settings(config, storage)

        # A fresh defaults snapshot is unaffected by the previous assembly.
        assert (
            default_config()["recommendations"]["scorer_weights"]["genre_match"] == 2.0
        )


class TestSensitiveLeafHandling:
    """Sensitive leaves (API keys) never reach the plaintext settings table."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_api_key_stays_out_of_db_and_keeps_yaml_value(
        self, storage: StorageManager
    ) -> None:
        """Provider api_key leaves stay in the running config and never hit the DB.

        Boot writes nothing, so the plaintext ``settings`` table cannot leak a
        real API key. The secret comes from YAML and survives the assembly in
        the effective config, while non-sensitive siblings resolve normally.
        """
        config: dict[str, Any] = {
            "enrichment": {
                "enabled": True,
                "providers": {
                    "tmdb": {"api_key": "tmdb-secret", "enabled": True},
                    "rawg": {"api_key": "rawg-secret", "enabled": True},
                },
            }
        }

        migrate_config_settings(config, storage)

        # Nothing was written — no secret can reach the plaintext table.
        assert storage.list_settings() == {}

        # The api_key values survive in the running config (YAML-sourced).
        providers = config["enrichment"]["providers"]
        assert providers["tmdb"]["api_key"] == "tmdb-secret"
        assert providers["rawg"]["api_key"] == "rawg-secret"

        # Non-sensitive siblings resolve from YAML too.
        assert config["enrichment"]["enabled"] is True
        assert providers["tmdb"]["enabled"] is True
        assert providers["rawg"]["enabled"] is True
