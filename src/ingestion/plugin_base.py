from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, final

# Re-exported: docs/PLUGIN_DEVELOPMENT.md tells plugin authors to import
# ConfigField from here, and every plugin in and out of the repo does.
from src.models.config_field import ConfigField as ConfigField
from src.models.content import ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# Progress callback: (items_processed, total_items, current_item) -> None
ProgressCallback = Callable[[int, int | None, str | None], None]

# Injected into plugin config as "_on_credential_rotated" by execute_sync.
CredentialUpdateCallback = Callable[[str, str], None]

# Framework-owned config keys are underscore-prefixed so they never collide
# with a plugin's own fields: "_source_id" from config assembly,
# "_on_credential_rotated" from execute_sync.
FRAMEWORK_CONFIG_PREFIX = "_"

# Both decide what a source is called, and execute_sync stores a rotated
# credential under that answer, so an override would let one source overwrite
# another's secret. Each entry names what to override instead.
_FRAMEWORK_OWNED_METHODS = {
    "transform_config": (
        "must override transform_fields, not transform_config, which carries "
        "the framework's own config keys across the plugin's transform."
    ),
    "get_source_identifier": (
        "must override the name property, not get_source_identifier, which "
        "answers with the id the user gave the source and decides both item "
        "attribution and which source owns a rotated credential."
    ),
}


class SourceError(Exception):
    def __init__(self, plugin_name: str, message: str) -> None:
        self.plugin_name = plugin_name
        self.message = message
        super().__init__(f"{plugin_name}: {message}")


class SourcePlugin(ABC):
    #: Until the user picks one; a key of ``schedule.SYNC_INTERVAL_KEYS``.
    default_sync_interval: str = "daily"

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    @abstractmethod
    def content_types(self) -> list[ContentType]: ...

    @property
    @abstractmethod
    def requires_api_key(self) -> bool: ...

    @property
    def description(self) -> str:
        return f"Import from {self.display_name}"

    @property
    def requires_network(self) -> bool:
        return self.requires_api_key

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]: ...

    @abstractmethod
    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]: ...

    @abstractmethod
    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """``execute_sync`` stamps ``item.source`` with the configured source id,
        overwriting any the plugin set.
        """
        ...

    def normalize_rating(self, raw_rating: Any) -> int | None:
        if raw_rating is None:
            return None

        try:
            rating = int(raw_rating)
            if rating == 0:
                return None
            return max(1, min(5, rating))
        except (ValueError, TypeError):
            return None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # @final only reaches code mypy checks, and a plugin author outside this
        # repository need never run it. Refusing the override at class creation
        # makes the mistake impossible rather than merely flagged.
        super().__init_subclass__(**kwargs)
        for method, guidance in _FRAMEWORK_OWNED_METHODS.items():
            if method in cls.__dict__:
                raise TypeError(f"{cls.__name__} {guidance}")

    @final
    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """A plugin's :meth:`transform_fields` may return a fresh dict, so the
        keys go back on afterwards: a lost ``_source_id`` reattributes the
        source's items and rotated tokens to the plugin name.
        """
        framework = {
            key: value
            for key, value in raw_config.items()
            if key.startswith(FRAMEWORK_CONFIG_PREFIX)
        }
        fields = {
            key: value for key, value in raw_config.items() if key not in framework
        }
        return {**cls.transform_fields(fields), **framework}

    @classmethod
    def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
        """Framework keys are not passed in and must not be added."""
        return dict(raw_fields)

    @final
    def get_source_identifier(self, config: dict[str, Any] | None = None) -> str:
        if config is not None:
            source_id = config.get("_source_id")
            if source_id is not None:
                return str(source_id)
        return self.name
