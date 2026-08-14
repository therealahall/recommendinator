"""Tests for sync source resolution."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.ingestion.sync import execute_sync
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.sources.service import (
    SourceConfigError,
    create_source,
    delete_source,
    get_available_sync_sources,
    get_sync_handler,
    redact_credentials,
    resolve_inputs,
    update_source_config_values,
    validate_source_config,
)
from src.storage.manager import StorageManager


class FakeBookPlugin(SourcePlugin):
    """Fake book plugin for resolve_inputs testing."""

    @property
    def name(self) -> str:
        return "fake_books"

    @property
    def display_name(self) -> str:
        return "Fake Books"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="path", field_type=str, required=True),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("path"):
            errors.append("'path' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="book_1",
            title="Fake Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source=self.get_source_identifier(config),
        )


class FakeGamePlugin(SourcePlugin):
    """Fake game plugin for resolve_inputs testing."""

    @property
    def name(self) -> str:
        return "fake_games"

    @property
    def display_name(self) -> str:
        return "Fake Games"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="api_key", field_type=str, required=True, sensitive=True),
            ConfigField(
                name="url",
                field_type=str,
                required=False,
                default="http://localhost:7878",
                credential_bound=True,
            ),
            ConfigField(name="label", field_type=str, required=False),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="game_1",
            title="Fake Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class FakeCredentialPlugin(SourcePlugin):
    """Fake plugin that performs DB credential lookup in validate_config.

    Mimics the pattern used by Epic Games and GOG plugins: when a required
    sensitive field is missing from config, the plugin checks the DB for
    stored credentials before reporting an error.
    """

    @property
    def name(self) -> str:
        return "fake_credential"

    @property
    def display_name(self) -> str:
        return "Fake Credential"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="refresh_token",
                field_type=str,
                required=True,
                sensitive=True,
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []
        if not (config.get("refresh_token") or "").strip():
            source_id = config.get("_source_id", self.name)
            if storage is not None:
                db_creds = storage.get_credentials_for_source(user_id, source_id)
                if (db_creds.get("refresh_token") or "").strip():
                    return errors
            errors.append("'refresh_token' is required")
        return errors

    @classmethod
    def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "refresh_token": raw_fields.get("refresh_token", "").strip(),
        }

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="cred_1",
            title="Fake Credential Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class RecordingGamePlugin(FakeGamePlugin):
    """Keeps every config and storage it was asked to validate against."""

    def __init__(self) -> None:
        self.validated: list[tuple[dict[str, Any], StorageManager | None]] = []

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        self.validated.append((dict(config), storage))
        return super().validate_config(config)


@pytest.fixture()
def _registry_with_fakes() -> Iterator[None]:
    """Set up a registry with fake plugins for testing."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(FakeBookPlugin())
    registry.register(FakeGamePlugin())
    registry.register(FakeCredentialPlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.mark.usefixtures("_registry_with_fakes")
class TestResolveInputs:
    """Tests for resolve_inputs function."""

    def test_basic_resolution(self) -> None:
        """Test resolving a single enabled input."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 1
        assert resolved[0].source_id == "my_books"
        assert resolved[0].plugin.name == "fake_books"
        assert resolved[0].config["path"] == "/data/books.csv"

    def test_disabled_entries_skipped(self) -> None:
        """Test that disabled entries are not resolved."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": False,
                    "path": "/data/books.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 0

    def test_multiple_instances_same_plugin(self) -> None:
        """Test that multiple instances of the same plugin resolve correctly."""
        config = {
            "inputs": {
                "fiction_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/fiction.csv",
                },
                "nonfiction_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/nonfiction.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 2
        source_ids = {entry.source_id for entry in resolved}
        assert source_ids == {"fiction_books", "nonfiction_books"}

        # Both use the same plugin type
        for entry in resolved:
            assert entry.plugin.name == "fake_books"

        # Each has its own config
        paths = {entry.config["path"] for entry in resolved}
        assert paths == {"/data/fiction.csv", "/data/nonfiction.csv"}

    def test_source_id_injected_into_config(self) -> None:
        """Test that _source_id is injected into the resolved config."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert resolved[0].config["_source_id"] == "my_books"

    def test_plugin_and_enabled_keys_stripped(self) -> None:
        """Test that 'plugin' and 'enabled' keys are removed from config."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert "plugin" not in resolved[0].config
        assert "enabled" not in resolved[0].config

    def test_unknown_plugin_skipped(self) -> None:
        """Test that entries with unknown plugin names are skipped."""
        config = {
            "inputs": {
                "mystery": {
                    "plugin": "nonexistent_plugin",
                    "enabled": True,
                    "path": "/data/mystery.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 0

    def test_missing_plugin_field_skipped(self) -> None:
        """Test that entries without a 'plugin' field are skipped."""
        config = {
            "inputs": {
                "broken": {
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 0

    def test_empty_inputs(self) -> None:
        """Test that empty inputs config returns empty list."""
        resolved = resolve_inputs({})

        assert len(resolved) == 0

    def test_mixed_enabled_disabled(self) -> None:
        """Test with a mix of enabled and disabled entries."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": False,
                    "api_key": "test",
                },
                "more_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/more.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 2
        source_ids = {entry.source_id for entry in resolved}
        assert source_ids == {"my_books", "more_books"}


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceIdPropagation:
    """Tests for _source_id propagation to ContentItem.source."""

    def test_source_id_in_fetched_items(self) -> None:
        """Test that _source_id propagates to ContentItem.source via fetch."""
        config = {
            "inputs": {
                "fiction_shelf": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/fiction.csv",
                },
            }
        }

        resolved = resolve_inputs(config)
        items = list(resolved[0].plugin.fetch(resolved[0].config))

        assert len(items) == 1
        assert items[0].source == "fiction_shelf"

    def test_different_instances_have_different_source_ids(self) -> None:
        """Test that different instances of the same plugin produce different source IDs."""
        config = {
            "inputs": {
                "fiction_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/fiction.csv",
                },
                "nonfiction_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/nonfiction.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        fiction_items = list(resolved[0].plugin.fetch(resolved[0].config))
        nonfiction_items = list(resolved[1].plugin.fetch(resolved[1].config))

        # Source IDs match the user-defined keys
        assert fiction_items[0].source == resolved[0].source_id
        assert nonfiction_items[0].source == resolved[1].source_id
        assert fiction_items[0].source != nonfiction_items[0].source

    def test_fallback_to_plugin_name_without_source_id(self) -> None:
        """Test that plugins fall back to plugin name when no _source_id in config."""
        plugin = FakeBookPlugin()
        items = list(plugin.fetch({"path": "/data/books.csv"}))

        assert items[0].source == "fake_books"


@pytest.mark.usefixtures("_registry_with_fakes")
class TestGetAvailableSyncSources:
    """Tests for get_available_sync_sources function.

    The listing surface includes BOTH enabled and disabled sources so the
    UI can render disabled accordions in a muted state. ``resolve_inputs``
    is the gate that filters to enabled-only for sync execution.
    """

    def test_returns_all_sources_with_enabled_flag(self) -> None:
        """Both enabled and disabled sources are listed; enabled flag exposed."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": False,
                    "api_key": "test",
                },
            }
        }

        sources = get_available_sync_sources(config)
        by_id = {s.id: s for s in sources}

        assert by_id["my_books"].enabled is True
        assert by_id["my_books"].display_name == "My Books"
        assert by_id["my_books"].plugin_display_name == "Fake Books"
        assert by_id["my_games"].enabled is False

    def test_skips_unknown_plugin(self) -> None:
        """Sources referencing an unregistered plugin are dropped from listing."""
        config = {
            "inputs": {
                "ghost": {
                    "plugin": "nonexistent_plugin",
                    "enabled": True,
                },
            }
        }
        assert get_available_sync_sources(config) == []

    def test_empty_config(self) -> None:
        """Test that empty config returns empty list."""
        sources = get_available_sync_sources({})

        assert len(sources) == 0


@pytest.mark.usefixtures("_registry_with_fakes")
class TestGetSyncHandler:
    """Tests for get_sync_handler function."""

    def test_finds_handler_by_source_id(self) -> None:
        """Test finding a sync handler by its user-defined source ID."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        handler = get_sync_handler("my_books", config)

        assert handler is not None
        assert handler.source_id == "my_books"
        assert handler.plugin.name == "fake_books"

    def test_returns_none_for_unknown_source(self) -> None:
        """Test that unknown source ID returns None."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        handler = get_sync_handler("nonexistent", config)

        assert handler is None

    def test_returns_none_for_disabled_source(self) -> None:
        """Test that disabled source returns None."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": False,
                    "path": "/data/books.csv",
                },
            }
        }

        handler = get_sync_handler("my_books", config)

        assert handler is None


@pytest.mark.usefixtures("_registry_with_fakes")
class TestValidateSourceConfig:
    """Tests for validate_source_config function."""

    def test_validates_valid_config(self) -> None:
        """Test validation passes for valid config."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        errors = validate_source_config("my_books", config)

        assert errors == []

    def test_validates_invalid_config(self) -> None:
        """Test validation fails for invalid config."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                },
            }
        }

        errors = validate_source_config("my_books", config)

        assert len(errors) == 1
        assert "'path' is required" in errors[0]

    def test_unknown_source_returns_error(self) -> None:
        """Test that unknown source returns an error."""
        errors = validate_source_config("nonexistent", {})

        assert len(errors) == 1
        assert "Unknown or disabled source" in errors[0]


@pytest.mark.usefixtures("_registry_with_fakes")
class TestResolveInputsWithStorage:
    """Tests for resolve_inputs with DB credential injection."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_db_credential_injected_into_config(self, storage: StorageManager) -> None:
        """DB credentials override config-file values for sensitive fields."""
        config = {
            "inputs": {
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": True,
                    "api_key": "config_key",
                }
            }
        }
        storage.save_credential(1, "my_games", "api_key", "db_key")

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].config["api_key"] == "db_key"

    def test_config_only_when_no_storage(self) -> None:
        """Without storage, only config values are used."""
        config = {
            "inputs": {
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": True,
                    "api_key": "config_key",
                }
            }
        }

        resolved = resolve_inputs(config, storage=None)

        assert len(resolved) == 1
        assert resolved[0].config["api_key"] == "config_key"

    def test_config_fallback_when_no_db_credential(
        self, storage: StorageManager
    ) -> None:
        """Config value used when no DB credential exists for the field."""
        config = {
            "inputs": {
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": True,
                    "api_key": "config_key",
                }
            }
        }
        # No DB credential saved for my_games

        resolved = resolve_inputs(config, storage=storage)

        assert resolved[0].config["api_key"] == "config_key"


@pytest.mark.usefixtures("_registry_with_fakes")
class TestValidateSourceConfigWithStorage:
    """Integration tests: validate_source_config forwards storage to plugins.

    Regression test for a bug where validate_source_config did not pass
    storage and user_id through to plugin.validate_config(), preventing
    DB credential lookup from being reached through the normal code path.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_db_credential_satisfies_validation(self, storage: StorageManager) -> None:
        """validate_source_config returns no errors when credential is in DB.

        The plugin's config has no refresh_token, but the DB has one stored.
        validate_source_config must forward storage and user_id so the plugin
        can find the credential and pass validation.
        """
        config = {
            "inputs": {
                "my_epic": {
                    "plugin": "fake_credential",
                    "enabled": True,
                    # No refresh_token in config
                },
            }
        }
        storage.save_credential(1, "my_epic", "refresh_token", "db_token_value")

        errors = validate_source_config("my_epic", config, storage=storage, user_id=1)

        assert errors == []

    def test_missing_credential_everywhere_fails(self, storage: StorageManager) -> None:
        """validate_source_config returns errors when credential is missing from both."""
        config = {
            "inputs": {
                "my_epic": {
                    "plugin": "fake_credential",
                    "enabled": True,
                },
            }
        }
        # No credential in DB either

        errors = validate_source_config("my_epic", config, storage=storage, user_id=1)

        assert len(errors) == 1
        assert "'refresh_token' is required" in errors[0]

    def test_config_credential_still_validates(self, storage: StorageManager) -> None:
        """validate_source_config passes when credential is in config (not DB)."""
        config = {
            "inputs": {
                "my_epic": {
                    "plugin": "fake_credential",
                    "enabled": True,
                    "refresh_token": "config_token",
                },
            }
        }

        errors = validate_source_config("my_epic", config, storage=storage, user_id=1)

        assert errors == []


@pytest.mark.usefixtures("_registry_with_fakes")
class TestResolveInputsWithDbSourceConfig:
    """Tests for resolve_inputs DB-backed source_configs precedence."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_db_config_overrides_yaml_when_migrated(
        self, storage: StorageManager
    ) -> None:
        """When source_configs has a row, DB values authoritative; yaml ignored."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/yaml/books.csv",
                },
            }
        }
        storage.upsert_source_config(
            1, "my_books", "fake_books", {"path": "/db/books.csv"}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].config["path"] == "/db/books.csv"
        assert resolved[0].source_id == "my_books"

    def test_db_config_disabled_excludes_source(self, storage: StorageManager) -> None:
        """A migrated source with enabled=False is excluded even when yaml is enabled."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/yaml/books.csv",
                },
            }
        }
        storage.upsert_source_config(
            1, "my_books", "fake_books", {"path": "/db/books.csv"}, enabled=False
        )

        resolved = resolve_inputs(config, storage=storage)

        assert resolved == []

    def test_db_only_source_resolves(self, storage: StorageManager) -> None:
        """A source that exists in DB but not yaml still resolves."""
        config: dict[str, Any] = {"inputs": {}}
        storage.upsert_source_config(
            1, "books_only_in_db", "fake_books", {"path": "/db/x.csv"}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].source_id == "books_only_in_db"
        assert resolved[0].config["path"] == "/db/x.csv"

    def test_yaml_only_source_still_resolves_when_other_migrated(
        self, storage: StorageManager
    ) -> None:
        """Mixed state: one yaml-only and one migrated source both resolve."""
        config = {
            "inputs": {
                "yaml_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/yaml/yaml_books.csv",
                },
                "migrated_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/yaml/old_path.csv",
                },
            }
        }
        storage.upsert_source_config(
            1,
            "migrated_books",
            "fake_books",
            {"path": "/db/new_path.csv"},
            enabled=True,
        )

        resolved = resolve_inputs(config, storage=storage)

        by_id = {entry.source_id: entry for entry in resolved}
        assert by_id["yaml_books"].config["path"] == "/yaml/yaml_books.csv"
        assert by_id["migrated_books"].config["path"] == "/db/new_path.csv"

    def test_db_config_strips_yaml_for_disabled_yaml_entry(
        self, storage: StorageManager
    ) -> None:
        """When yaml has the source disabled but DB enables it, DB wins."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": False,
                    "path": "/yaml/books.csv",
                },
            }
        }
        storage.upsert_source_config(
            1, "my_books", "fake_books", {"path": "/db/books.csv"}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].config["path"] == "/db/books.csv"

    def test_db_config_merges_with_credentials(self, storage: StorageManager) -> None:
        """Sensitive creds from credentials table merge over DB config dict."""
        config: dict[str, Any] = {"inputs": {}}
        storage.upsert_source_config(1, "my_games", "fake_games", {}, enabled=True)
        storage.save_credential(1, "my_games", "api_key", "secret_from_creds")

        resolved = resolve_inputs(config, storage=storage)

        assert resolved[0].config["api_key"] == "secret_from_creds"

    def test_get_available_sync_sources_includes_db_only(
        self, storage: StorageManager
    ) -> None:
        """A DB-only source appears in get_available_sync_sources."""
        config: dict[str, Any] = {"inputs": {}}
        storage.upsert_source_config(
            1, "db_only", "fake_books", {"path": "/x.csv"}, enabled=True
        )

        sources = get_available_sync_sources(config, storage=storage)

        ids = {s.id for s in sources}
        assert ids == {"db_only"}

    def test_get_sync_handler_finds_db_only_source(
        self, storage: StorageManager
    ) -> None:
        """get_sync_handler resolves a DB-only source by its id."""
        config: dict[str, Any] = {"inputs": {}}
        storage.upsert_source_config(
            1, "db_only", "fake_books", {"path": "/x.csv"}, enabled=True
        )

        handler = get_sync_handler("db_only", config, storage=storage)

        assert handler is not None
        assert handler.source_id == "db_only"

    def test_db_config_with_unregistered_plugin_is_skipped(
        self, storage: StorageManager
    ) -> None:
        """DB row referencing an unknown plugin is silently skipped (not crashed).

        Regression scenario: a plugin gets renamed or removed after the user
        migrated their source. The DB row still references the old plugin
        name. ``resolve_inputs`` must log + skip rather than raise.
        """
        config: dict[str, Any] = {"inputs": {}}
        storage.upsert_source_config(
            1, "ghost_source", "this_plugin_no_longer_exists", {}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert resolved == []


@pytest.mark.usefixtures("_registry_with_fakes")
class TestCredentialBoundUpdates:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture()
    def migrated(self, storage: StorageManager) -> StorageManager:
        storage.upsert_source_config(
            1, "my_games", "fake_games", {"url": "http://localhost:7878"}, enabled=True
        )
        storage.save_credential(1, "my_games", "api_key", "issued-for-localhost")
        return storage

    @staticmethod
    def _update(storage: StorageManager, values: dict[str, Any]) -> None:
        update_source_config_values("my_games", FakeGamePlugin(), storage, values)

    def test_repointing_the_url_is_refused_and_changes_nothing(
        self, migrated: StorageManager
    ) -> None:
        with pytest.raises(SourceConfigError) as refusal:
            self._update(migrated, {"url": "http://attacker.example"})

        assert refusal.value.kind == "credential_move"
        assert refusal.value.message == (
            "Changing 'url' points this source at a different host. Clear its "
            "stored 'api_key' first, then save this change and enter the "
            "credential the new host expects."
        )
        assert migrated.get_credential(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )
        row = migrated.get_source_config(1, "my_games")
        assert row is not None
        assert row["config"]["url"] == "http://localhost:7878"

    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost:7878",
            "http://localhost:7878/",
            "http://localhost:7878/radarr",
        ],
    )
    def test_a_rewrite_that_keeps_the_host_keeps_the_secret(
        self, migrated: StorageManager, url: str
    ) -> None:
        """Scheme, trailing slash and path do not decide who receives it."""
        self._update(migrated, {"url": url})

        assert migrated.get_credential(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )
        row = migrated.get_source_config(1, "my_games")
        assert row is not None and row["config"]["url"] == url

    @pytest.mark.parametrize(
        "url", ["http://other.example:7878", "http://localhost:9999"]
    )
    def test_a_new_host_or_port_is_a_move(
        self, migrated: StorageManager, url: str
    ) -> None:
        with pytest.raises(SourceConfigError, match="different host"):
            self._update(migrated, {"url": url})

    @pytest.mark.parametrize(
        "url", ["http://attacker.example:99999", "http://attacker.example:notaport"]
    )
    def test_a_url_whose_port_does_not_parse_is_a_move(
        self, migrated: StorageManager, url: str
    ) -> None:
        """It addresses a party nobody can read, which is not this one."""
        with pytest.raises(SourceConfigError) as refusal:
            self._update(migrated, {"url": url})

        assert refusal.value.kind == "credential_move"
        assert migrated.get_credential(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )

    def test_the_move_is_allowed_once_the_secret_is_gone(
        self, migrated: StorageManager
    ) -> None:
        migrated.delete_credential(1, "my_games", "api_key")

        self._update(migrated, {"url": "http://attacker.example"})

        row = migrated.get_source_config(1, "my_games")
        assert row is not None
        assert row["config"]["url"] == "http://attacker.example"

    def test_an_unset_bound_field_is_measured_from_the_plugin_default(
        self, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_games", {}, enabled=True)
        storage.save_credential(1, "my_games", "api_key", "issued-for-the-default")

        with pytest.raises(SourceConfigError, match="different host"):
            self._update(storage, {"url": "http://attacker.example"})

        self._update(storage, {"url": "https://localhost:7878"})

        assert storage.get_credential(1, "my_games", "api_key") == (
            "issued-for-the-default"
        )

    def test_an_empty_update_leaves_the_secret_alone(
        self, migrated: StorageManager
    ) -> None:
        self._update(migrated, {})

        assert migrated.get_credential(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )

    def test_a_non_binding_field_leaves_the_secret_alone(
        self, migrated: StorageManager
    ) -> None:
        self._update(migrated, {"label": "Games"})

        assert migrated.get_credential(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )

    def test_a_new_source_does_not_inherit_an_orphaned_secret(
        self, storage: StorageManager
    ) -> None:
        """Delete-then-recreate must not resurrect a secret under a new url."""
        storage.save_credential(1, "my_games", "api_key", "issued-for-localhost")

        create_source(
            "my_games",
            "fake_games",
            {"url": "http://attacker.example"},
            storage,
        )

        assert storage.get_credential(1, "my_games", "api_key") is None


@pytest.mark.usefixtures("_registry_with_fakes")
class TestUnreadableUrlWalksTheCredentialRegression:
    """Regression: two accepted writes walked the api key to another host.

    An unparseable port read as "addresses nobody", so neither the write onto
    it nor the write off it was a move. Unreadable is its own answer now.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _update(storage: StorageManager, values: dict[str, Any]) -> None:
        update_source_config_values("my_games", FakeGamePlugin(), storage, values)

    def test_neither_step_of_the_walk_is_accepted(
        self, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(
            1, "my_games", "fake_games", {"url": "http://localhost:7878"}, enabled=True
        )
        storage.save_credential(1, "my_games", "api_key", "issued-for-localhost")

        with pytest.raises(SourceConfigError) as onto:
            self._update(storage, {"url": "http://attacker.example:99999"})

        # A config.yaml entry reaches the same state without a write: migration
        # copies the url in unvalidated.
        storage.upsert_source_config(
            1,
            "my_games",
            "fake_games",
            {"url": "http://attacker.example:99999"},
            enabled=True,
        )

        with pytest.raises(SourceConfigError) as off:
            self._update(storage, {"url": "http://attacker.example"})

        assert onto.value.kind == "credential_move"
        assert off.value.kind == "credential_move"
        assert storage.get_credential(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )


@pytest.mark.usefixtures("_registry_with_fakes")
class TestDeleteSourceOrphanedCredentialsRegression:
    """Regression: removing a source left encrypted rows behind.

    Bug: cleanup iterated the registered plugin's sensitive fields, so an
    unregistered plugin skipped it entirely and a field that stopped being
    sensitive was missed. Fix: delete every row keyed by source id.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_unregistered_plugin_leaves_no_credential_row(
        self, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(
            1, "ghost", "this_plugin_no_longer_exists", {}, enabled=True
        )
        storage.save_credential(1, "ghost", "api_key", "still-valid-upstream")

        delete_source("ghost", storage, {})

        assert storage.get_credentials_for_source(1, "ghost") == {}
        assert storage.get_source_config(1, "ghost") is None

    def test_a_field_no_longer_marked_sensitive_is_removed_too(
        self, storage: StorageManager
    ) -> None:
        """``fake_games`` has no ``legacy_token`` field; the row exists anyway."""
        storage.upsert_source_config(1, "my_games", "fake_games", {}, enabled=True)
        storage.save_credential(1, "my_games", "api_key", "secret")
        storage.save_credential(1, "my_games", "legacy_token", "was-sensitive-once")

        delete_source("my_games", storage, {})

        assert storage.get_credentials_for_source(1, "my_games") == {}

    def test_another_sources_credentials_survive(self, storage: StorageManager) -> None:
        storage.upsert_source_config(1, "my_games", "fake_games", {}, enabled=True)
        storage.save_credential(1, "my_games", "api_key", "secret")
        storage.save_credential(1, "other", "api_key", "untouched")

        delete_source("my_games", storage, {})

        assert storage.get_credential(1, "other", "api_key") == "untouched"


class TestWriteValidationNeverSeesTheDecryptedSecretRegression:
    """Reported: the write door validated a config carrying the decrypted
    credential, and those messages reach the wire now. A plugin quoting a
    value it was handed would answer an HTTP body holding the secret.
    """

    def test_neither_validated_config_carries_the_stored_credential(
        self, tmp_path: Path
    ) -> None:
        """``storage`` still goes through, so a plugin can ask whether it is set."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.upsert_source_config(
            1, "my_games", "fake_games", {"label": "Games"}, enabled=True
        )
        storage.save_credential(1, "my_games", "api_key", "issued-for-localhost")
        plugin = RecordingGamePlugin()

        update_source_config_values("my_games", plugin, storage, {"label": "Shelf"})

        assert len(plugin.validated) == 2
        assert all(config.get("api_key") is None for config, _ in plugin.validated)
        assert all(seen is storage for _, seen in plugin.validated)


