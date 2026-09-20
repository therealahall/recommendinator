from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

from src.ingestion.urls import UrlOrigin, url_origin

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


def is_numeric_record_id(record_id: str) -> bool:
    """``isdigit()`` alone admits '٣', which ``int()`` then reads as 3."""
    return record_id.isascii() and record_id.isdigit()


_LINK_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*", re.ASCII)


def link_slug(url: str, hosts: frozenset[str], kind: str) -> str | None:
    """The slug a ``https://<host>/<kind>/<slug>`` link names. One whitelist for
    every provider, because a slug is spliced into a path or a query body
    nothing parameterises: '/' addresses another endpoint, '"' ends a statement.
    """
    origin = url_origin(url)
    if not isinstance(origin, UrlOrigin) or origin.host.lower() not in hosts:
        return None
    segments = urlsplit(url).path.strip("/").split("/")
    if len(segments) < 2 or segments[0] != kind:
        return None
    return segments[1] if _LINK_SLUG.fullmatch(segments[1]) else None


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

    def as_metadata(self) -> dict[str, Any]:
        stated = dict(self.extra_metadata)
        for key, value in (
            ("genres", self.genres),
            ("tags", self.tags),
            ("description", self.description),
            ("cover_url", self.cover_url),
        ):
            if value:
                stated[key] = value
        return stated


@dataclass(frozen=True)
class SeriesOrdinal:
    """The series a work belongs to, and its position where the source counts
    one. Naming the series lets the merge check both sources mean the same one,
    rather than filing a sub-series' number under its parent.
    """

    position: float | None
    series_name: str
    authority: SeriesAuthority = SeriesAuthority.AUTHORED

    def __post_init__(self) -> None:
        """One no reader can read back still counts as an ordinal stored, and
        the authority ladder then never asks for it again.
        """
        if self.position is not None and not valid_series_position(self.position):
            raise ValueError(
                f"A series position must be between 0 and {MAX_SERIES_POSITION}"
            )
        if not self.series_name.strip():
            raise ValueError("A series position must name the series it counts within")

    def as_metadata(self) -> dict[str, Any]:
        named = {SERIES_NAME_KEY: self.series_name.strip()}
        if self.position is None:
            return named
        return {
            **named,
            SERIES_POSITION_KEY: self.position,
            SERIES_AUTHORITY_KEY: self.authority.value,
        }


class ProviderError(Exception):
    def __init__(self, provider_name: str, message: str) -> None:
        self.provider_name = provider_name
        self.message = message
        super().__init__(f"{provider_name}: {message}")


class ProviderRefusedError(ProviderError):
    """The credential was refused, not the request. Its own type because the
    refusal can arrive as a 200 with an errors body. Only *codes* is safe to
    persist; the message may carry the request URL.
    """

    def __init__(self, provider_name: str, message: str, *, codes: str) -> None:
        super().__init__(provider_name, message)
        self.codes = sanitize_for_log(codes)


#: Where a provider that states no precedence sits: behind every provider that
#: states one, so installing one cannot displace the order the rest agreed on.
TRAILING_PRECEDENCE = 100


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
    def precedence(self) -> int:
        """Where this provider is tried in the default order, lowest first. The
        shipped providers leave gaps, so another can state a place between two.
        """
        return TRAILING_PRECEDENCE

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

    def candidate_from_url(
        self, item: ContentItem, url: str, config: dict[str, Any]
    ) -> Candidate | None:
        """The record *url* names for *item*, None for a link this provider
        cannot read — every link, by default. Its ``record_id`` must be one
        :meth:`accepts_record_id` admits, or pinning refuses the row just offered.
        """
        return None

    def accepts_record_id(self, record_id: str) -> bool:
        """Whether a pin naming this record is one :meth:`enrich` can look up.
        Refusing by default, so a provider that never reads
        :func:`pinned_record` cannot be handed an id every run then ignores.
        """
        return False

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


def flags_no_credential(provider: EnrichmentProvider) -> bool:
    """Whether a provider needing an api key marks no field sensitive."""
    return provider.requires_api_key and not any(
        declared.sensitive for declared in provider.get_config_schema()
    )


def stored_config_schema(provider: EnrichmentProvider) -> list[ConfigField]:
    """Read wherever a field's value is stored: an unflagged credential would
    have the operator's token typed into a plaintext ``settings`` row, so a
    provider that flags none has its text fields read as sensitive instead.
    """
    declared = provider.get_config_schema()
    if not flags_no_credential(provider):
        return declared
    return [
        replace(entry, sensitive=True) if entry.field_type is str else entry
        for entry in declared
    ]


def states_a_match(provider: EnrichmentProvider) -> bool:
    """Whether the first-success loop may settle an item on this provider."""
    return _overrides(type(provider), "enrich")


def states_a_series_ordinal(provider: EnrichmentProvider) -> bool:
    """Whether the ordinal pass has anything to ask this provider."""
    return _overrides(type(provider), "fetch_series_ordinal")


def offers_candidates(provider: EnrichmentProvider) -> bool:
    """Whether the picker has records to ask this provider for, which a provider
    that searches has whether or not it can enrich from one.
    """
    return _overrides(type(provider), "search")


def recognises_urls(provider: EnrichmentProvider) -> bool:
    """Whether a pasted link is worth spending this provider's rate limit on."""
    return _overrides(type(provider), "candidate_from_url")


def accepts_a_pin(provider: EnrichmentProvider) -> bool:
    """Whether a pin means anything to this provider, so the refusal can name
    the ones it does.
    """
    return _overrides(type(provider), "accepts_record_id")
