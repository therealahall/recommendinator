import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    SeriesOrdinal,
    log_search_title,
)
from src.ingestion.urls import (
    MAX_SAME_ORIGIN_REDIRECTS,
    REDIRECT_STATUSES,
    REQUEST_TIMEOUT,
    same_origin,
)
from src.models.content import ContentItem, ContentType, get_enum_value
from src.utils.matching import best_match_index, year_of
from src.utils.request_errors import scrub_request_error
from src.utils.text import clean_game_title_for_search, sanitize_for_log

logger = logging.getLogger(__name__)

GAMES_URL = "https://api.igdb.com/v4/games"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

_GAME_FIELDS = (
    "name,alternative_names.name,collections.name,franchise.name,franchises.name,"
    "genres.name,themes.name,summary,cover.url,first_release_date"
)

_CANDIDATE_LIMIT = 10

# Apicalypse ends a statement at `;` and a string at `"`, either of which a
# title carries. Dropped, not escaped: the hits are scored on similarity after.
_UNQUOTABLE = re.compile(r'["\\;\n\r]')

_THUMBNAIL_IN_PATH = "t_thumb"
_FULL_SIZE_IN_PATH = "t_cover_big"


def _names(value: Any) -> list[str]:
    entries = value if isinstance(value, list) else [value]
    return [
        name
        for entry in entries
        if isinstance(entry, dict) and (name := str(entry.get("name") or "").strip())
    ]


def _titles(game: dict[str, Any]) -> list[str]:
    return [str(game.get("name") or ""), *_names(game.get("alternative_names"))]


