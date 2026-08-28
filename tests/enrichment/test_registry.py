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

_STALL_TIMEOUT_SECONDS = 5.0

_BLOCKED_GRACE_SECONDS = 0.5

_BUILTIN_PROVIDER_NAMES = {"tmdb", "openlibrary", "rawg"}


def _private_module_names() -> list[str]:
    return [
        name
        for name in list(sys.modules)
        if name == "private" or name.startswith("private.")
    ]


class MockMovieProvider(EnrichmentProvider):
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
    @pytest.fixture(autouse=True)
    def reset_registry(self) -> None:
        EnrichmentRegistry.reset_instance()

    def test_get_enabled_providers_some_enabled(self) -> None:
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
    def test_a_rebuild_is_invisible_until_it_has_finished(self) -> None:
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
                during_rebuild = pool.submit(registry.get_all_providers).result(
                    timeout=_BLOCKED_GRACE_SECONDS
                )
            finally:
                release.set()
            rebuild.result(timeout=_STALL_TIMEOUT_SECONDS)

        assert set(during_rebuild) == {"mock_book"}
        assert set(registry.get_all_providers()) == {"mock_movie"}


class TestRegistrySingletonIsBuiltOnceRegression:
    def test_two_cold_callers_share_one_registry(self) -> None:
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
                with pytest.raises(TimeoutError):
                    second.result(timeout=_BLOCKED_GRACE_SECONDS)

                release.set()
                registry = first.result(timeout=_STALL_TIMEOUT_SECONDS)
                assert second.result(timeout=_STALL_TIMEOUT_SECONDS) is registry
            assert built == [registry]
        finally:
            EnrichmentRegistry.reset_instance()


class TestBuiltinProviderDiscoveryRegression:
    def test_a_folder_dropped_into_the_providers_directory_is_discovered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dropped = tmp_path / "dropped_provider"
        dropped.mkdir()
        (dropped / "__init__.py").write_text(
            f"from {__name__} import MockBookProvider\n"
        )
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
    def test_a_provider_dropped_into_private_plugins_is_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        private_plugins = tmp_path / "private" / "plugins"
        dropped = private_plugins / "dropped"
        dropped.mkdir(parents=True)
        (private_plugins.parent / "__init__.py").write_text("")
        (private_plugins / "__init__.py").write_text("")
        (dropped / "__init__.py").write_text(
            "from private.plugins.dropped.dropped import *  # noqa: F401, F403\n"
        )
        (dropped / "dropped.py").write_text(
            f"from {__name__} import MockBookProvider\n"
        )
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
    @pytest.fixture(autouse=True)
    def reset_registry(self) -> None:
        EnrichmentRegistry.reset_instance()

    def test_the_three_builtin_provider_folders_are_discovered(self) -> None:
        registry = get_enrichment_registry()

        assert _BUILTIN_PROVIDER_NAMES <= set(registry.get_all_providers())
