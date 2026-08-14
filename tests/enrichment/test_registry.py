"""Tests for the enrichment provider registry."""

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

from src.enrichment import registry as registry_module
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
)
from src.enrichment.registry import (
    EnrichmentRegistry,
    _registry_lock,
    get_enrichment_registry,
)
from src.models.content import ContentItem, ContentType

# Bounded so a thread nothing releases fails the test instead of hanging the
# suite; nothing waits this long on the passing path.
_STALL_TIMEOUT_SECONDS = 5.0

# How long the second discovery is given to prove it cannot get in. Only spent
# on the passing path.
_BLOCKED_GRACE_SECONDS = 0.5

_BUILTIN_PROVIDER_NAMES = {"tmdb", "openlibrary", "rawg"}


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
        """Test that force=True drops a manual registration and re-registers."""
        registry = EnrichmentRegistry.get_instance()
        registry.register(MockMovieProvider())
        registry._discovered = True

        registry.discover_providers(force=True)

        assert set(registry.get_all_providers()) == _BUILTIN_PROVIDER_NAMES


class TestConcurrentDiscoveryRegression:
    """A registration pass must not be visible half-built.

    The twin of ``tests/test_registry.py``'s case, same shape:
    ``discover_providers`` cleared ``_providers`` and refilled it in place,
    with every reader in a threadpool worker.
    Fix: fill a private map, swap it in.
    """

    def test_a_rebuild_is_invisible_until_it_has_finished(self) -> None:
        """Forced interleaving: the rebuild is parked mid-build throughout.

        Parking inside a provider's ``__init__`` also holds the construction
        outside the lock: under it, the read below would never return.
        """
        registry = EnrichmentRegistry()
        registry.discover_providers()
        parked = threading.Event()
        release = threading.Event()

        class StallingProvider(MockBookProvider):
            def __init__(self) -> None:
                super().__init__()
                parked.set()
                assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)

        with (
            patch.object(registry_module, "RAWGProvider", StallingProvider),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            rebuild = pool.submit(registry.discover_providers, force=True)
            assert parked.wait(timeout=_STALL_TIMEOUT_SECONDS)

            try:
                # A wedged main thread would take the suite down with it.
                during_rebuild = pool.submit(registry.get_all_providers).result(
                    timeout=_BLOCKED_GRACE_SECONDS
                )
            finally:
                release.set()
            rebuild.result(timeout=_STALL_TIMEOUT_SECONDS)

        # The parked pass is building a map of its own, so getting the previous
        # pass's providers is the swap being invisible. Rebuilt in place, this
        # read lands on the half-built map.
        assert set(during_rebuild) == _BUILTIN_PROVIDER_NAMES
        assert set(registry.get_all_providers()) == {"tmdb", "openlibrary", "mock_book"}


class TestRegistrySingletonIsBuiltOnceRegression:
    """A cold process must not hand two callers two registries.

    Bug: ``get_instance`` is a check-then-set and its callers run in threadpool
    workers, so two on a cold process each keep a registry of their own.
    Fix: build under ``_registry_lock``.
    """

    def test_two_cold_callers_share_one_registry(self) -> None:
        """Forced interleaving, not a race: the build cannot finish unreleased.

        The constructor asserts the lock is held rather than the test asserting
        the second caller has not finished: "not done yet" is also what a
        descheduled thread looks like.
        """
        building = threading.Event()
        release = threading.Event()
        built: list[EnrichmentRegistry] = []
        real_init = EnrichmentRegistry.__init__

        def stalling_init(self: EnrichmentRegistry) -> None:
            assert _registry_lock.locked(), "the lazy build is not serialised"
            building.set()
            assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)
            real_init(self)
            built.append(self)

        EnrichmentRegistry.reset_instance()
        try:
            with (
                patch.object(EnrichmentRegistry, "__init__", stalling_init),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(EnrichmentRegistry.get_instance)
                assert building.wait(timeout=_STALL_TIMEOUT_SECONDS)

                second = pool.submit(EnrichmentRegistry.get_instance)
                # Unlocked, the second caller passes the ``is None`` check and
                # builds a registry of its own while the first is parked, so it
                # finishes here rather than waiting for the release.
                with pytest.raises(TimeoutError):
                    second.result(timeout=_BLOCKED_GRACE_SECONDS)

                release.set()
                registry = first.result(timeout=_STALL_TIMEOUT_SECONDS)
                assert second.result(timeout=_STALL_TIMEOUT_SECONDS) is registry
            assert built == [registry]
        finally:
            EnrichmentRegistry.reset_instance()


class TestEnrichmentRegistryIntegration:
    """Integration tests for provider registry with real providers."""

    @pytest.fixture(autouse=True)
    def reset_registry(self) -> None:
        """Reset the singleton before each test."""
        EnrichmentRegistry.reset_instance()

    def test_the_three_builtin_providers_are_registered(self) -> None:
        """Registration is by direct import, so a missing one is a typo here."""
        registry = get_enrichment_registry()

        assert set(registry.get_all_providers()) == _BUILTIN_PROVIDER_NAMES