ROTATING_PLUGINS = ("gog", "epic_games", "trakt")
ROTATED_TOKEN = "rotated-during-this-sync"


def _rotates_on_fetch(real_plugin: SourcePlugin) -> SourcePlugin:
    """The real plugin class with only ``fetch`` replaced.

    Subclassed rather than stubbed so the config under test is built by the
    plugin's own ``transform_fields``, the step the source id was lost in.
    """

    class RotatesOnFetch(type(real_plugin)):  # type: ignore[misc,valid-type]
        def fetch(
            self, config: dict[str, Any], progress_callback: Any = None
        ) -> Iterator[ContentItem]:
            config["_on_credential_rotated"]("refresh_token", ROTATED_TOKEN)
            return iter(())

    return RotatesOnFetch()


@pytest.fixture()
def _real_registry() -> Iterator[None]:
    """The real plugins, whatever fakes an earlier test left registered."""
    PluginRegistry.reset_instance()
    PluginRegistry.get_instance().discover_plugins()
    yield
    PluginRegistry.reset_instance()


def _discovered_plugins() -> dict[str, SourcePlugin]:
    """Every plugin ``_real_registry`` discovered, never an empty mapping.

    A sweep over an undiscovered registry iterates nothing and passes, so the
    guard belongs here rather than in each caller that keeps forgetting it.
    """
    plugins = PluginRegistry.get_instance().get_all_plugins()
    assert plugins, "discovery found nothing, so the sweep proves nothing"
    return plugins


