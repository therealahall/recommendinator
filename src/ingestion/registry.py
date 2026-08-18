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
from src.utils.text import exception_for_log, sanitize_for_log

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
        plugin = registry.get_plugin("goodreads_rss")

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
        self._import_errors: dict[str, str] = {}
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
        import_errors = staging._import_errors

        with _registry_lock:
            self._plugins = plugins
            self._import_errors = import_errors
            self._discovered = True

        logger.info("Discovered %d plugins: %s", len(plugins), list(plugins.keys()))
        if import_errors:
            logger.warning(
                "%d plugin module(s) failed to load: %s",
                len(import_errors),
                sanitize_for_log(", ".join(sorted(import_errors))),
            )

    def _record_import_failure(self, module_name: str, error: BaseException) -> None:
        """Retain what the catch used to drop.

        A plugin whose module raised is otherwise indistinguishable from one
        that was never installed, so both interfaces blamed the operator's
        config for a stale container.
        """
        self._import_errors[module_name] = f"{type(error).__name__}: {error}"

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
                    self._register_plugins_from_module(module, module_name, "builtin")
                except Exception as error:
                    self._record_import_failure(module_name, error)
                    logger.warning(
                        "Failed to load built-in plugin module %s: %s",
                        sanitize_for_log(module_name),
                        exception_for_log(error),
                    )
        except ImportError as error:
            # Filed under the package: the operator gets the same
            # module-to-reason answer whether one plugin died or all of them.
            self._record_import_failure("src.ingestion.sources", error)
            logger.warning(
                "Failed to import sources package: %s", exception_for_log(error)
            )

    def _discover_private_plugins(self) -> None:
        """Discover private plugins from private/plugins/.

        The enrichment registry scans this directory too, so a failure names the
        module and this scan rather than a source plugin: it may have held a
        provider.
        """
        # Find project root (parent of src/)
        project_root = Path(__file__).parent.parent.parent
        private_path = project_root / "private" / "plugins"

        if not private_path.exists():
            logger.debug(
                "No private plugins directory at %s, skipping the scan for "
                "source plugins",
                private_path,
            )
            return

        # Ensure private directory has __init__.py files
        private_init = private_path.parent / "__init__.py"
        plugins_init = private_path / "__init__.py"

        if not private_init.exists():
            logger.debug(
                "private/__init__.py not found, skipping the scan for source plugins"
            )
            return

        if not plugins_init.exists():
            logger.debug(
                "private/plugins/__init__.py not found, skipping the scan for "
                "source plugins"
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
                self._register_plugins_from_module(module, module_name, "private")
            except Exception as error:
                self._record_import_failure(module_name, error)
                logger.warning(
                    "Failed to import private module %s while scanning for "
                    "source plugins: %s",
                    sanitize_for_log(module_name),
                    exception_for_log(error),
                )

    def _register_plugins_from_module(
        self, module: Any, module_name: str, origin: str
    ) -> None:
        """Register all SourcePlugin subclasses from a module.

        Args:
            module: Imported module to scan
            module_name: The module's own name, which keys a load failure
            origin: Where it was found ("builtin" or "private")
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
                        "Registered plugin %s from %s:%s",
                        plugin_instance.name,
                        origin,
                        module_name,
                    )
                except Exception as error:
                    self._record_import_failure(module_name, error)
                    logger.warning(
                        "Failed to instantiate plugin %s from %s:%s: %s",
                        attr_name,
                        origin,
                        sanitize_for_log(module_name),
                        exception_for_log(error),
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

    def get_import_errors(self) -> dict[str, str]:
        """Every module the last pass could not load, mapped to why.

        Triggers discovery if not already done.

        Returns:
            Dict mapping module name to the exception that lost it
        """
        self.discover_plugins()
        return dict(self._import_errors)


def get_registry() -> PluginRegistry:
    """Get the global plugin registry.

    Convenience function for accessing the singleton instance.

    Returns:
        The global PluginRegistry instance
    """
    return PluginRegistry.get_instance()
