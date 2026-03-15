"""Tests for plugin registry."""

import logging
import types
from collections.abc import Iterator
from typing import Any

import pytest

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.ingestion.sources.arr_base import ArrPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class FakeBookPlugin(SourcePlugin):
    """Fake book plugin for registry testing."""

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

    def validate_config(self, config: dict[str, Any]) -> list[str]:
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
    """Fake game plugin for registry testing."""

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

    def validate_config(self, config: dict[str, Any]) -> list[str]:
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
def clean_registry() -> PluginRegistry:
    """Create a fresh registry for each test (not singleton)."""
    return PluginRegistry()


class TestPluginRegistry:
    """Tests for PluginRegistry."""

    def test_register_plugin(self, clean_registry: PluginRegistry) -> None:
        """Test registering a plugin."""
        clean_registry._discovered = True  # Prevent auto-discovery
        plugin = FakeBookPlugin()
        clean_registry.register(plugin)

        assert clean_registry.get_plugin("fake_books") is plugin

    def test_register_duplicate_raises(self, clean_registry: PluginRegistry) -> None:
        """Test that registering duplicate plugin name raises ValueError."""
        plugin = FakeBookPlugin()
        clean_registry.register(plugin)

        with pytest.raises(ValueError, match="already registered"):
            clean_registry.register(FakeBookPlugin())

    def test_get_plugin_not_found(self, clean_registry: PluginRegistry) -> None:
        """Test getting a non-existent plugin returns None."""
        clean_registry._discovered = True

        assert clean_registry.get_plugin("nonexistent") is None

    def test_get_all_plugins(self, clean_registry: PluginRegistry) -> None:
        """Test getting all registered plugins."""
        clean_registry._discovered = True

        clean_registry.register(FakeBookPlugin())
        clean_registry.register(FakeGamePlugin())

        all_plugins = clean_registry.get_all_plugins()

        assert len(all_plugins) == 2
        assert "fake_books" in all_plugins
        assert "fake_games" in all_plugins

    def test_get_all_plugins_returns_copy(self, clean_registry: PluginRegistry) -> None:
        """Test that get_all_plugins returns a copy, not the internal dict."""
        clean_registry._discovered = True
        clean_registry.register(FakeBookPlugin())

        plugins_copy = clean_registry.get_all_plugins()
        plugins_copy["injected"] = FakeGamePlugin()  # type: ignore[assignment]

        # Original should not be affected
        assert "injected" not in clean_registry.get_all_plugins()

    def test_unregister_plugin(self, clean_registry: PluginRegistry) -> None:
        """Test unregistering a plugin."""
        clean_registry._discovered = True  # Prevent auto-discovery
        clean_registry.register(FakeBookPlugin())
        assert clean_registry.get_plugin("fake_books") is not None

        result = clean_registry.unregister("fake_books")
        assert result is True
        assert clean_registry.get_plugin("fake_books") is None

    def test_unregister_nonexistent(self, clean_registry: PluginRegistry) -> None:
        """Test unregistering a non-existent plugin returns False."""
        result = clean_registry.unregister("nonexistent")
        assert result is False

    def test_list_plugin_names(self, clean_registry: PluginRegistry) -> None:
        """Test listing plugin names returns sorted list."""
        clean_registry._discovered = True

        clean_registry.register(FakeGamePlugin())
        clean_registry.register(FakeBookPlugin())

        names = clean_registry.list_plugin_names()

        assert names == ["fake_books", "fake_games"]  # Sorted

    def test_get_enabled_plugins_dict_config(
        self, clean_registry: PluginRegistry
    ) -> None:
        """Test getting enabled plugins with named instance config."""
        clean_registry._discovered = True
        clean_registry.register(FakeBookPlugin())
        clean_registry.register(FakeGamePlugin())

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

        enabled = clean_registry.get_enabled_plugins(config)

        assert len(enabled) == 1
        assert enabled[0].name == "fake_books"

    def test_get_enabled_plugins_multiple_instances_same_plugin(
        self, clean_registry: PluginRegistry
    ) -> None:
        """Test that multiple instances of the same plugin return it once."""
        clean_registry._discovered = True
        clean_registry.register(FakeBookPlugin())

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

        enabled = clean_registry.get_enabled_plugins(config)

        assert len(enabled) == 1
        assert enabled[0].name == "fake_books"

    def test_get_enabled_plugins_empty_config(
        self, clean_registry: PluginRegistry
    ) -> None:
        """Test getting enabled plugins with no config returns empty."""
        clean_registry._discovered = True
        clean_registry.register(FakeBookPlugin())

        enabled = clean_registry.get_enabled_plugins({})

        assert len(enabled) == 0

    def test_get_enabled_plugins_missing_plugin_field(
        self, clean_registry: PluginRegistry
    ) -> None:
        """Test that entries without a plugin field are skipped."""
        clean_registry._discovered = True
        clean_registry.register(FakeBookPlugin())

        config = {
            "inputs": {
                "broken_entry": {
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        enabled = clean_registry.get_enabled_plugins(config)

        assert len(enabled) == 0

    def test_get_plugins_by_content_type(self, clean_registry: PluginRegistry) -> None:
        """Test filtering plugins by content type."""
        clean_registry._discovered = True
        clean_registry.register(FakeBookPlugin())
        clean_registry.register(FakeGamePlugin())

        book_plugins = clean_registry.get_plugins_by_content_type(ContentType.BOOK)
        game_plugins = clean_registry.get_plugins_by_content_type(
            ContentType.VIDEO_GAME
        )
        movie_plugins = clean_registry.get_plugins_by_content_type(ContentType.MOVIE)

        assert len(book_plugins) == 1
        assert book_plugins[0].name == "fake_books"
        assert len(game_plugins) == 1
        assert game_plugins[0].name == "fake_games"
        assert len(movie_plugins) == 0

    def test_discover_does_not_rediscover(self, clean_registry: PluginRegistry) -> None:
        """Test that discover_plugins only runs once unless forced."""
        clean_registry.discover_plugins()
        initial_count = len(clean_registry.get_all_plugins())

        # Call again - should not change
        clean_registry.discover_plugins()
        assert len(clean_registry.get_all_plugins()) == initial_count

    def test_discover_force_rediscovers(self, clean_registry: PluginRegistry) -> None:
        """Test that force=True triggers re-discovery."""
        clean_registry.discover_plugins()

        # Manually register one more
        clean_registry.register(FakeBookPlugin())
        count_with_extra = len(clean_registry.get_all_plugins())

        # Force re-discover - should lose the manually added one
        clean_registry.discover_plugins(force=True)
        assert len(clean_registry.get_all_plugins()) < count_with_extra


class TestPluginRegistrySingleton:
    """Tests for singleton pattern."""

    def test_get_instance_returns_same(self) -> None:
        """Test that get_instance returns the same object."""
        PluginRegistry.reset_instance()

        instance_one = PluginRegistry.get_instance()
        instance_two = PluginRegistry.get_instance()

        assert instance_one is instance_two

        # Cleanup
        PluginRegistry.reset_instance()

    def test_reset_instance(self) -> None:
        """Test that reset_instance creates a fresh instance."""
        PluginRegistry.reset_instance()

        instance_one = PluginRegistry.get_instance()
        PluginRegistry.reset_instance()
        instance_two = PluginRegistry.get_instance()

        assert instance_one is not instance_two

        # Cleanup
        PluginRegistry.reset_instance()


class TestPluginRegistryModuleDiscovery:
    """Tests for module-based plugin discovery."""

    def test_register_plugins_from_module(self, clean_registry: PluginRegistry) -> None:
        """Test discovering plugins from a module object."""
        fake_module = types.ModuleType("fake_module")
        fake_module.FakeBookPlugin = FakeBookPlugin  # type: ignore[attr-defined]
        fake_module.FakeGamePlugin = FakeGamePlugin  # type: ignore[attr-defined]
        fake_module.not_a_plugin = "just a string"  # type: ignore[attr-defined]

        clean_registry._discovered = True  # Prevent auto-discovery
        clean_registry._register_plugins_from_module(fake_module, "test")

        all_plugins = clean_registry.get_all_plugins()
        assert "fake_books" in all_plugins
        assert "fake_games" in all_plugins

    def test_register_plugins_skips_base_class(
        self, clean_registry: PluginRegistry
    ) -> None:
        """Test that SourcePlugin base class itself is not registered."""
        fake_module = types.ModuleType("fake_module")
        fake_module.SourcePlugin = SourcePlugin  # type: ignore[attr-defined]
        fake_module.FakeBookPlugin = FakeBookPlugin  # type: ignore[attr-defined]

        clean_registry._discovered = True  # Prevent auto-discovery
        clean_registry._register_plugins_from_module(fake_module, "test")

        all_plugins = clean_registry.get_all_plugins()
        assert len(all_plugins) == 1
        assert "fake_books" in all_plugins

    def test_register_plugins_skips_private_attrs(
        self, clean_registry: PluginRegistry
    ) -> None:
        """Test that attributes starting with _ are skipped."""
        fake_module = types.ModuleType("fake_module")
        fake_module._PrivatePlugin = FakeBookPlugin  # type: ignore[attr-defined]

        clean_registry._discovered = True  # Prevent auto-discovery
        clean_registry._register_plugins_from_module(fake_module, "test")

        assert len(clean_registry.get_all_plugins()) == 0


class TestPluginRegistryAbstractClassRegression:
    """Regression tests for abstract class handling in plugin discovery.

    Reported in: https://github.com/therealahall/recommendinator/issues/7
    """

    def test_skips_abstract_intermediate_class_regression(
        self, clean_registry: PluginRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that abstract intermediate base classes are skipped silently.

        Bug: The registry tried to instantiate ArrPlugin (an abstract base class
        for Radarr/Sonarr) because it only filtered out SourcePlugin itself.
        This caused 'Can't instantiate abstract class ArrPlugin' warnings on
        every module that imported or defined ArrPlugin.

        Root cause: _register_plugins_from_module checked `attr is not SourcePlugin`
        but didn't check for other abstract classes in the hierarchy.

        Fix: Use inspect.isabstract() to skip any abstract class, not just SourcePlugin.

        Reported in: https://github.com/therealahall/recommendinator/issues/7
        """
        fake_module = types.ModuleType("fake_module")
        fake_module.ArrPlugin = ArrPlugin  # type: ignore[attr-defined]
        fake_module.FakeBookPlugin = FakeBookPlugin  # type: ignore[attr-defined]

        clean_registry._discovered = True  # Prevent auto-discovery

        with caplog.at_level(logging.WARNING, logger="src.ingestion.registry"):
            clean_registry._register_plugins_from_module(fake_module, "test")

        all_plugins = clean_registry.get_all_plugins()
        assert (
            "fake_books" in all_plugins
        ), f"Expected fake_books to be registered, got: {list(all_plugins.keys())}"
        assert (
            len(all_plugins) == 1
        ), f"Expected exactly 1 plugin, got {len(all_plugins)}: {list(all_plugins.keys())}"
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert (
            warning_records == []
        ), f"Expected no warnings, got: {[r.message for r in warning_records]}"

    def test_skips_module_with_only_abstract_classes_regression(
        self, clean_registry: PluginRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that a module containing only abstract classes registers nothing silently.

        Edge case for issue #7: arr_base.py contains only ArrPlugin (abstract)
        and no concrete plugins. The registry should register zero plugins and
        emit zero warnings.

        Reported in: https://github.com/therealahall/recommendinator/issues/7
        """
        fake_module = types.ModuleType("fake_module")
        fake_module.ArrPlugin = ArrPlugin  # type: ignore[attr-defined]
        fake_module.SourcePlugin = SourcePlugin  # type: ignore[attr-defined]

        clean_registry._discovered = True  # Prevent auto-discovery

        with caplog.at_level(logging.WARNING, logger="src.ingestion.registry"):
            clean_registry._register_plugins_from_module(fake_module, "test")

        all_plugins = clean_registry.get_all_plugins()
        assert (
            len(all_plugins) == 0
        ), f"Expected no plugins, got: {list(all_plugins.keys())}"
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert (
            warning_records == []
        ), f"Expected no warnings, got: {[r.message for r in warning_records]}"
