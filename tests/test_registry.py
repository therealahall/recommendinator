"""Tests for plugin registry."""

import importlib
import logging
import threading
import types
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion import registry as registry_module
from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry, _registry_lock, get_registry
from src.ingestion.sources.arr_base import ArrPlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class FakeBookPlugin(SourcePlugin):
    """Fake book plugin for registry testing."""

    @property
    def name(self) -> str:
        return "fake_books"

    @property
    def display_name(self) -> str:
        return "Fake Books"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="path", field_type=str, required=True),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("path"):
            errors.append("'path' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="book_1",
            title="Fake Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source=self.get_source_identifier(config),
        )


class FakeGamePlugin(SourcePlugin):
    """Fake game plugin for registry testing."""

    @property
    def name(self) -> str:
        return "fake_games"

    @property
    def display_name(self) -> str:
        return "Fake Games"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="api_key", field_type=str, required=True, sensitive=True),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="game_1",
            title="Fake Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


@pytest.fixture()
def clean_registry() -> PluginRegistry:
    """Create a fresh registry for each test (not singleton)."""
    return PluginRegistry()


# Bounded so a thread nothing releases fails the test instead of hanging the
# suite; nothing waits this long on the passing path.
_STALL_TIMEOUT_SECONDS = 5.0

# How long the second discovery is given to prove it cannot get in. Only spent
# on the passing path.
_BLOCKED_GRACE_SECONDS = 0.5


