"""Abstract base class for enrichment providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.models.config_field import ConfigField
from src.models.content import ContentItem, ContentType


@dataclass
class ProviderInfo:
    """Information about a registered enrichment provider.

    Used by the registry to track provider metadata without
    requiring instantiation.
    """

    name: str
    display_name: str
    content_types: list[ContentType]
    requires_api_key: bool
    config_schema: list[ConfigField] = field(default_factory=list)


@dataclass
class EnrichmentResult:
    """Result of enriching a content item.

    Contains the metadata retrieved from the external API.
    Fields that couldn't be found are set to None.
    """

    # Provider's ID for the item (e.g., "tmdb:12345", "openlibrary:OL123W")
    external_id: str | None = None

    # Core metadata fields
    genres: list[str] | None = None
    tags: list[str] | None = None
    description: str | None = None

    # Additional provider-specific metadata
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    # Quality of the match
    # "high" = matched by ID or exact title+year
    # "medium" = fuzzy match
    # "not_found" = no match found
    match_quality: str = "high"

    # Provider that produced this result
    provider: str = ""


class ProviderError(Exception):
    """Exception raised when an enrichment provider encounters an error.

    Attributes:
        provider_name: Name of the provider that raised the error
        message: Human-readable error message
    """

    def __init__(self, provider_name: str, message: str) -> None:
        """Initialize ProviderError.

        Args:
            provider_name: Name of the provider that raised the error
            message: Human-readable error message
        """
        self.provider_name = provider_name
        self.message = message
        super().__init__(f"{provider_name}: {message}")


class EnrichmentProvider(ABC):
    """Abstract base class for metadata enrichment providers.

    All enrichment providers must implement this interface. Providers are
    discovered and registered automatically from src/enrichment/providers/.

    Example implementation:

        class MyProvider(EnrichmentProvider):
            @property
            def name(self) -> str:
                return "my_api"

            @property
            def display_name(self) -> str:
                return "My API"

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
                        description="API key for My API",
                        sensitive=True,
                    ),
                ]

            def validate_config(self, config: dict[str, Any]) -> list[str]:
                errors = []
                if not config.get("api_key"):
                    errors.append("'api_key' is required")
                return errors

            def enrich(
                self, item: ContentItem, config: dict[str, Any]
            ) -> EnrichmentResult | None:
                # Fetch metadata and return EnrichmentResult
                return EnrichmentResult(
                    genres=["Action"],
                    description="A great movie",
                    provider=self.name,
                )
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this provider.

        Used in config as enrichment.providers.<name>.* and in CLI commands.
        Should be lowercase with underscores (e.g., "tmdb", "openlibrary").

        Returns:
            Provider identifier string
        """
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for display purposes.

        Used in UI, logs, and error messages.
        Example: "TMDB", "Open Library", "RAWG"

        Returns:
            Human-readable provider name
        """
        ...

    @property
    @abstractmethod
    def content_types(self) -> list[ContentType]:
        """Content types this provider can enrich.

        Used to filter providers by content type.

        Returns:
            List of ContentType values this provider supports
        """
        ...

    @property
    @abstractmethod
    def requires_api_key(self) -> bool:
        """Whether this provider requires an API key.

        Used to validate configuration before enriching and to indicate
        in the UI that credentials are needed.

        Returns:
            True if an API key is required, False otherwise
        """
        ...

    @property
    def description(self) -> str:
        """Short description of what this provider does.

        Used in UI and CLI help text. Default derives from display_name.

        Returns:
            Human-readable description string
        """
        return f"Enrich metadata from {self.display_name}"

    @property
    def rate_limit_requests_per_second(self) -> float:
        """Maximum requests per second to the API.

        Used by the rate limiter to enforce API limits.
        Default is 1 request per second (conservative).

        Returns:
            Maximum requests per second
        """
        return 1.0

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]:
        """Get configuration schema for this provider.

        Returns a list of ConfigField objects describing the required
        and optional configuration options. Used for validation,
        documentation, and UI generation.

        Returns:
            List of ConfigField objects
        """
        ...

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate provider configuration.

        Checks that all required fields are present and valid.
        Called before enrich() to catch configuration errors early.

        Args:
            config: Provider-specific configuration dict

        Returns:
            List of validation error messages (empty list if valid)
        """
        ...

    @abstractmethod
    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        """Enrich a content item with metadata from this provider.

        Fetches metadata from the external API and returns an EnrichmentResult.
        Returns None if the item cannot be found or matched.

        The implementation should:
        1. Try to match the item using available identifiers (IDs, title, year)
        2. Fetch detailed metadata if a match is found
        3. Return EnrichmentResult with filled fields
        4. Set match_quality appropriately ("high", "medium", "not_found")

        Args:
            item: ContentItem to enrich
            config: Provider-specific configuration dict

        Returns:
            EnrichmentResult with fetched metadata, or None if not found

        Raises:
            ProviderError: If an API error occurs
        """
        ...

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Transform raw YAML config into the dict expected by validate/enrich.

        Override in subclasses when the YAML keys differ from the keys
        that ``enrich()`` and ``validate_config()`` expect. The default
        implementation returns *raw_config* unchanged.

        Args:
            raw_config: The ``enrichment.providers.<provider>`` section from config.

        Returns:
            Transformed config dict ready for ``validate_config`` / ``enrich``.
        """
        return dict(raw_config)

    def get_info(self) -> ProviderInfo:
        """Get provider information as a ProviderInfo object.

        Useful for serialization and display without needing
        the full provider instance.

        Returns:
            ProviderInfo with this provider's metadata
        """
        return ProviderInfo(
            name=self.name,
            display_name=self.display_name,
            content_types=self.content_types,
            requires_api_key=self.requires_api_key,
            config_schema=self.get_config_schema(),
        )
