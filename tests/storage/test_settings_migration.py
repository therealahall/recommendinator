"""Tests for config-to-DB settings migration and overlay.

``migrate_config_settings`` seeds the ``settings`` table from the loaded
YAML config on boot (in-scope global/system sections only), then overlays the
DB values back onto the in-memory config dict so the DB wins over YAML.

Granularity is **key-level**: each section is flattened to dotted leaf paths
(e.g. ``"web.port"``, ``"recommendations.scorer_weights.genre_match"``) and
each leaf is a separate ``settings`` row. Seeding never overwrites a leaf
already in the DB, and the overlay deep-merges DB leaves on top of the YAML
section so new YAML leaves still flow through while DB leaves win per-key.
Out-of-scope sections (``storage``, ``inputs``) are left untouched.
"""

from pathlib import Path
from typing import Any

import pytest

from src.storage.manager import StorageManager
from src.storage.settings_migration import IN_SCOPE_SECTIONS, migrate_config_settings


class TestMigrateConfigSettings:
    """Tests for migrate_config_settings."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_seeds_missing_leaves_to_db(self, storage: StorageManager) -> None:
        """In-scope leaves absent from the DB are written as dotted keys."""
        config: dict[str, Any] = {
            "sync": {"max_workers": 8},
            "logging": {"level": "DEBUG"},
        }

        migrate_config_settings(config, storage)

        assert storage.get_setting("sync.max_workers") == 8
        assert storage.get_setting("logging.level") == "DEBUG"

    def test_seeds_nested_structures_as_dotted_leaves(
        self, storage: StorageManager
    ) -> None:
        """Nested dicts are flattened to dotted leaf keys; lists stay whole."""
        config: dict[str, Any] = {
            "recommendations": {
                "default_count": 5,
                "scorer_weights": {"genre_match": 2.0, "tag_overlap": 1.0},
                "source_priority": ["goodreads", "steam"],
            }
        }

        migrate_config_settings(config, storage)

        assert storage.get_setting("recommendations.default_count") == 5
        assert storage.get_setting("recommendations.scorer_weights.genre_match") == 2.0
        assert storage.get_setting("recommendations.scorer_weights.tag_overlap") == 1.0
        # Lists are leaves — stored whole, not descended into.
        assert storage.get_setting("recommendations.source_priority") == [
            "goodreads",
            "steam",
        ]

    def test_never_overwrites_existing_db_leaf(self, storage: StorageManager) -> None:
        """A leaf already in the DB is preserved against the config value."""
        storage.set_setting("web.port", 9999)

        config: dict[str, Any] = {"web": {"port": 18473}}

        migrate_config_settings(config, storage)

        assert storage.get_setting("web.port") == 9999

    def test_overlays_db_leaves_onto_config(self, storage: StorageManager) -> None:
        """After migration, DB leaves win on the in-memory config dict."""
        storage.set_setting("web.port", 9999)
        storage.set_setting("web.host", "0.0.0.0")

        config: dict[str, Any] = {"web": {"port": 18473, "host": "127.0.0.1"}}

        migrate_config_settings(config, storage)

        assert config["web"] == {"port": 9999, "host": "0.0.0.0"}

    def test_new_yaml_key_flows_through_already_seeded_section(
        self, storage: StorageManager
    ) -> None:
        """A NEW YAML key added to an already-seeded section is seeded + overlaid.

        This is the core key-level guarantee the section-level design failed:
        an older DB has only the original leaf, but a newer app version adds a
        leaf to the same section in example.yaml. That new leaf must be seeded
        and must appear in the overlaid config (it is not silently dropped).
        """
        # First boot: only "port" existed in YAML and got seeded.
        first_config: dict[str, Any] = {"web": {"port": 18473}}
        migrate_config_settings(first_config, storage)

        # Second boot of a newer version: YAML now also has "host".
        second_config: dict[str, Any] = {"web": {"port": 18473, "host": "127.0.0.1"}}
        migrate_config_settings(second_config, storage)

        # The new leaf was seeded...
        assert storage.get_setting("web.host") == "127.0.0.1"
        # ...and it flows through into the overlaid running config.
        assert second_config["web"] == {"port": 18473, "host": "127.0.0.1"}

    def test_db_leaf_wins_while_new_yaml_leaf_appears(
        self, storage: StorageManager
    ) -> None:
        """A DB-stored leaf wins over a changed YAML leaf in the same section,
        while OTHER new YAML leaves in that section still appear.

        Combines the two halves: per-leaf DB-wins AND new-key flow-through must
        hold simultaneously within a single section.
        """
        # User edited "port" into the DB at some point.
        storage.set_setting("web.port", 9999)
        # YAML also already had "port" seeded? No — DB leaf pre-exists, so the
        # seed must skip it. YAML now carries a changed port AND a brand-new
        # "host" leaf the DB has never seen.
        config: dict[str, Any] = {"web": {"port": 18473, "host": "0.0.0.0"}}

        migrate_config_settings(config, storage)

        # DB leaf wins for "port"...
        assert config["web"]["port"] == 9999
        # ...but the brand-new YAML leaf "host" still flows through.
        assert config["web"]["host"] == "0.0.0.0"
        # And it got seeded into the DB for next boot.
        assert storage.get_setting("web.host") == "0.0.0.0"

    def test_seeded_value_overlaid_back(self, storage: StorageManager) -> None:
        """A freshly seeded section is overlaid back onto config unchanged."""
        config: dict[str, Any] = {"sync": {"max_workers": 4}}

        migrate_config_settings(config, storage)

        assert config["sync"] == {"max_workers": 4}

    def test_idempotent_reboot_no_duplicate_or_clobber(
        self, storage: StorageManager
    ) -> None:
        """Running migration twice does not duplicate rows or change values."""
        config: dict[str, Any] = {"sync": {"max_workers": 4}}
        migrate_config_settings(config, storage)

        # Simulate a second boot with a different YAML value
        config2: dict[str, Any] = {"sync": {"max_workers": 99}}
        migrate_config_settings(config2, storage)

        # DB still holds the first-seeded leaf; reboot overlays it
        assert storage.get_setting("sync.max_workers") == 4
        assert config2["sync"] == {"max_workers": 4}

    def test_out_of_scope_storage_not_seeded(self, storage: StorageManager) -> None:
        """The ``storage`` bootstrap section is never written to the DB."""
        config: dict[str, Any] = {
            "storage": {"database_path": "data/recommendations.db"}
        }

        migrate_config_settings(config, storage)

        assert storage.list_settings() == {}

    def test_out_of_scope_inputs_not_seeded(self, storage: StorageManager) -> None:
        """The ``inputs`` section is owned by source migration, not this hook."""
        config: dict[str, Any] = {"inputs": {"steam": {"plugin": "steam"}}}

        migrate_config_settings(config, storage)

        assert storage.list_settings() == {}

    def test_missing_section_not_seeded(self, storage: StorageManager) -> None:
        """Sections absent from config are not created in the DB."""
        migrate_config_settings({}, storage)

        assert storage.list_settings() == {}

    def test_only_in_scope_sections_seeded(self, storage: StorageManager) -> None:
        """Every seeded key belongs to a recognised in-scope section."""
        config: dict[str, Any] = {
            "features": {"ai_enabled": False},
            "ollama": {"model": "mistral:7b"},
            "ingestion": {"conflict_strategy": "last_write_wins"},
            "recommendations": {"default_count": 5},
            "conversation": {"enabled": True},
            "sync": {"max_workers": 4},
            "enrichment": {"enabled": False},
            "web": {"port": 18473},
            "logging": {"level": "INFO"},
            "storage": {"database_path": "x"},
            "inputs": {"steam": {}},
        }

        migrate_config_settings(config, storage)

        seeded_sections = {key.split(".", 1)[0] for key in storage.list_settings()}
        assert seeded_sections == set(IN_SCOPE_SECTIONS)
        assert "storage" not in seeded_sections
        assert "inputs" not in seeded_sections

    def test_empty_section_seeds_nothing_then_new_leaf_flows(
        self, storage: StorageManager
    ) -> None:
        """An empty in-scope section seeds nothing and overlays to ``{}``; a leaf
        added to that section on a later boot is seeded and overlaid."""
        first_config: dict[str, Any] = {"web": {}}
        migrate_config_settings(first_config, storage)

        assert storage.list_settings() == {}
        assert first_config["web"] == {}

        # Newer version adds a leaf to the previously empty section.
        second_config: dict[str, Any] = {"web": {"port": 18473}}
        migrate_config_settings(second_config, storage)

        assert storage.get_setting("web.port") == 18473
        assert second_config["web"] == {"port": 18473}

    def test_overlay_rebuilds_non_dict_intermediate(
        self, storage: StorageManager
    ) -> None:
        """The overlay clobbers a non-dict YAML intermediate to place a DB leaf.

        Exercises the ``_set_leaf`` branch where the YAML section value is not a
        dict (here ``"broken"``) but a DB leaf (``web.port``) must be written:
        the intermediate dict is built and the leaf lands without raising.
        """
        storage.set_setting("web.port", 9999)

        config: dict[str, Any] = {"web": "broken"}
        migrate_config_settings(config, storage)

        assert config["web"]["port"] == 9999


class TestSensitiveLeafExclusion:
    """Sensitive leaves (API keys) must never reach the plaintext settings table."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_api_keys_excluded_from_db_but_survive_in_config(
        self, storage: StorageManager
    ) -> None:
        """Provider api_key leaves stay in YAML config and never hit the DB.

        Regression: ``migrate_config_settings`` flattened the whole
        ``enrichment`` section, seeding ``...tmdb.api_key`` /
        ``...rawg.api_key`` as plaintext rows in the unencrypted ``settings``
        table — leaking real API keys. Fix: the sensitive-leaf denylist skips
        those keys at flatten time. They remain only in config.yaml, so the
        overlay leaves the YAML value intact in the running config.
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

        stored = storage.list_settings()
        # The sensitive leaf keys are absent from the settings table...
        assert "enrichment.providers.tmdb.api_key" not in stored
        assert "enrichment.providers.rawg.api_key" not in stored
        # ...and the secret values appear nowhere in the stored values.
        assert "tmdb-secret" not in stored.values()
        assert "rawg-secret" not in stored.values()

        # Non-sensitive leaves in the same section ARE seeded.
        assert storage.get_setting("enrichment.enabled") is True
        assert storage.get_setting("enrichment.providers.tmdb.enabled") is True
        assert storage.get_setting("enrichment.providers.rawg.enabled") is True

        # The overlay leaves the api_key values intact in the running config.
        assert config["enrichment"]["providers"]["tmdb"]["api_key"] == "tmdb-secret"
        assert config["enrichment"]["providers"]["rawg"]["api_key"] == "rawg-secret"