def _release_year(game: dict[str, Any]) -> int | None:
    """A Unix timestamp, so its leading digits are not the year in it."""
    stated = game.get("first_release_date")
    if isinstance(stated, bool) or not isinstance(stated, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(stated, tz=UTC).year
    except (OSError, OverflowError, ValueError):
        return None


def _https_cover(cover: Any) -> str | None:
    """The one cover URL a third party chooses freely, so it is narrowed here."""
    url = cover.get("url") if isinstance(cover, dict) else None
    if not isinstance(url, str):
        return None
    absolute = f"https:{url}" if url.startswith("//") else url
    if not absolute.startswith("https://"):
        return None
    return absolute.replace(_THUMBNAIL_IN_PATH, _FULL_SIZE_IN_PATH)


def _series_name(game: dict[str, Any]) -> str | None:
    """A collection is the narrower grouping — Planescape, not Dungeons &
    Dragons — and IGDB ranks neither list, so within one the first stated wins.
    """
    named = (
        _names(game.get("collections"))
        or _names(game.get("franchise"))
        or _names(game.get("franchises"))
    )
    return named[0] if named else None


def _result(game: dict[str, Any]) -> EnrichmentResult:
    extra: dict[str, Any] = {}
    year = _release_year(game)
    if year is not None:
        extra["release_year"] = year

    genres = _names(game.get("genres"))
    themes = _names(game.get("themes"))
    return EnrichmentResult(
        genres=genres or None,
        tags=themes or None,
        description=str(game.get("summary") or "").strip() or None,
        cover_url=_https_cover(game.get("cover")),
        extra_metadata=extra,
        match_quality="medium",
    )


def _search_body(title: str) -> str:
    return (
        f'search "{_UNQUOTABLE.sub("", title)}"; '
        f"fields {_GAME_FIELDS}; limit {_CANDIDATE_LIMIT};"
    )


def _credentials(config: dict[str, Any]) -> tuple[str, str] | None:
    client_id = str(config.get("client_id") or "").strip()
    client_secret = str(config.get("client_secret") or "").strip()
    return (client_id, client_secret) if client_id and client_secret else None


def _lifetime_seconds(stated: Any) -> float:
    try:
        return max(float(stated), 0.0)
    except (TypeError, ValueError):
        return 0.0


class IGDBProvider(EnrichmentProvider):
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def name(self) -> str:
        return "igdb"

    @property
    def display_name(self) -> str:
        return "IGDB"

    @property
    def description(self) -> str:
        return "Genres, themes, description, cover and series name for video games"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 4.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="enabled",
                field_type=bool,
                required=False,
                default=False,
                description="Enable IGDB enrichment",
            ),
            ConfigField(
                name="client_id",
                field_type=str,
                required=True,
                description=(
                    "Twitch application client ID "
                    "(get from https://dev.twitch.tv/console/apps)"
                ),
                sensitive=True,
            ),
            ConfigField(
                name="client_secret",
                field_type=str,
                required=True,
                description="Twitch application client secret",
                sensitive=True,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return [
            f"'{field}' is required for IGDB provider"
            for field in ("client_id", "client_secret")
            if not config.get(field)
        ]

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        credentials = self._credentials_for(item, config)
        if credentials is None:
            return None
        game = self._matched_game(item, credentials)
        if game is None:
            return EnrichmentResult(match_quality="not_found")
        return _result(game)

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        credentials = self._credentials_for(item, config)
        if credentials is None:
            return None
        game = self._matched_game(item, credentials)
        if game is None:
            return None
        name = _series_name(game)
        if name is None:
            return None
        # No collection, franchise or membership at IGDB holds an ordinal, so it
        # names a series and never counts within one.
        return SeriesOrdinal(position=None, series_name=name)

    def _credentials_for(
        self, item: ContentItem, config: dict[str, Any]
    ) -> tuple[str, str] | None:
        if get_enum_value(item.content_type) != ContentType.VIDEO_GAME.value:
            return None
        credentials = _credentials(config)
        if credentials is None:
            logger.debug(
                "IGDB is enabled with no client credentials stored; stating nothing"
            )
        return credentials

    def _matched_game(
        self, item: ContentItem, credentials: tuple[str, str]
    ) -> dict[str, Any] | None:
        searched = clean_game_title_for_search(item.title)
        log_search_title(logger, item.title, searched)

        games = self._games(_search_body(searched), credentials)
        index = best_match_index(
            searched,
            year_of((item.metadata or {}).get("release_year")),
            [(_titles(game), _release_year(game)) for game in games],
            # A store sells 'Ultima I' where IGDB catalogues its fuller name.
            allow_subtitled=True,
        )
        return None if index is None else games[index]

    def _games(self, body: str, credentials: tuple[str, str]) -> list[dict[str, Any]]:
        response = self._query(body, credentials, fresh_token=False)
        if response.status_code == 401:
            response = self._query(body, credentials, fresh_token=True)
        self._checked(response, "IGDB request failed")

        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [game for game in payload if isinstance(game, dict)]

    def _query(
        self, body: str, credentials: tuple[str, str], fresh_token: bool
    ) -> requests.Response:
        client_id, _client_secret = credentials
        token = self._access_token(credentials, fresh_token)
        return self._post(
            GAMES_URL,
            body,
            {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

    def _access_token(self, credentials: tuple[str, str], fresh_token: bool) -> str:
        if (
            not fresh_token
            and self._token is not None
            and time.monotonic() < self._token_expires_at
        ):
            return self._token

        client_id, client_secret = credentials
        response = self._post(
            TWITCH_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            {"Accept": "application/json"},
        )
        self._checked(response, "Twitch refused the client credentials")

        payload = response.json()
        stated = payload if isinstance(payload, dict) else {}
        token = str(stated.get("access_token") or "")
        if not token:
            raise ProviderError(self.name, "Twitch stated no app access token")

        self._token = token
        self._token_expires_at = time.monotonic() + _lifetime_seconds(
            stated.get("expires_in")
        )
        return token

    def _checked(self, response: requests.Response, failure: str) -> None:
        try:
            response.raise_for_status()
        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"{failure}: {scrub_request_error(error)}"
            ) from error

    def _post(self, url: str, data: Any, headers: dict[str, str]) -> requests.Response:
        """``requests`` replays the Authorization header onto a redirect's host."""
        current = url
        for _ in range(MAX_SAME_ORIGIN_REDIRECTS):
            try:
                response = requests.post(
                    current,
                    data=data,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                raise ProviderError(
                    self.name, f"IGDB request failed: {scrub_request_error(error)}"
                ) from error

            if response.status_code not in REDIRECT_STATUSES:
                return response

            location = response.headers.get("Location")
            if not location:
                return response
            target = urljoin(current, location)
            if not same_origin(url, target):
                raise ProviderError(
                    self.name,
                    f"Refused a redirect to {sanitize_for_log(target)}: it leaves "
                    "the origin the IGDB credentials are sent to.",
                )
            current = target

        raise ProviderError(
            self.name, f"IGDB redirected more than {MAX_SAME_ORIGIN_REDIRECTS} times."
        )
