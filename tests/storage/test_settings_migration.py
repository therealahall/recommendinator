from pathlib import Path
from typing import Any

import pytest

from src.settings.metadata import default_config
from src.storage.manager import StorageManager
from src.storage.settings_migration import IN_SCOPE_SECTIONS, migrate_config_settings


class TestMigrateConfigSettings:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_fresh_install_uses_defaults_and_leaves_db_empty(
        self, storage: StorageManager
    ) -> None:
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)

        defaults = default_config()
        for section in IN_SCOPE_SECTIONS:
            assert config[section] == defaults[section]
        assert storage.settings.list() == {}

    def test_db_leaf_resolves_section_absent_from_yaml(
        self, storage: StorageManager
    ) -> None:
        storage.settings.set("enrichment.enabled", True)
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)

        assert config["enrichment"]["enabled"] is True

    def test_nested_yaml_leaf_deep_merges_over_defaults(
        self, storage: StorageManager
    ) -> None:
        config: dict[str, Any] = {
            "recommendations": {"scorer_weights": {"genre_match": 5.0}}
        }

        migrate_config_settings(config, storage)

        weights = config["recommendations"]["scorer_weights"]
        assert weights["genre_match"] == 5.0
        assert weights["creator_match"] == 1.5

    def test_db_leaf_wins_while_new_yaml_leaf_appears(
        self, storage: StorageManager
    ) -> None:
        storage.settings.set("recommendations.default_count", 9)
        config: dict[str, Any] = {
            "recommendations": {"default_count": 11, "max_count": 30}
        }

        migrate_config_settings(config, storage)

        assert config["recommendations"]["default_count"] == 9
        assert config["recommendations"]["max_count"] == 30
        assert storage.settings.list() == {"recommendations.default_count": 9}

    def test_a_stored_log_path_follows_the_log_out_of_its_old_directory(
        self, storage: StorageManager
    ) -> None:
        storage.settings.set("logging.file", "logs/mine.log")
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)

        assert config["logging"]["file"] == "data/logs/mine.log"

    def test_out_of_scope_sections_untouched(self, storage: StorageManager) -> None:
        config: dict[str, Any] = {
            "storage": {"database_path": "data/recommendations.db"},
            "inputs": {"steam": {"plugin": "steam"}},
        }

        migrate_config_settings(config, storage)

        assert config["storage"] == {"database_path": "data/recommendations.db"}
        assert config["inputs"] == {"steam": {"plugin": "steam"}}
        assert storage.settings.list() == {}

    def test_does_not_mutate_shared_default_config(
        self, storage: StorageManager
    ) -> None:
        config: dict[str, Any] = {
            "recommendations": {"scorer_weights": {"genre_match": 99.0}}
        }

        migrate_config_settings(config, storage)

        assert (
            default_config()["recommendations"]["scorer_weights"]["genre_match"] == 2.0
        )


class TestSensitiveLeafHandling:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_api_key_stays_out_of_db_and_keeps_yaml_value(
        self, storage: StorageManager
    ) -> None:
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

        assert storage.settings.list() == {}

        providers = config["enrichment"]["providers"]
        assert providers["tmdb"]["api_key"] == "tmdb-secret"
        assert providers["rawg"]["api_key"] == "rawg-secret"

        assert config["enrichment"]["enabled"] is True
        assert providers["tmdb"]["enabled"] is True
        assert providers["rawg"]["enabled"] is True
