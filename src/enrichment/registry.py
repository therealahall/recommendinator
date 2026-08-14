"""Registry for the enrichment providers."""

import logging
import threading
from typing import Any

from src.enrichment.provider_base import EnrichmentProvider
from src.enrichment.providers.openlibrary.openlibrary import OpenLibraryProvider
from src.enrichment.providers.rawg.rawg import RAWGProvider
from src.enrichment.providers.tmdb.tmdb import TMDBProvider
from src.models.content import ContentType

logger = logging.getLogger(__name__)

# Serialises the singleton build and the publish ending a discovery pass, for
# the reason spelled out on ``src.ingestion.registry``: two concurrent passes
# rebuilding in place left the process serving a partial map for good.
_registry_lock = threading.Lock()


class EnrichmentRegistry:
    """Registry for the enrichment providers in ``src/enrichment/providers/``.

    Uses singleton pattern - get instance via get_enrichment_registry() or
    EnrichmentRegistry.get_instance().

    Example usage:
        registry = get_enrichment_registry()

        # Get a specific provider
        provider = registry.get_provider("tmdb")

        # Get all enabled providers based on config
        enabled = registry.get_enabled_providers(config)
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
        """Register the built-in providers.

        The map is built whole and swapped in rather than rebuilt in place:
        two concurrent passes doing the latter left the process serving a
        partial map for good.
        """
        with _registry_lock:
            if self._discovered and not force:
                return

        built_in: tuple[EnrichmentProvider, ...] = (
            TMDBProvider(),
            OpenLibraryProvider(),
            RAWGProvider(),
        )
        providers = {provider.name: provider for provider in built_in}

        with _registry_lock:
            self._providers = providers
            self._discovered = True

        logger.info("Registered enrichment providers: %s", list(providers))

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
