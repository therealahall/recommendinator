"""Trakt integration plugin for importing watched, rated, and watchlisted media.

Credential contract (depended on by the web/CLI auth task)
----------------------------------------------------------
Storage ``source_id`` is ``"trakt"``.  The config schema exposes four fields:

- ``client_id``     (str, required, sensitive=False) — the user's own Trakt API
  application client id.  Not secret, but identifies the app for every request
  via the ``trakt-api-key`` header.
- ``client_secret`` (str, required, sensitive=True)  — the user's Trakt API
  application secret, used only for the OAuth token exchange.
- ``refresh_token`` (str, required, sensitive=True)  — obtained later via the
  device-code OAuth flow.  It is NOT present in YAML; the auth task writes it to
  the encrypted credential store.  Trakt rotates this token on every refresh, so
  the new value is persisted through the ``_on_credential_rotated`` callback.
- ``include_watchlist`` (bool, required=False, default=True) — whether to import
  watchlisted items as ``UNREAD``.

On each fetch the plugin exchanges ``refresh_token`` (plus ``client_id`` and
``client_secret``) for a fresh access token via ``POST /oauth/token``
(``grant_type=refresh_token``).  All Trakt requests carry the standard headers:
``trakt-api-version: 2``, ``trakt-api-key: <client_id>``, and
``Authorization: Bearer <access_token>``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    CredentialUpdateCallback,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_TOKEN_URL = f"{TRAKT_API_URL}/oauth/token"

# Sensitive fields that must be present in config or the credential DB.
_REQUIRED_SENSITIVE_FIELDS = ("client_secret", "refresh_token")


class TraktAPIError(Exception):
    """Exception raised for Trakt API errors."""

    pass


def refresh_access_token(
    refresh_token: str, client_id: str, client_secret: str
) -> dict[str, str]:
    """Exchange a Trakt OAuth refresh token for a fresh access token.

    Args:
        refresh_token: Trakt OAuth refresh token from the device-code flow.
        client_id: The user's Trakt API application client id.
        client_secret: The user's Trakt API application secret.

    Returns:
        Dictionary with 'access_token' and 'refresh_token' keys. Trakt rotates
        the refresh token, so the returned value may differ from the input.

    Raises:
        TraktAPIError: If the token refresh fails.
    """
    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "grant_type": "refresh_token",
    }
    try:
        response = requests.post(TRAKT_TOKEN_URL, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise TraktAPIError("Token response missing access_token")
        return {
            "access_token": access_token,
            "refresh_token": data.get("refresh_token", refresh_token),
        }
    except requests.RequestException as error:
        logger.error("Error refreshing Trakt access token: %s", type(error).__name__)
        raise TraktAPIError(
            f"Failed to refresh access token: {type(error).__name__}"
        ) from error


def _trakt_headers(access_token: str, client_id: str) -> dict[str, str]:
    """Build the standard Trakt API request headers."""
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
        "Authorization": f"Bearer {access_token}",
    }


def fetch_list(
    endpoint: str,
    access_token: str,
    client_id: str,
    extended: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch a Trakt sync list, following pagination if present.

    Args:
        endpoint: API path (e.g. "/sync/watched/movies").
        access_token: Valid Trakt OAuth access token.
        client_id: Trakt API application client id (sent as trakt-api-key).
        extended: Optional value for the ``extended`` query parameter.

    Returns:
        Flat list of result objects across all pages.

    Raises:
        TraktAPIError: If any request fails.
    """
    headers = _trakt_headers(access_token, client_id)
    base_params: dict[str, Any] = {}
    if extended:
        base_params["extended"] = extended

    results: list[dict[str, Any]] = []
    page = 1
    page_count = 1

    while page <= page_count:
        params = dict(base_params)
        params["page"] = page
        try:
            response = requests.get(
                f"{TRAKT_API_URL}{endpoint}",
                params=params,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            results.extend(response.json())
            try:
                page_count = int(response.headers.get("X-Pagination-Page-Count", page))
            except (ValueError, TypeError):
                # Malformed/missing header: stop after the current page.
                page_count = page
            page += 1
        except requests.RequestException as error:
            logger.error(
                "Error fetching Trakt %s (page %d): %s",
                endpoint,
                page,
                type(error).__name__,
            )
            raise TraktAPIError(
                f"Failed to fetch {endpoint}: {type(error).__name__}"
            ) from error

    return results


def fetch_show_season_count(
    trakt_id: int,
    access_token: str,
    client_id: str,
) -> int:
    """Fetch a show's true number of real seasons (excluding specials).

    Calls ``GET /shows/{id}/seasons``, which returns one object per season with
    a ``number`` field (season 0 is specials). The watched-shows sync endpoint
    only returns seasons the user has already watched, so this extra call is the
    only way to learn how many seasons exist for a partially-watched show.

    Args:
        trakt_id: The show's Trakt id.
        access_token: Valid Trakt OAuth access token.
        client_id: Trakt API application client id (sent as trakt-api-key).

    Returns:
        Count of seasons whose ``number`` is >= 1 (specials excluded).

    Raises:
        TraktAPIError: If the request fails.
    """
    headers = _trakt_headers(access_token, client_id)
    endpoint = f"/shows/{trakt_id}/seasons"
    try:
        response = requests.get(
            f"{TRAKT_API_URL}{endpoint}",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        seasons = response.json()
    except requests.RequestException as error:
        logger.error("Error fetching Trakt %s: %s", endpoint, type(error).__name__)
        raise TraktAPIError(
            f"Failed to fetch {endpoint}: {type(error).__name__}"
        ) from error

    return sum(
        1
        for season in seasons
        if isinstance(season.get("number"), int) and season["number"] >= 1
    )


def _parse_completed_date(raw: str | None) -> date | None:
    """Parse a Trakt ISO 8601 timestamp into a date, or None if absent/invalid."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _show_season_progress(seasons: list[dict[str, Any]]) -> tuple[list[int], int]:
    """Compute watched seasons and the highest watched season number for a show.

    Season 0 (specials) is excluded from both values.

    Args:
        seasons: The ``seasons`` array from a watched-show entry.

    Returns:
        Tuple of (sorted watched season numbers, highest watched season number).
        The second value is NOT the true season total — the watched-shows sync
        endpoint only reports seasons the user has watched. It is a placeholder
        used only for COMPLETED shows, which are excluded from season expansion;
        in-progress shows overwrite it with the true count from
        ``fetch_show_season_count``.
    """
    watched: list[int] = []
    for season in seasons:
        number = season.get("number")
        if not isinstance(number, int) or number < 1:
            continue
        if season.get("episodes"):
            watched.append(number)
    watched.sort()
    highest_watched_season = max(watched) if watched else 0
    return watched, highest_watched_season


def _watched_episode_count(seasons: list[dict[str, Any]]) -> int:
    """Count watched episodes across real seasons (excluding specials)."""
    count = 0
    for season in seasons:
        number = season.get("number")
        if not isinstance(number, int) or number < 1:
            continue
        count += len(season.get("episodes") or [])
    return count


def _media_metadata(media: dict[str, Any]) -> dict[str, Any]:
    """Build the base metadata dict shared by movie and show items."""
    ids = media.get("ids") or {}
    metadata: dict[str, Any] = {"trakt_id": ids.get("trakt")}
    if ids.get("slug"):
        metadata["slug"] = ids["slug"]
    if ids.get("imdb"):
        metadata["imdb_id"] = ids["imdb"]
    if ids.get("tmdb"):
        metadata["tmdb_id"] = ids["tmdb"]
    if media.get("year"):
        metadata["year"] = media["year"]
    if media.get("genres"):
        metadata["genres"] = media["genres"]
    return metadata


class TraktPlugin(SourcePlugin):
    """Plugin for importing watched, rated, and watchlisted media from Trakt.

    Fetches watched movies/shows, ratings, and (optionally) the watchlist,
    de-duplicating each title across lists into a single ContentItem.
    """

    @property
    def name(self) -> str:
        return "trakt"

    @property
    def display_name(self) -> str:
        return "Trakt"

    @property
    def description(self) -> str:
        return "Import watched, rated, and watchlisted movies and TV shows from Trakt"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.TV_SHOW, ContentType.MOVIE]

    @property
    def requires_api_key(self) -> bool:
        return True

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Normalise Trakt YAML config (strip credentials, apply defaults)."""
        return {
            "client_id": (raw_config.get("client_id") or "").strip(),
            "client_secret": (raw_config.get("client_secret") or "").strip(),
            "refresh_token": (raw_config.get("refresh_token") or "").strip(),
            "include_watchlist": raw_config.get("include_watchlist", True),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="client_id",
                field_type=str,
                required=True,
                sensitive=False,
                description="Your Trakt API application client id",
            ),
            ConfigField(
                name="client_secret",
                field_type=str,
                required=True,
                sensitive=True,
                description="Your Trakt API application client secret",
            ),
            ConfigField(
                name="refresh_token",
                field_type=str,
                required=True,
                sensitive=True,
                description="Trakt OAuth refresh token (obtained via device-code flow)",
            ),
            ConfigField(
                name="include_watchlist",
                field_type=bool,
                required=False,
                default=True,
                description="Import watchlisted titles as unread items",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []

        if not (config.get("client_id") or "").strip():
            errors.append(
                "'client_id' is required. Create a Trakt API application at "
                "https://trakt.tv/oauth/applications and provide its client id."
            )

        source_id = config.get("_source_id", self.name)
        db_creds: dict[str, Any] = {}
        if storage is not None:
            db_creds = storage.get_credentials_for_source(user_id, source_id)

        for field_name in _REQUIRED_SENSITIVE_FIELDS:
            if (config.get(field_name) or "").strip():
                continue
            if (db_creds.get(field_name) or "").strip():
                continue
            errors.append(
                f"'{field_name}' is required. "
                "Use the web UI (Data tab) to connect your Trakt account, "
                "or see README.md for manual setup instructions."
            )

        return errors

    def normalize_rating(self, raw_rating: Any) -> int | None:
        """Normalize a Trakt 1-10 rating to the 1-5 scale.

        Trakt ratings are integers 1-10 (0/None means unrated). The 10-point
        scale is halved and rounded up so every rated value maps to 1-5 and a
        rated item never normalizes to 0.

        Args:
            raw_rating: Raw Trakt rating (1-10), 0, or None.

        Returns:
            Normalized rating (1-5), or None if unrated/invalid.
        """
        if raw_rating is None:
            return None
        try:
            rating = int(raw_rating)
        except (ValueError, TypeError):
            return None
        if rating <= 0:
            return None
        return max(1, min(5, (rating + 1) // 2))

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch watched, rated, and watchlisted media from Trakt.

        Args:
            config: Must contain 'client_id', 'client_secret', 'refresh_token'.
                Optional: 'include_watchlist'.
            progress_callback: Optional callback for progress updates.

        Yields:
            One ContentItem per unique title across the synced lists.

        Raises:
            SourceError: If the Trakt API returns an error.
        """
        on_rotated = config.get("_on_credential_rotated")
        if not callable(on_rotated):
            on_rotated = None

        try:
            yield from self._fetch_items(
                client_id=(config.get("client_id") or "").strip(),
                client_secret=(config.get("client_secret") or "").strip(),
                refresh_token=(config.get("refresh_token") or "").strip(),
                include_watchlist=config.get("include_watchlist", True),
                source=self.get_source_identifier(config),
                progress_callback=progress_callback,
                on_credential_rotated=on_rotated,
            )
        except TraktAPIError as error:
            raise SourceError(self.name, str(error)) from error

    def _fetch_items(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        include_watchlist: bool,
        source: str,
        progress_callback: ProgressCallback | None,
        on_credential_rotated: CredentialUpdateCallback | None,
    ) -> Iterator[ContentItem]:
        """Fetch, merge, and yield Trakt items (see fetch for the public API)."""
        logger.info("Refreshing Trakt access token...")
        tokens = refresh_access_token(refresh_token, client_id, client_secret)
        access_token = tokens["access_token"]

        new_refresh = tokens.get("refresh_token")
        if new_refresh and new_refresh != refresh_token and on_credential_rotated:
            on_credential_rotated("refresh_token", new_refresh)
            logger.info("Trakt refresh token rotated and persisted")

        # Items keyed by (content_type, trakt_id) so the same title across lists
        # merges into one ContentItem. Watched status takes priority over
        # watchlist; ratings attach to whichever entry exists.
        items: dict[tuple[ContentType, int], ContentItem] = {}

        self._add_watched_movies(items, access_token, client_id, source)
        if progress_callback:
            progress_callback(len(items), None, "Fetching watched shows...")

        self._add_watched_shows(items, access_token, client_id, source)
        if progress_callback:
            progress_callback(len(items), None, "Fetching ratings...")

        self._apply_ratings(items, access_token, client_id, source)

        if include_watchlist:
            if progress_callback:
                progress_callback(len(items), None, "Fetching watchlist...")
            self._add_watchlist(items, access_token, client_id, source)

        total = len(items)
        for index, item in enumerate(items.values(), start=1):
            if progress_callback:
                progress_callback(index, total, item.title)
            yield item

    def _add_watched_movies(
        self,
        items: dict[tuple[ContentType, int], ContentItem],
        access_token: str,
        client_id: str,
        source: str,
    ) -> None:
        for entry in fetch_list("/sync/watched/movies", access_token, client_id):
            movie = entry.get("movie") or {}
            trakt_id = (movie.get("ids") or {}).get("trakt")
            title = movie.get("title")
            if trakt_id is None or not title:
                continue
            items[(ContentType.MOVIE, int(trakt_id))] = ContentItem(
                id=f"trakt:{trakt_id}",
                title=title,
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                rating=None,
                date_completed=_parse_completed_date(entry.get("last_watched_at")),
                metadata=_media_metadata(movie),
                source=source,
            )

    def _add_watched_shows(
        self,
        items: dict[tuple[ContentType, int], ContentItem],
        access_token: str,
        client_id: str,
        source: str,
    ) -> None:
        for entry in fetch_list(
            "/sync/watched/shows", access_token, client_id, extended="full"
        ):
            show = entry.get("show") or {}
            trakt_id = (show.get("ids") or {}).get("trakt")
            title = show.get("title")
            if trakt_id is None or not title:
                continue

            seasons = entry.get("seasons") or []
            seasons_watched, highest_watched_season = _show_season_progress(seasons)
            aired_episodes = show.get("aired_episodes") or 0
            watched_episodes = _watched_episode_count(seasons)
            fully_watched = aired_episodes > 0 and watched_episodes >= aired_episodes

            # The sync endpoint only returns watched seasons, so the high-water
            # mark cannot see the user's unwatched later seasons. For an
            # in-progress show, fetch the true real-season count so season
            # expansion can recommend the next seasons. Completed shows are
            # excluded from expansion, so they reuse the high-water mark.
            if fully_watched:
                total_seasons = highest_watched_season
            else:
                total_seasons = fetch_show_season_count(
                    int(trakt_id), access_token, client_id
                )

            metadata = _media_metadata(show)
            metadata["seasons_watched"] = seasons_watched
            metadata["total_seasons"] = total_seasons

            items[(ContentType.TV_SHOW, int(trakt_id))] = ContentItem(
                id=f"trakt:{trakt_id}",
                title=title,
                content_type=ContentType.TV_SHOW,
                status=(
                    ConsumptionStatus.COMPLETED
                    if fully_watched
                    else ConsumptionStatus.CURRENTLY_CONSUMING
                ),
                date_completed=(
                    _parse_completed_date(entry.get("last_watched_at"))
                    if fully_watched
                    else None
                ),
                rating=None,
                metadata=metadata,
                source=source,
            )

    def _apply_ratings(
        self,
        items: dict[tuple[ContentType, int], ContentItem],
        access_token: str,
        client_id: str,
        source: str,
    ) -> None:
        for content_type, endpoint, media_key in (
            (ContentType.MOVIE, "/sync/ratings/movies", "movie"),
            (ContentType.TV_SHOW, "/sync/ratings/shows", "show"),
        ):
            for entry in fetch_list(endpoint, access_token, client_id):
                media = entry.get(media_key) or {}
                trakt_id = (media.get("ids") or {}).get("trakt")
                title = media.get("title")
                if trakt_id is None or not title:
                    continue
                rating = self.normalize_rating(entry.get("rating"))
                key = (content_type, int(trakt_id))
                existing = items.get(key)
                if existing is not None:
                    existing.rating = rating
                    continue
                items[key] = ContentItem(
                    id=f"trakt:{trakt_id}",
                    title=title,
                    content_type=content_type,
                    status=ConsumptionStatus.UNREAD,
                    rating=rating,
                    metadata=_media_metadata(media),
                    source=source,
                )

    def _add_watchlist(
        self,
        items: dict[tuple[ContentType, int], ContentItem],
        access_token: str,
        client_id: str,
        source: str,
    ) -> None:
        for content_type, endpoint, media_key in (
            (ContentType.MOVIE, "/sync/watchlist/movies", "movie"),
            (ContentType.TV_SHOW, "/sync/watchlist/shows", "show"),
        ):
            for entry in fetch_list(endpoint, access_token, client_id):
                media = entry.get(media_key) or {}
                trakt_id = (media.get("ids") or {}).get("trakt")
                title = media.get("title")
                if trakt_id is None or not title:
                    continue
                key = (content_type, int(trakt_id))
                if key in items:
                    continue
                items[key] = ContentItem(
                    id=f"trakt:{trakt_id}",
                    title=title,
                    content_type=content_type,
                    status=ConsumptionStatus.UNREAD,
                    rating=None,
                    metadata=_media_metadata(media),
                    source=source,
                )
