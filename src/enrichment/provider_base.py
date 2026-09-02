import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Re-exported: every enrichment provider imports ConfigField from here, the
# same way source plugins take it from plugin_base.
from src.models.config_field import ConfigField as ConfigField
from src.models.content import ContentItem, ContentType
from src.utils.text import sanitize_for_log


def log_search_title(
    provider_logger: logging.Logger, original: str, cleaned: str
) -> None:
    """Titles restrict no characters, and twice now a provider has grown its own
    unescaped copy of this log (CWE-117). The sink lives here and nowhere else.
    """
    if cleaned == original:
        return
    provider_logger.debug(
        "Cleaned title for search: '%s' -> '%s'",
        sanitize_for_log(original),
        sanitize_for_log(cleaned),
    )


@dataclass
class EnrichmentResult:
    # Provider's ID for the item (e.g., "tmdb:12345", "openlibrary:OL123W")
    external_id: str | None = None

    genres: list[str] | None = None
    tags: list[str] | None = None
    description: str | None = None

    # Its own field: ``extra_metadata`` reaches only the detail tables.
    cover_url: str | None = None

    extra_metadata: dict[str, Any] = field(default_factory=dict)

    # "high" = matched by ID or exact title+year
    # "medium" = fuzzy match
    # "not_found" = no match found
    match_quality: str = "high"

    provider: str = ""


class ProviderError(Exception):
    def __init__(self, provider_name: str, message: str) -> None:
        self.provider_name = provider_name
        self.message = message
        super().__init__(f"{provider_name}: {message}")


class EnrichmentProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Used in config as enrichment.providers.<name>.* and in CLI commands."""
        ...

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
        return f"Enrich metadata from {self.display_name}"

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 1.0

    @abstractmethod
    def get_config_schema(self) -> list[ConfigField]: ...

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]: ...

    @abstractmethod
    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None: ...
