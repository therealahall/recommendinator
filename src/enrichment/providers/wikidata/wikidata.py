import logging
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import requests

from src import __version__ as APP_VERSION
from src.enrichment.matching import (
    MINIMUM_TITLE_SIMILARITY,
    best_match_index,
    title_similarity,
    year_of,
)
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    ProviderError,
    SeriesOrdinal,
    log_search_title,
)
from src.models.content import ContentItem, ContentType
from src.utils.request_errors import scrub_request_error
from src.utils.series import split_series_from_title

logger = logging.getLogger(__name__)

WIKIDATA_BASE = "https://www.wikidata.org"
SEARCH_URL = f"{WIKIDATA_BASE}/w/api.php"
ITEMS_URL = f"{WIKIDATA_BASE}/w/rest.php/wikibase/v1/entities/items"

USER_AGENT = (
    f"Recommendinator/{APP_VERSION} (+https://github.com/therealahall/recommendinator)"
)
REQUEST_HEADERS = {"User-Agent": USER_AGENT}

REQUEST_TIMEOUT = 10

PART_OF_THE_SERIES = "P179"
SERIES_ORDINAL_QUALIFIER = "P1545"
INSTANCE_OF = "P31"
DATE_PROPERTIES = ("P577", "P571", "P580")

SEARCH_LIMIT = 7

MAX_ENTITY_LOOKUPS = 3

MAX_POSITION = 1000

QID = re.compile(r"^Q[1-9][0-9]*$")

LITERARY_WORK = "Q7725634"
WRITTEN_WORK = "Q47461344"
NOVEL = "Q8261"
FILM = "Q11424"
FEATURE_FILM = "Q24869"
TELEVISION_FILM = "Q506240"
TELEVISION_SERIES = "Q5398426"
TELEVISION_PROGRAM = "Q15416"
MINISERIES = "Q1259759"
VIDEO_GAME = "Q7889"
VIDEO_GAME_REMAKE = "Q4393107"

INSTANCE_OF_BY_TYPE: dict[ContentType, frozenset[str]] = {
    ContentType.BOOK: frozenset({LITERARY_WORK, WRITTEN_WORK, NOVEL}),
    ContentType.MOVIE: frozenset({FILM, FEATURE_FILM, TELEVISION_FILM}),
    ContentType.TV_SHOW: frozenset({TELEVISION_SERIES, TELEVISION_PROGRAM, MINISERIES}),
    ContentType.VIDEO_GAME: frozenset({VIDEO_GAME, VIDEO_GAME_REMAKE}),
}


def _hit_titles(hit: Mapping[str, Any]) -> list[str]:
    match = hit.get("match")
    aliases = hit.get("aliases")
    named: list[Any] = [hit.get("label")]
    if isinstance(match, dict):
        named.append(match.get("text"))
    if isinstance(aliases, list):
        named.extend(aliases)
    return [name for name in named if isinstance(name, str) and name.strip()]


def _best_similarity(search_title: str, titles: Sequence[str]) -> float:
    return max((title_similarity(search_title, title) for title in titles), default=0.0)


def _undeprecated_claims(
    statements: Mapping[str, Any], property_id: str
) -> list[dict[str, Any]]:
    claims = statements.get(property_id)
    if not isinstance(claims, list):
        return []
    return [
        claim
        for claim in claims
        if isinstance(claim, dict) and claim.get("rank") != "deprecated"
    ]


def _stated_value(node: Mapping[str, Any]) -> Any:
    value = node.get("value")
    if not isinstance(value, dict) or value.get("type") != "value":
        return None
    return value.get("content")


def _qualifier_property(qualifier: Mapping[str, Any]) -> str | None:
    prop = qualifier.get("property")
    identifier = prop.get("id") if isinstance(prop, dict) else None
    return identifier if isinstance(identifier, str) else None


def _instance_of(statements: Mapping[str, Any]) -> set[str]:
    return {
        stated
        for claim in _undeprecated_claims(statements, INSTANCE_OF)
        if isinstance(stated := _stated_value(claim), str)
    }


def _stated_year(statements: Mapping[str, Any]) -> int | None:
    for property_id in DATE_PROPERTIES:
        for claim in _undeprecated_claims(statements, property_id):
            stated = _stated_value(claim)
            signed_time = stated.get("time") if isinstance(stated, dict) else None
            if not isinstance(signed_time, str):
                continue
            year = year_of(signed_time.lstrip("+"))
            if year is not None:
                return year
    return None


