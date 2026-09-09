from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Re-exported: every enrichment provider imports ConfigField from here, the
# same way source plugins take it from plugin_base.
from src.models.config_field import ConfigField as ConfigField
from src.models.content import ContentItem, ContentType
from src.models.detail_fields import PIN_KEY
from src.utils.matching import Candidate
from src.utils.series import (
    MAX_SERIES_POSITION,
    SERIES_AUTHORITY_KEY,
    SERIES_NAME_KEY,
    SERIES_POSITION_KEY,
    SeriesAuthority,
    valid_series_position,
)
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


def pins_of(metadata: dict[str, Any]) -> dict[str, str]:
    """The record each provider is bound to, however the blob read back."""
    pins = metadata.get(PIN_KEY)
    if not isinstance(pins, dict):
        return {}
    return {str(name): str(record) for name, record in pins.items() if record}


def pinned_record(item: ContentItem, provider_name: str) -> str | None:
    return pins_of(item.metadata or {}).get(provider_name)


def with_pin(
    metadata: dict[str, Any], provider_name: str, record_id: str | None
) -> dict[str, str]:
    """The whole pin map as it should be stored, *record_id* ``None`` clearing."""
    kept = {
        name: record
        for name, record in pins_of(metadata).items()
        if name != provider_name
    }
    return kept if record_id is None else {**kept, provider_name: record_id}


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


@dataclass(frozen=True)
class SeriesOrdinal:
    """A position and the series it counts within. Naming the series is what
    lets the merge check that this source and whoever stored the name mean the
    same one, rather than filing a sub-series' number under its parent.
    """

    position: float
    series_name: str
    authority: SeriesAuthority = SeriesAuthority.AUTHORED

    def __post_init__(self) -> None:
        """One no reader can read back still counts as an ordinal stored, and
        the authority ladder then never asks for it again.
        """
        if not valid_series_position(self.position):
            raise ValueError(
                f"A series position must be between 0 and {MAX_SERIES_POSITION}"
            )
        if not self.series_name.strip():
            raise ValueError("A series position must name the series it counts within")

    def as_metadata(self) -> dict[str, Any]:
        return {
            SERIES_NAME_KEY: self.series_name.strip(),
            SERIES_POSITION_KEY: self.position,
            SERIES_AUTHORITY_KEY: self.authority.value,
        }


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

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        """The item's whole metadata; None when this provider has no match.

        Not abstract, so a provider can state only an ordinal — and one that
        states nothing at all is refused when its class is created.
        """
        return None

    def search(self, item: ContentItem, config: dict[str, Any]) -> list[Candidate]:
        """The records this provider would consider for *item*, its own ranking
        kept — the same search :meth:`enrich` runs, without the discarding.
        """
        return []

    def accepts_record_id(self, record_id: str) -> bool:
        """Whether a pin naming this record is one :meth:`enrich` can look up.
        An id it cannot is stored and then silently ignored by every run.
        """
        return bool(record_id.strip())

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        """The item's place in its series, asked of every provider that states
        one. None means no ordinal for this item; a failure raises, as
        :meth:`enrich` does.
        """
        return None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not (_overrides(cls, "enrich") or _overrides(cls, "fetch_series_ordinal")):
            raise TypeError(
                f"{cls.__name__} implements neither enrich nor fetch_series_ordinal,"
                " so enrichment has nothing to ask it"
            )


def _overrides(provider_class: type[EnrichmentProvider], method: str) -> bool:
    return getattr(provider_class, method) is not getattr(EnrichmentProvider, method)


def states_a_match(provider: EnrichmentProvider) -> bool:
    """Whether the first-success loop may settle an item on this provider."""
    return _overrides(type(provider), "enrich")


def states_a_series_ordinal(provider: EnrichmentProvider) -> bool:
    """Whether the ordinal pass has anything to ask this provider."""
    return _overrides(type(provider), "fetch_series_ordinal")
