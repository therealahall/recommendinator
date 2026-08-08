"""Tests for the enrichment provider registry."""

import threading
import types
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

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


class TestConcurrentDiscoveryRegression:
    """A discovery pass must not be visible half-built.

    The twin of ``tests/test_registry.py``'s case, same shape:
    ``discover_providers`` cleared ``_providers`` and refilled it in place,
    with every reader in a threadpool worker.
    Fix: fill a private map, swap it in.
    """

    def test_a_rebuild_is_invisible_until_it_has_finished(self) -> None:
        """Forced interleaving: the rebuild is parked mid-scan throughout.

        Pins the half-built map. The cleared middle is closed by the publish
        being one rebind, and a reader cannot be parked inside a publish
        deterministically, so nothing here pins that half.
        """
        registry = EnrichmentRegistry()
        first_pass_done = threading.Event()
        parked = threading.Event()
        release = threading.Event()

        def register_one_builtin(self: EnrichmentRegistry) -> None:
            # A different provider per pass, so which map the reader got is
            # readable off its contents rather than inferred from timing.
            if not first_pass_done.is_set():
                self.register(MockMovieProvider())
                first_pass_done.set()
                return
            self.register(MockBookProvider())
            parked.set()
            assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)

        with (
            patch.object(
                EnrichmentRegistry, "_discover_builtin_providers", register_one_builtin
            ),
            patch.object(
                EnrichmentRegistry, "_discover_private_providers", lambda self: None
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            registry.discover_providers()
            rebuild = pool.submit(registry.discover_providers, force=True)
            assert parked.wait(timeout=_STALL_TIMEOUT_SECONDS)

            try:
                # Read from a worker: with the scan under the lock this read
                # never returns, and a wedged main thread takes the suite too.
                during_rebuild = pool.submit(registry.get_all_providers).result(
                    timeout=_BLOCKED_GRACE_SECONDS
                )
            finally:
                release.set()
            rebuild.result(timeout=_STALL_TIMEOUT_SECONDS)

        # The parked pass has already registered mock_book into a map of its
        # own, so getting the previous pass's provider is the swap being
        # invisible. Rebuilt in place, this read lands on the half-built map.
        assert set(during_rebuild) == {"mock_movie"}
        assert set(registry.get_all_providers()) == {"mock_book"}


class TestProviderConstructedOutsideTheLockRegression:
    """A provider that calls the registry while loading must not hang.

    Bug: the lock spanned ``import_module`` and every provider ``__init__``, so
    a provider calling ``get_enrichment_registry()`` blocked on its caller's
    own lock, silently.
    Fix: only the publish is locked.
    """

    def test_a_provider_whose_init_asks_for_the_registry_still_loads(self) -> None:
        """Bounded: a regression fails here rather than hanging the suite."""

        class ReentrantProvider(MockMovieProvider):
            def __init__(self) -> None:
                super().__init__()
                get_enrichment_registry()

        fake_module = types.ModuleType("fake_module")
        fake_module.ReentrantProvider = ReentrantProvider  # type: ignore[attr-defined]
        registry = EnrichmentRegistry()

        def register_the_reentrant_module(self: EnrichmentRegistry) -> None:
            self._register_providers_from_module(fake_module, "test")

        EnrichmentRegistry.reset_instance()
        try:
            with (
                patch.object(
                    EnrichmentRegistry,
                    "_discover_builtin_providers",
                    register_the_reentrant_module,
                ),
                patch.object(
                    EnrichmentRegistry, "_discover_private_providers", lambda self: None
                ),
            ):
                # Daemon, because a thread deadlocked on the module lock is
                # never joinable and pytest would never exit.
                discovery = threading.Thread(
                    target=registry.discover_providers, daemon=True
                )
                discovery.start()
                discovery.join(timeout=_STALL_TIMEOUT_SECONDS)

                assert (
                    not discovery.is_alive()
                ), "discovery deadlocked on a provider that called the registry"
                assert set(registry.get_all_providers()) == {"mock_movie"}
        finally:
            EnrichmentRegistry.reset_instance()


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

    def test_discover_builtin_providers(self) -> None:
        """Test discovering built-in providers from src/enrichment/providers/."""
        registry = get_enrichment_registry()
        registry.discover_providers()

        # At minimum, the discovery should complete without error
        # Actual providers will be tested when implemented
        all_providers = registry.get_all_providers()
        assert isinstance(all_providers, dict)
