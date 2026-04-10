"""Tests for sync source resolution."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.web.sync_sources import (
    get_available_sync_sources,
    get_sync_handler,
    resolve_inputs,
    validate_source_config,
)


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


@pytest.fixture()
def _registry_with_fakes() -> Iterator[None]:
    """Set up a registry with fake plugins for testing."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(FakeBookPlugin())
    registry.register(FakeGamePlugin())
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
    """Tests for get_available_sync_sources function."""

    def test_returns_enabled_sources(self) -> None:
        """Test that enabled sources are returned as SyncSourceInfo."""
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

        assert len(sources) == 1
        assert sources[0].id == "my_books"
        assert sources[0].display_name == "My Books"
        assert sources[0].plugin_display_name == "Fake Books"

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
