"""Tests for the enrichment provider registry."""

import importlib
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.enrichment import providers as providers_package
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


def _private_module_names() -> list[str]:
    """The imported ``private`` package and its submodules, if any."""
    return [
        name
        for name in list(sys.modules)
        if name == "private" or name.startswith("private.")
    ]


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


class TestConcurrentDiscoveryRegression:
    """A registration pass must not be visible half-built.

    The twin of ``tests/test_registry.py``'s case, same shape:
    ``discover_providers`` cleared ``_providers`` and refilled it in place,
    with every reader in a threadpool worker.
    Fix: fill a private map, swap it in.
    """

    def test_a_rebuild_is_invisible_until_it_has_finished(self) -> None:
        """Forced interleaving: the rebuild is parked mid-scan throughout.

        Parking inside a provider's ``__init__`` also holds the construction
        outside the lock: under it, the read below would never return.
        """
        registry = EnrichmentRegistry()
        first_pass_done = threading.Event()
        parked = threading.Event()
        release = threading.Event()

        class StallingProvider(MockMovieProvider):
            def __init__(self) -> None:
                super().__init__()
                parked.set()
                assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)

        stalling_module = types.ModuleType("stalling_module")
        stalling_module.StallingProvider = StallingProvider  # type: ignore[attr-defined]

        def register_one_builtin(self: EnrichmentRegistry) -> None:
            # A different provider per pass, so which map the reader got is
            # readable off its contents rather than inferred from timing.
            if not first_pass_done.is_set():
                self.register(MockBookProvider())
                first_pass_done.set()
                return
            self._register_providers_from_module(
                stalling_module, "stalling_module", "test"
            )

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
                # A wedged main thread would take the suite down with it.
                during_rebuild = pool.submit(registry.get_all_providers).result(
                    timeout=_BLOCKED_GRACE_SECONDS
                )
            finally:
                release.set()
            rebuild.result(timeout=_STALL_TIMEOUT_SECONDS)

        # The parked pass is building a map of its own, so getting the previous
        # pass's provider is the swap being invisible. Rebuilt in place, this
        # read lands on the half-built map.
        assert set(during_rebuild) == {"mock_book"}
        assert set(registry.get_all_providers()) == {"mock_movie"}


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


class TestBuiltinProviderDiscoveryRegression:
    """A provider folder registers by being there, not by being imported.

    The registry listed its three providers as imports for a while, so a fourth
    folder stayed invisible until someone edited ``registry.py``.
    """

    def test_a_folder_dropped_into_the_providers_directory_is_discovered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dropped provider is the only one the registry ends up holding."""
        dropped = tmp_path / "dropped_provider"
        dropped.mkdir()
        (dropped / "__init__.py").write_text(
            f"from {__name__} import MockBookProvider\n"
        )
        # The scan lists the package's own directory and imports through its
        # ``__path__``, so both have to point at the directory under test.
        monkeypatch.setattr(
            providers_package, "__file__", str(tmp_path / "__init__.py")
        )
        monkeypatch.setattr(providers_package, "__path__", [str(tmp_path)])
        monkeypatch.setattr(
            EnrichmentRegistry, "_discover_private_providers", lambda self: None
        )
        importlib.invalidate_caches()

        registry = EnrichmentRegistry()
        try:
            registry.discover_providers()
        finally:
            sys.modules.pop("src.enrichment.providers.dropped_provider", None)

        assert set(registry.get_all_providers()) == {"mock_book"}


class TestPrivateProviderDiscoveryRegression:
    """A provider shipped outside the repo must load from private/plugins/.

    The scan read ``plugins/private/enrichment/``, which this repo has never
    had, so an out-of-tree provider could not be loaded at all. The
    source-plugin registry has always read ``private/plugins/``.
    """

    def test_a_provider_dropped_into_private_plugins_is_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A discovery pass reaches a module the repo does not ship.

        Driven through the public entry point, so dropping the private scan out
        of it fails here: nothing else can register ``mock_book``.
        """
        private_plugins = tmp_path / "private" / "plugins"
        private_plugins.mkdir(parents=True)
        (private_plugins.parent / "__init__.py").write_text("")
        (private_plugins / "__init__.py").write_text("")
        (private_plugins / "dropped.py").write_text(
            f"from {__name__} import MockBookProvider\n"
        )
        # The scan derives the project root from this module's location, so a
        # registry.py three levels down makes tmp_path the root.
        monkeypatch.setattr(
            registry_module,
            "__file__",
            str(tmp_path / "src" / "enrichment" / "registry.py"),
        )
        for name in _private_module_names():
            monkeypatch.delitem(sys.modules, name)
        importlib.invalidate_caches()

        registry = EnrichmentRegistry()
        try:
            registry.discover_providers()
        finally:
            for name in _private_module_names():
                del sys.modules[name]
            if str(tmp_path) in sys.path:
                sys.path.remove(str(tmp_path))

        discovered = set(registry.get_all_providers())
        assert "mock_book" in discovered
        assert _BUILTIN_PROVIDER_NAMES <= discovered


class TestEnrichmentRegistryIntegration:
    """Integration tests for provider registry with real providers."""

    @pytest.fixture(autouse=True)
    def reset_registry(self) -> None:
        """Reset the singleton before each test."""
        EnrichmentRegistry.reset_instance()

    def test_the_three_builtin_provider_folders_are_discovered(self) -> None:
        """A folder that fails to import is skipped with a log, so check for real."""
        registry = get_enrichment_registry()

        assert _BUILTIN_PROVIDER_NAMES <= set(registry.get_all_providers())
