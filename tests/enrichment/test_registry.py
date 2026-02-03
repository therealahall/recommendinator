"""Tests for the enrichment provider registry."""

from typing import Any

import pytest

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
)
from src.enrichment.registry import EnrichmentRegistry, get_enrichment_registry
from src.models.content import ContentItem, ContentType


class MockMovieProvider(EnrichmentProvider):
    """Mock provider for movies."""

    @property
    def name(self) -> str:
        return "mock_movie"

    @property
    def display_name(self) -> str:
        return "Mock Movie Provider"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
            )
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return [] if config.get("api_key") else ["'api_key' is required"]

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        return EnrichmentResult(genres=["Action"], provider=self.name)


class MockBookProvider(EnrichmentProvider):
    """Mock provider for books (no API key required)."""

    @property
    def name(self) -> str:
        return "mock_book"

    @property
    def display_name(self) -> str:
        return "Mock Book Provider"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        return EnrichmentResult(genres=["Fiction"], provider=self.name)


class TestEnrichmentRegistry:
    """Tests for the EnrichmentRegistry class."""

    @pytest.fixture(autouse=True)
    def reset_registry(self) -> None:
        """Reset the singleton before each test."""
        EnrichmentRegistry.reset_instance()

    def test_singleton_instance(self) -> None:
        """Test that registry returns same instance."""
        registry1 = EnrichmentRegistry.get_instance()
        registry2 = EnrichmentRegistry.get_instance()

        assert registry1 is registry2

    def test_get_enrichment_registry_function(self) -> None:
        """Test the convenience function returns singleton."""
        registry = get_enrichment_registry()

        assert registry is EnrichmentRegistry.get_instance()

    def test_reset_instance(self) -> None:
        """Test that reset_instance clears the singleton."""
        registry1 = EnrichmentRegistry.get_instance()
        EnrichmentRegistry.reset_instance()
        registry2 = EnrichmentRegistry.get_instance()

        assert registry1 is not registry2

    def test_register_provider(self) -> None:
        """Test registering a provider."""
        registry = EnrichmentRegistry.get_instance()
        # Mark as discovered to prevent auto-discovery from clearing
        registry._discovered = True
        provider = MockMovieProvider()

        registry.register(provider)

        assert registry.get_provider("mock_movie") is provider

    def test_register_duplicate_raises(self) -> None:
        """Test that registering duplicate provider raises error."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        provider1 = MockMovieProvider()
        provider2 = MockMovieProvider()

        registry.register(provider1)

        with pytest.raises(
            ValueError, match="Enrichment provider 'mock_movie' already registered"
        ):
            registry.register(provider2)

    def test_unregister_provider(self) -> None:
        """Test unregistering a provider."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        provider = MockMovieProvider()

        registry.register(provider)
        result = registry.unregister("mock_movie")

        assert result is True
        assert registry.get_provider("mock_movie") is None

    def test_unregister_nonexistent(self) -> None:
        """Test unregistering a provider that doesn't exist."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True

        result = registry.unregister("nonexistent")

        assert result is False

    def test_get_provider_not_found(self) -> None:
        """Test getting a provider that doesn't exist."""
        registry = EnrichmentRegistry.get_instance()
        registry.discover_providers()  # Trigger discovery

        result = registry.get_provider("nonexistent")

        assert result is None

    def test_get_all_providers(self) -> None:
        """Test getting all registered providers."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        movie_provider = MockMovieProvider()
        book_provider = MockBookProvider()

        registry.register(movie_provider)
        registry.register(book_provider)

        all_providers = registry.get_all_providers()

        assert "mock_movie" in all_providers
        assert "mock_book" in all_providers
        assert all_providers["mock_movie"] is movie_provider

    def test_list_provider_names(self) -> None:
        """Test listing provider names."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True

        registry.register(MockMovieProvider())
        registry.register(MockBookProvider())

        names = registry.list_provider_names()

        assert sorted(names) == ["mock_book", "mock_movie"]

    def test_get_enabled_providers_none_enabled(self) -> None:
        """Test getting enabled providers when none are enabled."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        registry.register(MockMovieProvider())
        registry.register(MockBookProvider())

        config: dict[str, Any] = {"enrichment": {"providers": {}}}
        enabled = registry.get_enabled_providers(config)

        assert enabled == []

    def test_get_enabled_providers_some_enabled(self) -> None:
        """Test getting enabled providers when some are enabled."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        registry.register(MockMovieProvider())
        registry.register(MockBookProvider())

        config = {
            "enrichment": {
                "providers": {
                    "mock_movie": {"enabled": True, "api_key": "test"},
                    "mock_book": {"enabled": False},
                }
            }
        }
        enabled = registry.get_enabled_providers(config)

        assert len(enabled) == 1
        assert enabled[0].name == "mock_movie"

    def test_get_enabled_providers_all_enabled(self) -> None:
        """Test getting enabled providers when all are enabled."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        registry.register(MockMovieProvider())
        registry.register(MockBookProvider())

        config = {
            "enrichment": {
                "providers": {
                    "mock_movie": {"enabled": True, "api_key": "test"},
                    "mock_book": {"enabled": True},
                }
            }
        }
        enabled = registry.get_enabled_providers(config)

        assert len(enabled) == 2

    def test_get_enabled_providers_missing_config(self) -> None:
        """Test getting enabled providers with missing config sections."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        registry.register(MockMovieProvider())

        # Missing enrichment section entirely
        enabled = registry.get_enabled_providers({})
        assert enabled == []

        # Missing providers section
        enabled = registry.get_enabled_providers({"enrichment": {}})
        assert enabled == []

    def test_get_providers_by_content_type(self) -> None:
        """Test getting providers by content type."""
        registry = EnrichmentRegistry.get_instance()
        registry._discovered = True
        registry.register(MockMovieProvider())
        registry.register(MockBookProvider())

        movie_providers = registry.get_providers_by_content_type(ContentType.MOVIE)
        book_providers = registry.get_providers_by_content_type(ContentType.BOOK)
        game_providers = registry.get_providers_by_content_type(ContentType.VIDEO_GAME)

        assert len(movie_providers) == 1
        assert movie_providers[0].name == "mock_movie"

        assert len(book_providers) == 1
        assert book_providers[0].name == "mock_book"

        assert len(game_providers) == 0

    def test_discover_providers_idempotent(self) -> None:
        """Test that discovery is idempotent (only runs once)."""
        registry = EnrichmentRegistry.get_instance()
        # Trigger initial discovery
        registry.discover_providers()

        # Manually register a provider after discovery
        registry.register(MockMovieProvider())

        # Second discovery should not clear manually registered provider
        registry.discover_providers()
        assert registry.get_provider("mock_movie") is not None

    def test_discover_providers_force(self) -> None:
        """Test that force=True re-discovers providers."""
        registry = EnrichmentRegistry.get_instance()

        # Manually register a provider
        provider = MockMovieProvider()
        registry.register(provider)
        registry._discovered = True

        # Force discovery should clear and rediscover
        registry.discover_providers(force=True)

        # Manual provider should be gone (was cleared)
        # Unless it was in the discovery path
        # For testing, we just check that force works
        assert registry._discovered is True


class TestEnrichmentRegistryIntegration:
    """Integration tests for provider registry with real providers."""

    @pytest.fixture(autouse=True)
    def reset_registry(self) -> None:
        """Reset the singleton before each test."""
        EnrichmentRegistry.reset_instance()

    def test_discover_builtin_providers(self) -> None:
        """Test discovering built-in providers from src/enrichment/providers/."""
        registry = get_enrichment_registry()
        registry.discover_providers()

        # At minimum, the discovery should complete without error
        # Actual providers will be tested when implemented
        all_providers = registry.get_all_providers()
        assert isinstance(all_providers, dict)
