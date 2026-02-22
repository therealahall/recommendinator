"""Registry for discovering and managing enrichment providers."""

import importlib
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Any

from src.enrichment.provider_base import EnrichmentProvider
from src.models.content import ContentType

logger = logging.getLogger(__name__)


class EnrichmentRegistry:
    """Registry for enrichment providers.

    Discovers and manages providers from:
    1. Built-in providers in src/enrichment/providers/
    2. Private providers in plugins/private/enrichment/ (if exists)

    Uses singleton pattern - get instance via get_enrichment_registry() or
    EnrichmentRegistry.get_instance().

    Example usage:
        registry = get_enrichment_registry()
        registry.discover_providers()

        # Get a specific provider
        provider = registry.get_provider("tmdb")

        # Get all enabled providers based on config
        enabled = registry.get_enabled_providers(config)

        # Get providers for a content type
        movie_providers = registry.get_providers_by_content_type(ContentType.MOVIE)
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
        """Discover and register all available providers.

        Scans built-in and private provider directories for EnrichmentProvider
        subclasses and registers them.

        Args:
            force: If True, re-discover even if already done
        """
        if self._discovered and not force:
            return

        self._providers.clear()

        # 1. Discover built-in providers
        self._discover_builtin_providers()

        # 2. Discover private providers (if directory exists)
        self._discover_private_providers()

        self._discovered = True
        logger.info(
            "Discovered %d enrichment providers: %s",
            len(self._providers),
            list(self._providers.keys()),
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
                    self._register_providers_from_module(
                        module, f"builtin:{module_name}"
                    )
                except Exception as error:
                    logger.warning(
                        "Failed to load built-in enrichment provider module %s: %s",
                        module_name,
                        error,
                    )
        except ImportError as error:
            logger.warning("Failed to import enrichment providers package: %s", error)

    def _discover_private_providers(self) -> None:
        """Discover private providers from plugins/private/enrichment/."""
        private_path = Path("plugins/private/enrichment")

        if not private_path.exists():
            logger.debug("No private enrichment providers directory found")
            return

        # Ensure parent directories have __init__.py
        plugins_init = Path("plugins/__init__.py")
        private_init = Path("plugins/private/__init__.py")
        enrichment_init = private_path / "__init__.py"

        if not plugins_init.exists() or not private_init.exists():
            logger.debug(
                "plugins/__init__.py or plugins/private/__init__.py not found, "
                "skipping private enrichment providers"
            )
            return

        if not enrichment_init.exists():
            logger.debug(
                "plugins/private/enrichment/__init__.py not found, "
                "skipping private enrichment providers"
            )
            return

        # Add plugins directory to path if needed
        plugins_root = str(Path("plugins").parent.absolute())
        if plugins_root not in sys.path:
            sys.path.insert(0, plugins_root)

        for py_file in private_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            module_name = py_file.stem
            try:
                module = importlib.import_module(
                    f"plugins.private.enrichment.{module_name}"
                )
                self._register_providers_from_module(module, f"private:{module_name}")
            except Exception as error:
                logger.warning(
                    "Failed to load private enrichment provider %s: %s",
                    module_name,
                    error,
                )

    def _register_providers_from_module(self, module: Any, source: str) -> None:
        """Register all EnrichmentProvider subclasses from a module.

        Args:
            module: Imported module to scan
            source: Description of source for logging
        """
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)

            # Check if it's an EnrichmentProvider subclass (not the base class)
            if (
                isinstance(attr, type)
                and issubclass(attr, EnrichmentProvider)
                and attr is not EnrichmentProvider
            ):
                try:
                    provider_instance = attr()
                    self.register(provider_instance)
                    logger.debug(
                        "Registered enrichment provider %s from %s",
                        provider_instance.name,
                        source,
                    )
                except Exception as error:
                    logger.warning(
                        "Failed to instantiate enrichment provider %s from %s: %s",
                        attr_name,
                        source,
                        error,
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

    def unregister(self, name: str) -> bool:
        """Unregister a provider by name.

        Args:
            name: Provider name to unregister

        Returns:
            True if provider was found and removed, False otherwise
        """
        if name in self._providers:
            del self._providers[name]
            return True
        return False

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

    def get_providers_by_content_type(
        self, content_type: ContentType
    ) -> list[EnrichmentProvider]:
        """Get providers that can enrich a specific content type.

        Args:
            content_type: ContentType to filter by

        Returns:
            List of providers that support this content type
        """
        self.discover_providers()

        return [
            provider
            for provider in self._providers.values()
            if content_type in provider.content_types
        ]

    def list_provider_names(self) -> list[str]:
        """Get list of all registered provider names.

        Triggers discovery if not already done.

        Returns:
            Sorted list of provider names
        """
        self.discover_providers()
        return sorted(self._providers.keys())


def get_enrichment_registry() -> EnrichmentRegistry:
    """Get the global enrichment provider registry.

    Convenience function for accessing the singleton instance.

    Returns:
        The global EnrichmentRegistry instance
    """
    return EnrichmentRegistry.get_instance()