class TestConcurrentDiscoveryRegression:
    """A discovery pass must not be visible half-built.

    Bug: ``discover_plugins`` cleared ``_plugins`` and refilled it in place, so
    a concurrent reader saw the cleared middle: 404 for a source that exists.
    Fix: fill a private map, swap it in.
    """

    def test_a_rebuild_is_invisible_until_it_has_finished(self) -> None:
        """Forced interleaving: the rebuild is parked mid-scan throughout.

        Pins the half-built map. The cleared middle is closed by the publish
        being one rebind, and a reader cannot be parked inside a publish
        deterministically, so nothing here pins that half.
        """
        registry = PluginRegistry()
        first_pass_done = threading.Event()
        parked = threading.Event()
        release = threading.Event()

        def register_one_builtin(self: PluginRegistry) -> None:
            # A different plugin per pass, so which map the reader got is
            # readable off its contents rather than inferred from timing.
            if not first_pass_done.is_set():
                self.register(FakeBookPlugin())
                first_pass_done.set()
                return
            self.register(FakeGamePlugin())
            parked.set()
            assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)

        with (
            patch.object(
                PluginRegistry, "_discover_builtin_plugins", register_one_builtin
            ),
            patch.object(
                PluginRegistry, "_discover_private_plugins", lambda self: None
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            registry.discover_plugins()
            rebuild = pool.submit(registry.discover_plugins, force=True)
            assert parked.wait(timeout=_STALL_TIMEOUT_SECONDS)

            try:
                # Read from a worker: with the scan under the lock this read
                # never returns, and a wedged main thread takes the suite too.
                during_rebuild = pool.submit(registry.get_all_plugins).result(
                    timeout=_BLOCKED_GRACE_SECONDS
                )
            finally:
                release.set()
            rebuild.result(timeout=_STALL_TIMEOUT_SECONDS)

        # The parked pass has already registered fake_games into a map of its
        # own, so getting the previous pass's plugin is the swap being
        # invisible. Rebuilt in place, this read lands on the half-built map.
        assert set(during_rebuild) == {"fake_books"}
        assert set(registry.get_all_plugins()) == {"fake_games"}


class TestPluginConstructedOutsideTheLockRegression:
    """A plugin that calls the registry while loading must not hang.

    Bug: the lock spanned ``import_module`` and every plugin ``__init__``, so a
    plugin calling ``get_registry()`` blocked on its caller's own lock,
    silently.
    Fix: only the publish is locked.
    """

    def test_a_plugin_whose_init_asks_for_the_registry_still_loads(self) -> None:
        """Bounded: a regression fails here rather than hanging the suite.

        ``private/plugins/`` is a documented extension point, so a plugin's
        ``__init__`` is code this repository does not control.
        """

        class ReentrantPlugin(FakeBookPlugin):
            def __init__(self) -> None:
                super().__init__()
                get_registry()

        fake_module = types.ModuleType("fake_module")
        fake_module.ReentrantPlugin = ReentrantPlugin  # type: ignore[attr-defined]
        registry = PluginRegistry()

        def register_the_reentrant_module(self: PluginRegistry) -> None:
            self._register_plugins_from_module(fake_module, "fake_module", "test")

        PluginRegistry.reset_instance()
        try:
            with (
                patch.object(
                    PluginRegistry,
                    "_discover_builtin_plugins",
                    register_the_reentrant_module,
                ),
                patch.object(
                    PluginRegistry, "_discover_private_plugins", lambda self: None
                ),
            ):
                # Daemon, because a thread deadlocked on the module lock is
                # never joinable and pytest would never exit.
                discovery = threading.Thread(
                    target=registry.discover_plugins, daemon=True
                )
                discovery.start()
                discovery.join(timeout=_STALL_TIMEOUT_SECONDS)

                assert (
                    not discovery.is_alive()
                ), "discovery deadlocked on a plugin that called get_registry()"
                assert set(registry.get_all_plugins()) == {"fake_books"}
        finally:
            PluginRegistry.reset_instance()


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
        built: list[PluginRegistry] = []
        real_init = PluginRegistry.__init__

        def stalling_init(self: PluginRegistry) -> None:
            assert _registry_lock.locked(), "the lazy build is not serialised"
            building.set()
            assert release.wait(timeout=_STALL_TIMEOUT_SECONDS)
            real_init(self)
            built.append(self)

        PluginRegistry.reset_instance()
        try:
            with (
                patch.object(PluginRegistry, "__init__", stalling_init),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                first = pool.submit(PluginRegistry.get_instance)
                assert building.wait(timeout=_STALL_TIMEOUT_SECONDS)

                second = pool.submit(PluginRegistry.get_instance)
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
            PluginRegistry.reset_instance()


class TestPluginRegistryAbstractClassRegression:
    """Regression tests for abstract class handling in plugin discovery.

    Reported in: https://github.com/therealahall/recommendinator/issues/7
    """

    def test_skips_abstract_intermediate_class_regression(
        self, clean_registry: PluginRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that abstract intermediate base classes are skipped silently.

        Bug: The registry tried to instantiate ArrPlugin (an abstract base class
        for Radarr/Sonarr) because it only filtered out SourcePlugin itself.
        This caused 'Can't instantiate abstract class ArrPlugin' warnings on
        every module that imported or defined ArrPlugin.

        Root cause: _register_plugins_from_module checked `attr is not SourcePlugin`
        but didn't check for other abstract classes in the hierarchy.

        Fix: Use inspect.isabstract() to skip any abstract class, not just SourcePlugin.

        Reported in: https://github.com/therealahall/recommendinator/issues/7
        """
        fake_module = types.ModuleType("fake_module")
        fake_module.ArrPlugin = ArrPlugin  # type: ignore[attr-defined]
        fake_module.FakeBookPlugin = FakeBookPlugin  # type: ignore[attr-defined]

        clean_registry._discovered = True  # Prevent auto-discovery

        with caplog.at_level(logging.WARNING, logger="src.ingestion.registry"):
            clean_registry._register_plugins_from_module(
                fake_module, "fake_module", "test"
            )

        all_plugins = clean_registry.get_all_plugins()
        assert (
            "fake_books" in all_plugins
        ), f"Expected fake_books to be registered, got: {list(all_plugins.keys())}"
        assert (
            len(all_plugins) == 1
        ), f"Expected exactly 1 plugin, got {len(all_plugins)}: {list(all_plugins.keys())}"
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert (
            warning_records == []
        ), f"Expected no warnings, got: {[r.message for r in warning_records]}"


class TestPluginImportFailureRegression:
    """Symptom: a broken private module, then goodreads_rss in a container
    without defusedxml, vanished from every listing.

    Cause: both scans logged and continued, keeping no record of what died.
    Fix: retain the module and its reason.
    """

    def test_a_builtin_module_that_raises_is_reported_with_its_reason(
        self, clean_registry: PluginRegistry
    ) -> None:
        """The defusedxml incident: goodreads_rss dies, the rest still load."""
        real_import = importlib.import_module

        def import_module(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "src.ingestion.sources.goodreads_rss":
                raise ModuleNotFoundError("No module named 'defusedxml'")
            return real_import(name, *args, **kwargs)

        with (
            patch.object(
                PluginRegistry, "_discover_private_plugins", lambda self: None
            ),
            patch.object(registry_module.importlib, "import_module", import_module),
        ):
            clean_registry.discover_plugins()

        assert clean_registry.get_import_errors() == {
            "goodreads_rss": "ModuleNotFoundError: No module named 'defusedxml'"
        }
        assert "goodreads_rss" not in clean_registry.get_all_plugins()
        assert "goodreads_csv" in clean_registry.get_all_plugins()


class TestGoodreadsPluginRename:
    """Real-discovery tests locking in the goodreads -> goodreads_csv rename.

    These exercise the actual built-in plugin discovery (no fakes) so they
    prove the rename is a true hard rename: the new identifier resolves and
    the old one does not silently fall back to anything.
    """

    def test_goodreads_csv_resolves_to_renamed_class(
        self, clean_registry: PluginRegistry
    ) -> None:
        """The renamed plugin resolves under 'goodreads_csv' with correct metadata."""
        from src.ingestion.sources.goodreads_csv.goodreads_csv import (
            GoodreadsCsvPlugin,
        )

        clean_registry.discover_plugins()
        plugin = clean_registry.get_plugin("goodreads_csv")

        assert isinstance(plugin, GoodreadsCsvPlugin)
        assert plugin.name == "goodreads_csv"
        assert plugin.display_name == "Goodreads (CSV Export)"
