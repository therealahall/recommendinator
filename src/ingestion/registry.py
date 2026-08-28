from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import threading
from pathlib import Path
from typing import Any

from src.ingestion.plugin_base import SourcePlugin
from src.utils.private_plugins import private_plugin_module_names
from src.utils.text import exception_for_log, sanitize_for_log

logger = logging.getLogger(__name__)

# Serialises the singleton build and the publish ending a discovery pass.
# Threadpool handlers hit a cold registry together, and rebuilding ``_plugins``
# in place let one pass wipe the other's work, then flag the registry
# discovered over a permanently partial map.
_registry_lock = threading.Lock()


class PluginRegistry:
    _instance: PluginRegistry | None = None

    def __init__(self) -> None:
        self._plugins: dict[str, SourcePlugin] = {}
        self._import_errors: dict[str, str] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> PluginRegistry:
        with _registry_lock:
            if cls._instance is None:
                cls._instance = PluginRegistry()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def discover_plugins(self, force: bool = False) -> None:
        """The scan is deliberately not held under ``_registry_lock``: importing a
        module and constructing a plugin both run arbitrary code from
        ``private/plugins/``, and a plugin reaching back into ``get_registry()``
        would block on a lock its own caller holds.
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
        self._import_errors[module_name] = f"{type(error).__name__}: {error}"

    def _discover_builtin_plugins(self) -> None:
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
        """The enrichment registry scans this directory too, so a failure names the
        module and this scan rather than a source plugin: it may have held a provider.
        """
        project_root = Path(__file__).parent.parent.parent

        for module_name in private_plugin_module_names(project_root, "source plugins"):
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
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)

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
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' already registered")

        self._plugins[plugin.name] = plugin
        logger.debug("Registered plugin: %s (%s)", plugin.name, plugin.display_name)

    def get_plugin(self, name: str) -> SourcePlugin | None:
        self.discover_plugins()
        return self._plugins.get(name)

    def get_all_plugins(self) -> dict[str, SourcePlugin]:
        self.discover_plugins()
        return dict(self._plugins)

    def get_import_errors(self) -> dict[str, str]:
        self.discover_plugins()
        return dict(self._import_errors)


def get_registry() -> PluginRegistry:
    return PluginRegistry.get_instance()