def _ordinal_position(stated: Any) -> float | None:
    try:
        position = float(str(stated))
    except ValueError:
        return None
    if not math.isfinite(position) or not 0 <= position <= MAX_POSITION:
        return None
    return position


def _stated_ordinal(statements: Mapping[str, Any]) -> tuple[str, float] | None:
    # A series stating no ordinal yields none. Counting the P155/P156
    # preceded-by chain would invent the rank the qualifier exists to state,
    # and it would be written at the authority of one Wikidata does state.
    for claim in _undeprecated_claims(statements, PART_OF_THE_SERIES):
        series_id = _stated_value(claim)
        if not isinstance(series_id, str) or not QID.match(series_id):
            continue
        qualifiers = claim.get("qualifiers")
        for qualifier in qualifiers if isinstance(qualifiers, list) else []:
            if not isinstance(qualifier, dict):
                continue
            if _qualifier_property(qualifier) != SERIES_ORDINAL_QUALIFIER:
                continue
            position = _ordinal_position(_stated_value(qualifier))
            if position is not None:
                return series_id, position
    return None


def _item_year(item: ContentItem) -> int | None:
    metadata = item.metadata or {}
    return year_of(metadata.get("release_year") or metadata.get("year_published"))


class WikidataProvider(EnrichmentProvider):
    @property
    def name(self) -> str:
        return "wikidata"

    @property
    def display_name(self) -> str:
        return "Wikidata"

    @property
    def description(self) -> str:
        return "Take the series position Wikidata states for a work"

    @property
    def content_types(self) -> list[ContentType]:
        return list(INSTANCE_OF_BY_TYPE)

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def rate_limit_requests_per_second(self) -> float:
        # One item is a search, up to three statement reads and a label, and
        # Wikidata is donated infrastructure rather than a bought quota.
        return 1.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="enabled",
                field_type=bool,
                required=False,
                default=False,
                description="Enable Wikidata series-position enrichment",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )
        search_title, _marked_up_series = split_series_from_title(item.title)
        log_search_title(logger, item.title, search_title)

        statements = self._matched_entity(item, search_title, content_type)
        if statements is None:
            return None

        stated = _stated_ordinal(statements)
        if stated is None:
            return None

        series_id, position = stated
        return SeriesOrdinal(position=position, series=self._label(series_id))

    def _matched_entity(
        self, item: ContentItem, search_title: str, content_type: ContentType
    ) -> dict[str, Any] | None:
        allowed = INSTANCE_OF_BY_TYPE[content_type]
        validated: list[tuple[list[str], int | None, dict[str, Any]]] = []

        for hit in self._plausible_hits(search_title):
            statements = self._statements(str(hit["id"]))
            if not _instance_of(statements) & allowed:
                continue
            validated.append((_hit_titles(hit), _stated_year(statements), statements))

        index = best_match_index(
            search_title,
            _item_year(item),
            [(titles, year) for titles, year, _statements in validated],
        )
        return None if index is None else validated[index][2]

    def _plausible_hits(self, search_title: str) -> list[dict[str, Any]]:
        payload = self._get(
            SEARCH_URL,
            {
                "action": "wbsearchentities",
                "search": search_title,
                "language": "en",
                "type": "item",
                "limit": SEARCH_LIMIT,
                "format": "json",
            },
        )
        hits = payload.get("search") if isinstance(payload, dict) else None

        plausible: list[dict[str, Any]] = []
        for hit in hits if isinstance(hits, list) else []:
            if not isinstance(hit, dict) or not QID.match(str(hit.get("id", ""))):
                continue
            similarity = _best_similarity(search_title, _hit_titles(hit))
            if similarity < MINIMUM_TITLE_SIMILARITY:
                continue
            plausible.append(hit)
            if len(plausible) == MAX_ENTITY_LOOKUPS:
                break
        return plausible

    def _statements(self, entity_id: str) -> dict[str, Any]:
        payload = self._get(f"{ITEMS_URL}/{entity_id}/statements")
        return payload if isinstance(payload, dict) else {}

    def _label(self, entity_id: str) -> str | None:
        payload = self._get(f"{ITEMS_URL}/{entity_id}/labels/en", unnamed_is_none=True)
        name = payload.strip() if isinstance(payload, str) else ""
        return name or None

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        unnamed_is_none: bool = False,
    ) -> Any:
        try:
            response = requests.get(
                url,
                params=params,
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if unnamed_is_none and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to reach Wikidata: {scrub_request_error(error)}"
            ) from error
