"""Abstract base class for source plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, final

from src.models.config_field import ConfigField
from src.models.content import ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

# Progress callback: (items_processed, total_items, current_item) -> None
# - items_processed: Number of items fetched/processed so far
# - total_items: Total expected (None if unknown)
# - current_item: Title of current item or phase description (e.g. "Fetching...")
ProgressCallback = Callable[[int, int | None, str | None], None]

# Credential update callback: (key, new_value) -> None
# Called by plugins when an OAuth token is rotated during a sync operation.
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


@dataclass
class PluginInfo:
    """Information about a registered plugin.

    Used by the registry to track plugin metadata without
    requiring instantiation.
    """

    name: str
    display_name: str
    content_types: list[ContentType]
    requires_api_key: bool
    requires_network: bool
    config_schema: list[ConfigField] = field(default_factory=list)


class SourceError(Exception):
    """Exception raised when a source plugin encounters an error.

    Attributes:
        plugin_name: Name of the plugin that raised the error
        message: Human-readable error message
    """

    def __init__(self, plugin_name: str, message: str) -> None:
        """Initialize SourceError.

        Args:
            plugin_name: Name of the plugin that raised the error
            message: Human-readable error message
        """
        self.plugin_name = plugin_name
        self.message = message
        super().__init__(f"{plugin_name}: {message}")


class SourcePlugin(ABC):
    """Abstract base class for data source plugins.

    All source plugins must implement this interface. Plugins are discovered
    and registered automatically from src/ingestion/sources/ and plugins/private/.

    Example implementation:

        class MyPlugin(SourcePlugin):
            @property
            def name(self) -> str:
                return "my_source"

            @property
            def display_name(self) -> str:
                return "My Data Source"

            @property
            def content_types(self) -> list[ContentType]:
                return [ContentType.BOOK]

            @property
            def requires_api_key(self) -> bool:
                return False

            def get_config_schema(self) -> list[ConfigField]:
                return [
                    ConfigField(
                        name="path",
                        field_type=str,
                        required=True,
                        description="Path to data file"
                    ),
                ]

            def validate_config(
                self,
                config: dict[str, Any],
                storage: StorageManager | None = None,
                user_id: int = 1,
            ) -> list[str]:
                errors = []
                if not config.get("path"):
                    errors.append("'path' is required")
                return errors

            def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
                # Parse data and yield ContentItems
                yield ContentItem(...)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this plugin.

        Used in config as inputs.<name>.* and in CLI as --source <name>.
        Should be lowercase with underscores (e.g., "goodreads_csv", "steam", "generic_csv").

        Returns:
            Plugin identifier string
        """
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for display purposes.

        Used in UI, logs, and error messages.
        Example: "Goodreads", "Steam", "Sonarr (TV Shows)"

        Returns:
            Human-readable plugin name
        """
        ...

    @property
    @abstractmethod
    def content_types(self) -> list[ContentType]:
        """Content types this plugin provides.

        Used to filter plugins by content type and validate configuration.

        Returns:
            List of ContentType values this plugin can produce
        """
        ...

    @property
    @abstractmethod
    def requires_api_key(self) -> bool:
        """Whether this plugin requires an API key.

        Used to validate configuration before fetching and to indicate
        in the UI that credentials are needed.

        Returns:
            True if an API key is required, False otherwise
        """
        ...

    @property
    def description(self) -> str:
        """Short description of what this plugin does.

        Used in UI and CLI help text. Default derives from display_name.

        Returns:
            Human-readable description string
        """
        return f"Import from {self.display_name}"

    @property
    def requires_network(self) -> bool:
        """Whether this plugin requires network access.

        Default returns the same value as requires_api_key, since most
        API-based sources need network. Override for file-based sources
        that don't need network access.

        Returns:
            True if network access is required, False otherwise
        """
        return self.requires_api_key

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]:
        """Get configuration schema for this plugin.

        Returns a list of ConfigField objects describing the required
        and optional configuration options. Used for validation,
        documentation, and UI generation.

        Returns:
            List of ConfigField objects
        """
        ...

    @abstractmethod
    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        """Validate plugin configuration.

        Checks that all required fields are present and valid.
        Called before fetch() to catch configuration errors early.

        When *storage* is provided, sensitive fields (e.g. OAuth tokens)
        that are missing from *config* are looked up in the encrypted
        credential database.  If the credential exists there, the field
        is treated as satisfied.

        Args:
            config: Plugin-specific configuration dict from inputs.<name>
            storage: Optional StorageManager for DB credential lookup.
            user_id: User ID for credential lookup (default 1).

        Returns:
            List of validation error messages (empty list if valid)
        """
        ...

    @abstractmethod
    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch content items from this source.

        Main entry point for retrieving data. Yields ContentItem objects
        for each piece of content found. Should set item.source to
        self.get_source_identifier().

        Plugins should call progress_callback(items_processed, total_items,
        current_item) during long-running operations (API fetches, file
        parsing) so callers can report progress to users. Call with
        total_items=None when the total is unknown.

        Args:
            config: Plugin-specific configuration dict from inputs.<name>
            progress_callback: Optional callback for progress updates during
                fetch. Signature: (items_processed, total_items, current_item).

        Yields:
            ContentItem objects for each piece of content

        Raises:
            SourceError: If fetching fails (network error, file not found, etc.)
        """
        ...

    def normalize_rating(self, raw_rating: Any) -> int | None:
        """Normalize a raw rating to 1-5 scale.

        Default implementation handles common cases:
        - None -> None
        - 0 -> None (unrated)
        - 1-5 -> as-is
        - Out of range -> clamped to 1-5

        Override for custom rating scales (e.g., 1-10, percentages).

        Args:
            raw_rating: Raw rating value from source

        Returns:
            Normalized rating (1-5) or None if unrated/invalid
        """
        if raw_rating is None:
            return None

        try:
            rating = int(raw_rating)
            if rating == 0:
                return None
            # Clamp to 1-5 range
            return max(1, min(5, rating))
        except (ValueError, TypeError):
            return None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # @final only reaches code mypy checks, and private plugins are neither
        # checked nor in this repository. Refusing the override at class
        # creation makes the mistake impossible rather than merely flagged.
        super().__init_subclass__(**kwargs)
        for method, guidance in _FRAMEWORK_OWNED_METHODS.items():
            if method in cls.__dict__:
                raise TypeError(f"{cls.__name__} {guidance}")

    @final
    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Build the config ``fetch`` receives, framework keys intact.

        A plugin's :meth:`transform_fields` may return a fresh dict, so the
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
        """Transform the stored source fields into the keys this plugin reads.

        Override when the stored keys differ from what ``fetch`` expects, or to
        normalise values. Framework keys are not passed in and must not be added.
        """
        return dict(raw_fields)

    @final
    def get_source_identifier(self, config: dict[str, Any] | None = None) -> str:
        """Get the source identifier to store in ContentItem.source.

        When *config* contains a ``_source_id`` key (injected by
        :func:`resolve_inputs`), that user-defined name is returned.
        Otherwise falls back to the plugin name.

        Args:
            config: Optional plugin config dict that may contain ``_source_id``.

        Returns:
            Source identifier string
        """
        if config is not None:
            source_id = config.get("_source_id")
            if source_id is not None:
                return str(source_id)
        return self.name

    def get_info(self) -> PluginInfo:
        """Get plugin information as a PluginInfo object.

        Useful for serialization and display without needing
        the full plugin instance.

        Returns:
            PluginInfo with this plugin's metadata
        """
        return PluginInfo(
            name=self.name,
            display_name=self.display_name,
            content_types=self.content_types,
            requires_api_key=self.requires_api_key,
            requires_network=self.requires_network,
            config_schema=self.get_config_schema(),
        )
