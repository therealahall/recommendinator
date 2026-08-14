"""Plugin registry for discovering and managing source plugins."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import sys
import threading
from pathlib import Path
from typing import Any

from src.ingestion.plugin_base import SourcePlugin

logger = logging.getLogger(__name__)

# Serialises the singleton build and the publish ending a discovery pass.
# Threadpool handlers hit a cold registry together, and rebuilding ``_plugins``
# in place let one pass wipe the other's work, then flag the registry
# discovered over a permanently partial map.
_registry_lock = threading.Lock()


class PluginRegistry:
    """Registry for source plugins.

    Discovers and manages plugins from:
    1. Built-in plugins in src/ingestion/sources/
    2. Private plugins in private/plugins/ (if exists)

    Uses singleton pattern - get instance via get_registry() or
    PluginRegistry.get_instance().

    Example usage:
        registry = get_registry()
        registry.discover_plugins()

        # Get a specific plugin
        plugin = registry.get_plugin("goodreads_csv")

        # List all available plugins
        for name, plugin in registry.get_all_plugins().items():
            print(f"{name}: {plugin.display_name}")
    """

    _instance: PluginRegistry | None = None

    def __init__(self) -> None:
        """Initialize empty registry.

        Use get_instance() or get_registry() instead of direct instantiation.
        """
        self._plugins: dict[str, SourcePlugin] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> PluginRegistry:
        """Get singleton registry instance.

        Returns:
            The global PluginRegistry instance
        """
        with _registry_lock:
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
        subclasses and registers them. A pass accumulates into a registry of its
        own and swaps the finished map in, so ``self._plugins`` never holds a
        half-built one.

        The scan is deliberately not held under ``_registry_lock``: importing a
        module and constructing a plugin both run arbitrary code from
        ``private/plugins/``, and a plugin reaching back into ``get_registry()``
        would block on a lock its own caller holds, wedging the process with no
        exception to catch. Such a plugin now gets its registry back rather than
        blocking. The cost is that two cold passes each do the work; both
        publish a complete map and the later swap wins.

        Args:
            force: If True, re-discover even if already done
        """
        with _registry_lock:
            if self._discovered and not force:
                return

        staging = PluginRegistry()
        staging._discover_builtin_plugins()
        staging._discover_private_plugins()
        plugins = staging._plugins

        with _registry_lock:
            self._plugins = plugins
            self._discovered = True

        logger.info("Discovered %d plugins: %s", len(plugins), list(plugins.keys()))

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
                        "Failed to load built-in plugin module %s: %s",
                        module_name,
                        error,
                    )
        except ImportError as error:
            logger.warning("Failed to import sources package: %s", error)

    def _discover_private_plugins(self) -> None:
        """Discover private plugins from private/plugins/."""
        # Find project root (parent of src/)
        project_root = Path(__file__).parent.parent.parent
        private_path = project_root / "private" / "plugins"

        if not private_path.exists():
            logger.debug("No private plugins directory found at %s", private_path)
            return

        # Ensure private directory has __init__.py files
        private_init = private_path.parent / "__init__.py"
        plugins_init = private_path / "__init__.py"

        if not private_init.exists():
            logger.debug("private/__init__.py not found, skipping private plugins")
            return

        if not plugins_init.exists():
            logger.debug(
                "private/plugins/__init__.py not found, skipping private plugins"
            )
            return

        # Add project root to path if needed (so 'private.plugins' can be imported)
        project_root_str = str(project_root.absolute())
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)

        for py_file in private_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            module_name = py_file.stem
            try:
                module = importlib.import_module(f"private.plugins.{module_name}")
                self._register_plugins_from_module(module, f"private:{module_name}")
            except Exception as error:
                logger.warning(
                    "Failed to load private plugin %s: %s", module_name, error
                )

    def _register_plugins_from_module(self, module: Any, source: str) -> None:
        """Register all SourcePlugin subclasses from a module.

        Args:
            module: Imported module to scan
            source: Description of source for logging (e.g., "builtin:goodreads_csv")
        """
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)

            # Check if it's a concrete SourcePlugin subclass
            if (
                isinstance(attr, type)
                and issubclass(attr, SourcePlugin)
                and not inspect.isabstract(attr)
            ):
                try:
                    plugin_instance = attr()
                    self.register(plugin_instance)
                    logger.debug(
                        "Registered plugin %s from %s", plugin_instance.name, source
                    )
                except Exception as error:
                    logger.warning(
                        "Failed to instantiate plugin %s from %s: %s",
                        attr_name,
                        source,
                        error,
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
        logger.debug("Registered plugin: %s (%s)", plugin.name, plugin.display_name)

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


def get_registry() -> PluginRegistry:
    """Get the global plugin registry.

    Convenience function for accessing the singleton instance.

    Returns:
        The global PluginRegistry instance
    """
    return PluginRegistry.get_instance()
