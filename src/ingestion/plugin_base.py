"""Abstract base class for source plugins."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from src.models.config_field import ConfigField
from src.models.content import ContentItem, ContentType

# Progress callback: (items_processed, total_items, current_item) -> None
# - items_processed: Number of items fetched/processed so far
# - total_items: Total expected (None if unknown)
# - current_item: Title of current item or phase description (e.g. "Fetching...")
ProgressCallback = Callable[[int, int | None, str | None], None]


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

            def validate_config(self, config: dict[str, Any]) -> list[str]:
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
        Should be lowercase with underscores (e.g., "goodreads", "steam", "generic_csv").

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
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate plugin configuration.

        Checks that all required fields are present and valid.
        Called before fetch() to catch configuration errors early.

        Args:
            config: Plugin-specific configuration dict from inputs.<name>

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

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Transform raw YAML config into the dict expected by fetch/validate.

        Override in subclasses when the YAML keys differ from the keys
        that ``fetch()`` and ``validate_config()`` expect.  The default
        implementation returns *raw_config* unchanged.

        Args:
            raw_config: The ``inputs.<source>`` section from the YAML config.

        Returns:
            Transformed config dict ready for ``validate_config`` / ``fetch``.
        """
        return dict(raw_config)

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
