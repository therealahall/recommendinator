"""Plugin registry for discovering and managing source plugins."""

import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Any

from src.ingestion.plugin_base import SourcePlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Registry for source plugins.

    Discovers and manages plugins from:
    1. Built-in plugins in src/ingestion/sources/
    2. Private plugins in plugins/private/ (if exists)

    Uses singleton pattern - get instance via get_registry() or
    PluginRegistry.get_instance().

    Example usage:
        registry = get_registry()
        registry.discover_plugins()

        # Get a specific plugin
        plugin = registry.get_plugin("goodreads")

        # Get all enabled plugins based on config
        enabled = registry.get_enabled_plugins(config)

        # List all available plugins
        for name, plugin in registry.get_all_plugins().items():
            print(f"{name}: {plugin.display_name}")
    """

    _instance: "PluginRegistry | None" = None

    def __init__(self) -> None:
        """Initialize empty registry.

        Use get_instance() or get_registry() instead of direct instantiation.
        """
        self._plugins: dict[str, SourcePlugin] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> "PluginRegistry":
        """Get singleton registry instance.

        Returns:
            The global PluginRegistry instance
        """
        if cls._instance is None:
            cls._instance = PluginRegistry()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance.

        Primarily used for testing to ensure clean state.
        """
        cls._instance = None

    def discover_plugins(self, force: bool = False) -> None:
        """Discover and register all available plugins.

        Scans built-in and private plugin directories for SourcePlugin
        subclasses and registers them.

        Args:
            force: If True, re-discover even if already done
        """
        if self._discovered and not force:
            return

        self._plugins.clear()

        # 1. Discover built-in plugins
        self._discover_builtin_plugins()

        # 2. Discover private plugins (if directory exists)
        self._discover_private_plugins()

        self._discovered = True
        logger.info(
            f"Discovered {len(self._plugins)} plugins: {list(self._plugins.keys())}"
        )

    def _discover_builtin_plugins(self) -> None:
        """Discover built-in plugins from src/ingestion/sources/."""
        try:
            import src.ingestion.sources as sources_package

            package_path = Path(sources_package.__file__).parent

            for module_info in pkgutil.iter_modules([str(package_path)]):
                module_name = module_info.name
                if module_name.startswith("_"):
                    continue

                try:
                    module = importlib.import_module(
                        f"src.ingestion.sources.{module_name}"
                    )
                    self._register_plugins_from_module(module, f"builtin:{module_name}")
                except Exception as error:
                    logger.warning(
                        f"Failed to load built-in plugin module {module_name}: {error}"
                    )
        except ImportError as error:
            logger.warning(f"Failed to import sources package: {error}")

    def _discover_private_plugins(self) -> None:
        """Discover private plugins from plugins/private/."""
        private_path = Path("plugins/private")

        if not private_path.exists():
            logger.debug("No private plugins directory found")
            return

        # Ensure plugins directory has __init__.py
        plugins_init = private_path.parent / "__init__.py"
        private_init = private_path / "__init__.py"

        if not plugins_init.exists():
            logger.debug("plugins/__init__.py not found, skipping private plugins")
            return

        if not private_init.exists():
            logger.debug(
                "plugins/private/__init__.py not found, skipping private plugins"
            )
            return

        # Add plugins directory to path if needed
        plugins_root = str(private_path.parent.parent.absolute())
        if plugins_root not in sys.path:
            sys.path.insert(0, plugins_root)

        for py_file in private_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            module_name = py_file.stem
            try:
                module = importlib.import_module(f"plugins.private.{module_name}")
                self._register_plugins_from_module(module, f"private:{module_name}")
            except Exception as error:
                logger.warning(f"Failed to load private plugin {module_name}: {error}")

    def _register_plugins_from_module(self, module: Any, source: str) -> None:
        """Register all SourcePlugin subclasses from a module.

        Args:
            module: Imported module to scan
            source: Description of source for logging (e.g., "builtin:goodreads")
        """
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)

            # Check if it's a SourcePlugin subclass (not the base class itself)
            if (
                isinstance(attr, type)
                and issubclass(attr, SourcePlugin)
                and attr is not SourcePlugin
            ):
                try:
                    plugin_instance = attr()
                    self.register(plugin_instance)
                    logger.debug(
                        f"Registered plugin {plugin_instance.name} from {source}"
                    )
                except Exception as error:
                    logger.warning(
                        f"Failed to instantiate plugin {attr_name} from {source}: {error}"
                    )

    def register(self, plugin: SourcePlugin) -> None:
        """Register a plugin instance.

        Args:
            plugin: Plugin instance to register

        Raises:
            ValueError: If plugin with same name already registered
        """
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' already registered")

        self._plugins[plugin.name] = plugin
        logger.debug(f"Registered plugin: {plugin.name} ({plugin.display_name})")

    def unregister(self, name: str) -> bool:
        """Unregister a plugin by name.

        Args:
            name: Plugin name to unregister

        Returns:
            True if plugin was found and removed, False otherwise
        """
        if name in self._plugins:
            del self._plugins[name]
            return True
        return False

    def get_plugin(self, name: str) -> SourcePlugin | None:
        """Get a plugin by name.

        Triggers discovery if not already done.

        Args:
            name: Plugin name

        Returns:
            Plugin instance or None if not found
        """
        self.discover_plugins()
        return self._plugins.get(name)

    def get_all_plugins(self) -> dict[str, SourcePlugin]:
        """Get all registered plugins.

        Triggers discovery if not already done.

        Returns:
            Dict mapping plugin names to instances
        """
        self.discover_plugins()
        return dict(self._plugins)

    def get_enabled_plugins(self, config: dict[str, Any]) -> list[SourcePlugin]:
        """Get plugins that are enabled in config.

        A plugin is considered enabled if config has:
        inputs.<plugin_name>.enabled = true

        Args:
            config: Full application config

        Returns:
            List of enabled plugin instances
        """
        self.discover_plugins()

        inputs_config = config.get("inputs", {})
        enabled_plugins = []

        for name, plugin in self._plugins.items():
            plugin_config = inputs_config.get(name, {})

            # Handle both dict config and list config (for generic plugins)
            if isinstance(plugin_config, dict):
                if plugin_config.get("enabled", False):
                    enabled_plugins.append(plugin)
            elif isinstance(plugin_config, list):
                # For plugins with multiple instances (generic_csv, etc.)
                # Check if any instance is enabled
                for instance_config in plugin_config:
                    if instance_config.get("enabled", False):
                        enabled_plugins.append(plugin)
                        break

        return enabled_plugins

    def get_plugins_by_content_type(
        self, content_type: "ContentType"
    ) -> list[SourcePlugin]:
        """Get plugins that provide a specific content type.

        Args:
            content_type: ContentType to filter by

        Returns:
            List of plugins that can provide this content type
        """
        self.discover_plugins()

        return [
            plugin
            for plugin in self._plugins.values()
            if content_type in plugin.content_types
        ]

    def list_plugin_names(self) -> list[str]:
        """Get list of all registered plugin names.

        Triggers discovery if not already done.

        Returns:
            Sorted list of plugin names
        """
        self.discover_plugins()
        return sorted(self._plugins.keys())


# Import ContentType for type hint (avoid circular import)
from src.models.content import ContentType  # noqa: E402


def get_registry() -> PluginRegistry:
    """Get the global plugin registry.

    Convenience function for accessing the singleton instance.

    Returns:
        The global PluginRegistry instance
    """
    return PluginRegistry.get_instance()