class TestTheSweepHelperRefusesAnEmptyRegistry:
    """Nothing else here tells a working guard from a missing one.

    Every sweep reading the helper passes on an empty mapping, which is the
    hole the guard closes.
    """

    def test_discovery_finding_nothing_raises_instead_of_sweeping_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patched rather than emptied: ``get_all_plugins`` runs discovery
        # itself, so a fresh registry cannot be left holding nothing.
        monkeypatch.setattr(PluginRegistry, "get_all_plugins", lambda self: {})

        with pytest.raises(AssertionError, match="discovery found nothing"):
            _discovered_plugins()


@pytest.fixture()
def _registry_with_rotating_doubles(_real_registry: None) -> Iterator[None]:
    """The real token-rotating plugins, with fetching stubbed out."""
    registry = PluginRegistry.get_instance()
    for plugin_name in ROTATING_PLUGINS:
        real_plugin = registry.get_plugin(plugin_name)
        assert real_plugin is not None
        registry.unregister(plugin_name)
        registry.register(_rotates_on_fetch(real_plugin))
    yield


@pytest.mark.usefixtures("_real_registry")
class TestCreateSourceRefusesUncontainedPaths:
    """``roms`` scans a list of directories, and every entry must be contained.

    Its multi-entry ``paths`` was checked at plugin level only, never through
    the boundary an HTTP caller reaches it by.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_a_path_outside_the_allowed_roots_is_refused(
        self, storage: StorageManager
    ) -> None:
        with pytest.raises(SourceConfigError) as raised:
            create_source("leaky", "roms", {"paths": ["/etc"]}, storage)

        assert raised.value.kind == "invalid_values"
        assert "outside the allowed source roots" in raised.value.message
        assert storage.get_source_config(1, "leaky") is None

    def test_a_path_under_an_allowed_root_is_accepted(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        games = tmp_path / "games"
        games.mkdir()

        create_source("my_roms", "roms", {"paths": [str(games)]}, storage)

        row = storage.get_source_config(1, "my_roms")
        assert row is not None
        assert row["config"]["paths"] == [str(games)]

    def test_one_escaping_entry_refuses_the_whole_list(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        games = tmp_path / "games"
        games.mkdir()

        with pytest.raises(SourceConfigError):
            create_source("mixed", "roms", {"paths": [str(games), "/etc"]}, storage)

        assert storage.get_source_config(1, "mixed") is None

    def test_a_symlink_planted_in_the_root_cannot_reach_out_of_it(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        escape = tmp_path / "games"
        escape.symlink_to("/etc")

        with pytest.raises(SourceConfigError) as raised:
            create_source("linked", "roms", {"paths": [str(escape)]}, storage)

        assert "outside the allowed source roots" in raised.value.message

    def test_a_traversal_is_refused_before_the_path_is_probed_for_existence(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The "not found" message is an oracle for anything the caller names."""
        with pytest.raises(SourceConfigError) as raised:
            create_source(
                "traversing", "roms", {"paths": [f"{tmp_path}/../secrets"]}, storage
            )

        assert "outside the allowed source roots" in raised.value.message
        assert "not found" not in raised.value.message

    def test_an_empty_path_list_is_refused(self, storage: StorageManager) -> None:
        with pytest.raises(SourceConfigError) as raised:
            create_source("empty", "roms", {"paths": []}, storage)

        assert raised.value.kind == "invalid_values"
        assert storage.get_source_config(1, "empty") is None

    def test_a_non_ascii_directory_under_the_root_is_accepted(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        games = tmp_path / "ポケモン Émulé"
        games.mkdir()

        create_source("unicode_roms", "roms", {"paths": [str(games)]}, storage)

        row = storage.get_source_config(1, "unicode_roms")
        assert row is not None
        assert row["config"]["paths"] == [str(games)]


class TwoSecretGamePlugin(FakeGamePlugin):
    """``fake_games`` plus a second sensitive field."""

    def get_config_schema(self) -> list[ConfigField]:
        return [
            *super().get_config_schema(),
            ConfigField(
                name="password", field_type=str, required=False, sensitive=True
            ),
        ]


class TestRedactCredentials:
    """Edge cases for the redaction the sync door's 400 log line depends on.

    That door validates with the secret layered on, so scrubbing the plugin's
    message is the only thing between a stored key and the log file.
    """

    def test_every_occurrence_of_every_secret_goes(self) -> None:
        """One pass has to cover both fields and repeated mentions."""
        redacted = redact_credentials(
            "GET ?key=abc123 rejected; retry with abc123 or pw-9",
            TwoSecretGamePlugin(),
            {"api_key": "abc123", "password": "pw-9", "label": "Games"},
        )

        assert redacted == (
            "GET ?key=[redacted] rejected; retry with [redacted] or [redacted]"
        )

    def test_an_empty_secret_is_not_a_match(self) -> None:
        """``str.replace("", ...)`` would splice the marker between every char."""
        message = "'api_key' is required"

        assert redact_credentials(message, FakeGamePlugin(), {"api_key": ""}) == message

    def test_an_absent_or_non_string_secret_is_skipped(self) -> None:
        """A YAML source can type its fields however it likes."""
        message = "'api_key' is required"

        assert redact_credentials(message, FakeGamePlugin(), {}) == message
        assert (
            redact_credentials(message, FakeGamePlugin(), {"api_key": 1234}) == message
        )

    def test_a_credential_bound_field_is_left_alone(self) -> None:
        """``url`` is bound to the credential, not one — the log wants it."""
        redacted = redact_credentials(
            "host http://sonarr.internal:9999 refused",
            FakeGamePlugin(),
            {"url": "http://sonarr.internal:9999"},
        )

        assert "http://sonarr.internal:9999" in redacted

    def test_a_multiline_secret_is_redacted_before_anything_escapes_it(self) -> None:
        """A pasted PEM-shaped value survives no reordering with the escaper."""
        redacted = redact_credentials(
            "rejected: -----KEY-----\nabc\n-----END-----",
            FakeGamePlugin(),
            {"api_key": "-----KEY-----\nabc\n-----END-----"},
        )

        assert redacted == "rejected: [redacted]"


@pytest.mark.usefixtures("_registry_with_rotating_doubles")
class TestRotatedCredentialSurvivesTheRealConfigAssemblyRegression:
    """Reported: a sync saved a rotated OAuth token under the plugin name.

    Bug: each rotating plugin's ``transform_config`` returned a fresh dict,
    dropping the injected ``_source_id``. The orphan survived deleting the
    source. Fix: ``transform_config`` is framework owned and restores it.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def _sync_a_rotating_source(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        """Sync a DB-backed source through the production resolution path."""
        storage.upsert_source_config(1, "work_games", plugin_name, {}, enabled=True)
        resolved = get_sync_handler("work_games", {}, storage)
        assert resolved is not None

        execute_sync(
            plugin=resolved.plugin,
            plugin_config=resolved.config,
            storage_manager=storage,
        )

    @pytest.mark.parametrize("plugin_name", ROTATING_PLUGINS)
    def test_the_token_lands_under_the_source_id(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        self._sync_a_rotating_source(plugin_name, storage)

        assert storage.get_credential(1, "work_games", "refresh_token") == ROTATED_TOKEN
        assert storage.get_credential(1, plugin_name, "refresh_token") is None

    @pytest.mark.parametrize("plugin_name", ROTATING_PLUGINS)
    def test_deleting_the_source_takes_the_token_with_it(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        self._sync_a_rotating_source(plugin_name, storage)

        delete_source("work_games", storage, {})

        assert storage.get_credentials_for_source(1, "work_games") == {}
        assert storage.get_credential(1, plugin_name, "refresh_token") is None

    @pytest.mark.parametrize("plugin_name", ROTATING_PLUGINS)
    def test_the_next_sync_resolves_the_rotated_token(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        """SECURITY.md's promise: the operator never reconnects by hand."""
        self._sync_a_rotating_source(plugin_name, storage)

        resolved = get_sync_handler("work_games", {}, storage)

        assert resolved is not None
        assert resolved.config["refresh_token"] == ROTATED_TOKEN

    def test_a_token_an_earlier_release_orphaned_stays_where_it_is(
        self, storage: StorageManager
    ) -> None:
        """Nothing migrates them, which is what SECURITY.md promises."""
        storage.save_credential(1, "gog", "refresh_token", "orphaned-by-an-old-sync")

        self._sync_a_rotating_source("gog", storage)

        assert storage.get_credential(1, "gog", "refresh_token") == (
            "orphaned-by-an-old-sync"
        )


@pytest.mark.usefixtures("_registry_with_fakes")
class TestRemovingASourceTakesItsStrandedTokenWithItRegression:
    """Reported: a token stranded under a plugin name outlived every source.

    Cause: deletion is keyed on the source id, and that row is not under one.
    Fix: the last source on a plugin takes it; a shared plugin keeps it.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.upsert_source_config(1, "work_games", "fake_games", {}, enabled=True)
        storage.save_credential(1, "fake_games", "api_key", "stranded-by-an-upgrade")
        return storage

    def test_the_last_source_on_the_plugin_takes_the_row_with_it(
        self, storage: StorageManager
    ) -> None:
        delete_source("work_games", storage, {"inputs": {}})

        assert storage.get_credential(1, "fake_games", "api_key") is None

    def test_a_sibling_on_the_same_plugin_keeps_the_row(
        self, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(1, "home_games", "fake_games", {}, enabled=True)

        delete_source("work_games", storage, {"inputs": {}})

        assert storage.get_credential(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_a_disabled_sibling_keeps_the_row(self, storage: StorageManager) -> None:
        """A disabled source is reconnected by enabling it, not by re-adding it."""
        storage.upsert_source_config(1, "home_games", "fake_games", {}, enabled=False)

        delete_source("work_games", storage, {"inputs": {}})

        assert storage.get_credential(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_a_disabled_yaml_sibling_keeps_the_row(
        self, storage: StorageManager
    ) -> None:
        """The YAML half answers who is left the same way the database half does."""
        config = {"inputs": {"home_games": {"plugin": "fake_games", "enabled": False}}}

        delete_source("work_games", storage, config)

        assert storage.get_credential(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_a_yaml_sibling_keeps_the_row(self, storage: StorageManager) -> None:
        """The database is half the source list, so a sweep reading it alone lies."""
        config = {"inputs": {"home_games": {"plugin": "fake_games", "enabled": True}}}

        delete_source("work_games", storage, config)

        assert storage.get_credential(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_a_source_named_after_the_plugin_keeps_its_own_credential(
        self, storage: StorageManager
    ) -> None:
        """That row is not stranded at all — it is that source's, live."""
        config = {"inputs": {"fake_games": {"plugin": "fake_books", "enabled": True}}}

        delete_source("work_games", storage, config)

        assert storage.get_credential(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_the_deleted_sources_own_credentials_still_go(
        self, storage: StorageManager
    ) -> None:
        storage.save_credential(1, "work_games", "api_key", "its-own")

        delete_source("work_games", storage, {"inputs": {}})

        assert storage.get_credentials_for_source(1, "work_games") == {}


class TestAPluginCannotDropAFrameworkConfigKey:
    """The guarantee the seven rotating plugins each broke independently."""

    def test_overriding_transform_config_is_refused_at_class_creation(self) -> None:
        with pytest.raises(TypeError, match="transform_fields"):

            class EighthPlugin(FakeBookPlugin):
                @classmethod
                def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
                    return {"path": raw_config.get("path")}

    @pytest.mark.usefixtures("_real_registry")
    def test_every_registered_plugin_keeps_the_source_id(self) -> None:
        for plugin in _discovered_plugins().values():
            transformed = type(plugin).transform_config({"_source_id": "my_source"})
            assert transformed["_source_id"] == "my_source"

    def test_the_plugins_own_fields_never_see_a_framework_key(self) -> None:
        """``transform_fields`` is handed the source's fields and nothing else."""
        seen: list[dict[str, Any]] = []

        class RecordingPlugin(FakeBookPlugin):
            @classmethod
            def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
                seen.append(dict(raw_fields))
                return dict(raw_fields)

        RecordingPlugin.transform_config({"path": "/data/books.csv", "_source_id": "x"})

        assert seen == [{"path": "/data/books.csv"}]

    def test_a_deeper_subclass_is_refused_the_same_way(self) -> None:
        """*arr plugins subclass an intermediate, so the guard must inherit."""

        class Intermediate(FakeBookPlugin):
            pass

        with pytest.raises(TypeError, match="transform_fields"):

            class Grandchild(Intermediate):
                @classmethod
                def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
                    return {}

    def test_transform_fields_cannot_forge_a_framework_key(self) -> None:
        """The framework's value wins, so a plugin cannot rename its source."""

        class ForgingPlugin(FakeBookPlugin):
            @classmethod
            def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
                return {**raw_fields, "_source_id": "hijacked"}

        transformed = ForgingPlugin.transform_config(
            {"path": "/data/books.csv", "_source_id": "my_books"}
        )

        assert transformed["_source_id"] == "my_books"

    def test_no_framework_key_appears_when_none_was_given(self) -> None:
        assert FakeCredentialPlugin.transform_config({"refresh_token": "  t  "}) == {
            "refresh_token": "t"
        }

    @pytest.mark.usefixtures("_real_registry")
    def test_every_registered_plugin_keeps_the_rotation_callback(self) -> None:
        """Steam re-transforms mid-``fetch``, so callables must survive too."""

        def callback(key: str, value: str) -> None:
            raise AssertionError("only identity is under test")

        for plugin in _discovered_plugins().values():
            transformed = type(plugin).transform_config(
                {"_on_credential_rotated": callback}
            )
            assert transformed["_on_credential_rotated"] is callback


def _somebody_elses_source(
    plugin: SourcePlugin, config: dict[str, Any] | None = None
) -> str:
    """A hijacking implementation bound by assignment rather than by ``def``."""
    return "somebody_elses_source"


class TestAPluginCannotRenameItsOwnSource:
    """The same guarantee on the other method that answers "what is this?"."""

    def test_overriding_get_source_identifier_is_refused_at_class_creation(
        self,
    ) -> None:
        with pytest.raises(TypeError, match="name property"):

            class RenamingPlugin(FakeBookPlugin):
                def get_source_identifier(
                    self, config: dict[str, Any] | None = None
                ) -> str:
                    return "somebody_elses_source"

    def test_a_deeper_subclass_is_refused_the_same_way(self) -> None:
        """*arr plugins subclass an intermediate, so the guard must inherit."""

        class Intermediate(FakeBookPlugin):
            pass

        with pytest.raises(TypeError, match="name property"):

            class Grandchild(Intermediate):
                def get_source_identifier(
                    self, config: dict[str, Any] | None = None
                ) -> str:
                    return "somebody_elses_source"

    def test_an_assigned_attribute_is_refused_like_a_def(self) -> None:
        """A guard keyed on ``def`` would miss the one-line assignment dodge."""
        with pytest.raises(TypeError, match="name property"):

            class AssigningPlugin(FakeBookPlugin):
                get_source_identifier = _somebody_elses_source

    def test_overriding_both_guarded_methods_is_still_refused(self) -> None:
        """One refusal per class creation, so the second must not mask the first."""
        with pytest.raises(TypeError):

            class GreedyPlugin(FakeBookPlugin):
                @classmethod
                def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
                    return {}

                def get_source_identifier(
                    self, config: dict[str, Any] | None = None
                ) -> str:
                    return "somebody_elses_source"

    def test_both_guarded_methods_are_final_for_a_plugin_authors_mypy(self) -> None:
        """A private plugin is not in this repo's mypy run; its author's is all."""
        assert SourcePlugin.get_source_identifier.__final__ is True
        assert SourcePlugin.__dict__["transform_config"].__final__ is True

    @pytest.mark.usefixtures("_real_registry")
    def test_every_registered_plugin_answers_with_the_source_id(self) -> None:
        for plugin in _discovered_plugins().values():
            assert plugin.get_source_identifier({"_source_id": "my_source"}) == (
                "my_source"
            )


@pytest.mark.usefixtures("_real_registry")
class TestARealPluginsItemsCarryTheSourceIdRegression:
    """Reported: a renamed Steam source's items were attributed to "steam".

    Bug: ``SteamPlugin.fetch`` re-transforms the config it was handed, and the
    plugin's transform dropped the injected ``_source_id``. Fix:
    ``transform_config`` is framework owned and restores it.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_items_are_attributed_to_the_source_not_the_plugin(
        self, owned_games: Mock, storage: StorageManager
    ) -> None:
        owned_games.return_value = [
            {"appid": 7, "name": "A Game", "playtime_forever": 120}
        ]
        storage.upsert_source_config(
            1,
            "work_games",
            "steam",
            {"api_key": "key", "steam_id": "76561198000000000"},
            enabled=True,
        )
        resolved = get_sync_handler("work_games", {}, storage)
        assert resolved is not None

        execute_sync(
            plugin=resolved.plugin,
            plugin_config=resolved.config,
            storage_manager=storage,
        )

        assert {item.source for item in storage.get_content_items()} == {"work_games"}


@pytest.mark.usefixtures("_registry_with_rotating_doubles")
class TestTheCredentialOwnerIsWhateverTheSourceIsCalled:
    """Source ids from YAML skip the create-source charset check."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _sync_yaml_source(source_id: str, storage: StorageManager) -> None:
        config = {"inputs": {source_id: {"plugin": "gog", "enabled": True}}}
        resolved = get_sync_handler(source_id, config, storage)
        assert resolved is not None

        execute_sync(
            plugin=resolved.plugin,
            plugin_config=resolved.config,
            storage_manager=storage,
        )

    @pytest.mark.parametrize(
        "source_id", ["my-gog", "Wörk Games 📚", "gog_2", "x" * 200]
    )
    def test_the_token_lands_under_the_id_verbatim(
        self, source_id: str, storage: StorageManager
    ) -> None:
        self._sync_yaml_source(source_id, storage)

        assert storage.get_credential(1, source_id, "refresh_token") == ROTATED_TOKEN
        assert storage.get_credential(1, "gog", "refresh_token") is None

    def test_ids_differing_only_in_case_do_not_share_a_token(
        self, storage: StorageManager
    ) -> None:
        """A NOCASE collation here would hand one source another's secret."""
        self._sync_yaml_source("Work_Games", storage)

        assert storage.get_credential(1, "work_games", "refresh_token") is None

    def test_an_empty_yaml_id_still_owns_its_token(
        self, storage: StorageManager
    ) -> None:
        """The one id whose owner and reported name part company.

        ``execute_sync`` reports progress under the display name for a falsy
        id, and a token following it there is orphaned.
        """
        self._sync_yaml_source("", storage)

        assert storage.get_credential(1, "", "refresh_token") == ROTATED_TOKEN
        assert storage.get_credential(1, "gog", "refresh_token") is None
