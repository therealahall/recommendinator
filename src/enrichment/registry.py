"""Registry for discovering and managing enrichment providers."""

import importlib
import inspect
import logging
import pkgutil
import threading
from pathlib import Path
from typing import Any

from src.enrichment.provider_base import EnrichmentProvider
from src.utils.private_plugins import private_plugin_module_names
from src.utils.text import exception_for_log, sanitize_for_log

logger = logging.getLogger(__name__)

# Serialises the singleton build and the publish ending a discovery pass, for
# the reason spelled out on ``src.ingestion.registry``: two concurrent passes
# rebuilding in place left the process serving a partial map for good.
_registry_lock = threading.Lock()


class EnrichmentRegistry:
    """The enrichment providers, discovered rather than listed.

    Built-in ones come from ``src/enrichment/providers/``, private ones from
    ``private/plugins/``: shipping a provider is dropping a folder in, never
    editing this file. Get the singleton from :func:`get_enrichment_registry`.
    """

    _instance: "EnrichmentRegistry | None" = None

    def __init__(self) -> None:
        """Initialize empty registry.

        Use get_instance() or get_enrichment_registry() instead of direct
        instantiation.
        """
        self._providers: dict[str, EnrichmentProvider] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> "EnrichmentRegistry":
        """Get singleton registry instance.

        Returns:
            The global EnrichmentRegistry instance
        """
        with _registry_lock:
            if cls._instance is None:
                cls._instance = EnrichmentRegistry()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance.

        Primarily used for testing to ensure clean state.
        """
        cls._instance = None

    def discover_providers(self, force: bool = False) -> None:
        """Register every provider the two directories hold.

        A pass fills a registry of its own and swaps the finished map in, and
        scans outside ``_registry_lock``: it constructs code this repo does not
        control. :meth:`PluginRegistry.discover_plugins` spells out both.
        """
        with _registry_lock:
            if self._discovered and not force:
                return

        staging = EnrichmentRegistry()
        staging._discover_builtin_providers()
        staging._discover_private_providers()
        providers = staging._providers

        with _registry_lock:
            self._providers = providers
            self._discovered = True

        logger.info(
            "Discovered %d enrichment providers: %s", len(providers), list(providers)
        )

    def _discover_builtin_providers(self) -> None:
        """Discover built-in providers from src/enrichment/providers/."""
        try:
            import src.enrichment.providers as providers_package

            package_path = Path(providers_package.__file__).parent

            for module_info in pkgutil.iter_modules([str(package_path)]):
                module_name = module_info.name
                if module_name.startswith("_"):
                    continue

                try:
                    module = importlib.import_module(
                        f"src.enrichment.providers.{module_name}"
                    )
                    self._register_providers_from_module(module, module_name, "builtin")
                except Exception as error:
                    logger.warning(
                        "Failed to load built-in provider module %s: %s",
                        sanitize_for_log(module_name),
                        exception_for_log(error),
                    )
        except ImportError as error:
            logger.warning(
                "Failed to import enrichment providers package: %s",
                exception_for_log(error),
            )

    def _discover_private_providers(self) -> None:
        """Discover private providers from private/plugins/.

        The same directory the source-plugin registry scans: what a private
        module holds decides which registry keeps it, not where it sits. Both
        registries import every module here, so a failure names the module and
        the scan that hit it rather than claiming a provider was lost.
        """
        project_root = Path(__file__).parent.parent.parent

        for module_name in private_plugin_module_names(
            project_root, "enrichment providers"
        ):
            try:
                module = importlib.import_module(f"private.plugins.{module_name}")
                self._register_providers_from_module(module, module_name, "private")
            except Exception as error:
                logger.warning(
                    "Failed to import private module %s while scanning for "
                    "enrichment providers: %s",
                    sanitize_for_log(module_name),
                    exception_for_log(error),
                )

    def _register_providers_from_module(
        self, module: Any, module_name: str, origin: str
    ) -> None:
        """Register all EnrichmentProvider subclasses from a module.

        Args:
            module: Imported module to scan
            module_name: The module's own name, as the log names it
            origin: Where it was found ("builtin" or "private")
        """
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)

            if (
                isinstance(attr, type)
                and issubclass(attr, EnrichmentProvider)
                and not inspect.isabstract(attr)
            ):
                try:
                    provider_instance = attr()
                    self.register(provider_instance)
                    logger.debug(
                        "Registered enrichment provider %s from %s:%s",
                        provider_instance.name,
                        origin,
                        module_name,
                    )
                except Exception as error:
                    logger.warning(
                        "Failed to instantiate enrichment provider %s from %s:%s: %s",
                        attr_name,
                        origin,
                        sanitize_for_log(module_name),
                        exception_for_log(error),
                    )

    def register(self, provider: EnrichmentProvider) -> None:
        """Register a provider instance.

        Args:
            provider: Provider instance to register

        Raises:
            ValueError: If provider with same name already registered
        """
        if provider.name in self._providers:
            raise ValueError(
                f"Enrichment provider '{provider.name}' already registered"
            )

        self._providers[provider.name] = provider
        logger.debug(
            "Registered enrichment provider: %s (%s)",
            provider.name,
            provider.display_name,
        )

    def get_provider(self, name: str) -> EnrichmentProvider | None:
        """Get a provider by name.

        Triggers discovery if not already done.

        Args:
            name: Provider name

        Returns:
            Provider instance or None if not found
        """
        self.discover_providers()
        return self._providers.get(name)

    def get_all_providers(self) -> dict[str, EnrichmentProvider]:
        """Get all registered providers.

        Triggers discovery if not already done.

        Returns:
            Dict mapping provider names to instances
        """
        self.discover_providers()
        return dict(self._providers)

    def get_enabled_providers(self, config: dict[str, Any]) -> list[EnrichmentProvider]:
        """Get providers that are enabled in config.

        A provider is considered enabled if config has:
        enrichment.providers.<provider_name>.enabled = true

        Args:
            config: Full application config

        Returns:
            List of enabled provider instances
        """
        self.discover_providers()

        enrichment_config = config.get("enrichment", {})
        providers_config = enrichment_config.get("providers", {})
        enabled_providers = []

        for name, provider in self._providers.items():
            provider_config = providers_config.get(name, {})
            if provider_config.get("enabled", False):
                enabled_providers.append(provider)

        return enabled_providers


def get_enrichment_registry() -> EnrichmentRegistry:
    """Get the global enrichment provider registry.

    Convenience function for accessing the singleton instance.

    Returns:
        The global EnrichmentRegistry instance
    """
    return EnrichmentRegistry.get_instance()
