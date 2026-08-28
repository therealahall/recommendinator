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
    """The enrichment providers, discovered rather than listed."""

    _instance: "EnrichmentRegistry | None" = None

    def __init__(self) -> None:
        self._providers: dict[str, EnrichmentProvider] = {}
        self._discovered = False

    @classmethod
    def get_instance(cls) -> "EnrichmentRegistry":
        with _registry_lock:
            if cls._instance is None:
                cls._instance = EnrichmentRegistry()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def discover_providers(self, force: bool = False) -> None:
        """A pass fills a registry of its own and swaps the finished map in, and
        scans outside ``_registry_lock``: it constructs code this repo does not control.
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
        self.discover_providers()
        return self._providers.get(name)

    def get_all_providers(self) -> dict[str, EnrichmentProvider]:
        self.discover_providers()
        return dict(self._providers)

    def get_enabled_providers(self, config: dict[str, Any]) -> list[EnrichmentProvider]:
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
    return EnrichmentRegistry.get_instance()
