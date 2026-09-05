"""Tautulli records what was played on Plex, so its rows are consumption events
rather than a shelf. It reports what was watched and leaves completion to the
readers downstream.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.ingestion.urls import (
    MAX_SAME_ORIGIN_REDIRECTS,
    REDIRECT_STATUSES,
    REQUEST_TIMEOUT,
    redirect_refusal,
    same_origin,
    source_url_error,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.request_errors import scrub_request_error
from src.utils.text import exception_for_log, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8181"

_HISTORY_PAGE_SIZE = 1000

# Plex files specials as season 0; they are not part of a show's run, so no
# mapping this plugin emits counts them.
_SPECIALS_SEASON = 0


@dataclass
class _WatchedMovie:
    title: str
    year: int | None
    last_played: datetime


@dataclass
class _WatchedShow:
    title: str
    rating_key: int | None = None
    episodes: dict[int, set[str]] = field(default_factory=dict)
    last_played: dict[int, datetime] = field(default_factory=dict)


def _result_data(plugin_name: str, payload: Any, command: str) -> Any:
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        raise SourceError(plugin_name, f"Tautulli sent no response for '{command}'")
    if response.get("result") != "success":
        message = sanitize_for_log(str(response.get("message") or "no reason given"))
        raise SourceError(plugin_name, f"Tautulli refused '{command}': {message}")
    return response.get("data")


def _to_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _release_year(row: dict[str, Any]) -> int | None:
    year = _to_int(row.get("year"))
    if year is not None:
        return year
    try:
        return date.fromisoformat(str(row.get("originally_available_at"))).year
    except ValueError:
        return None


def _played_at(row: dict[str, Any]) -> datetime | None:
    epoch = _to_int(row.get("date"))
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _watched_fully(row: dict[str, Any]) -> bool:
    """Tautulli marks a row watched against the threshold its operator set, and a
    second threshold here could only disagree with it.
    """
    try:
        return float(row.get("watched_status", 0)) == 1.0
    except (TypeError, ValueError):
        return False


def _belongs_to(row: dict[str, Any], username: str) -> bool:
    return str(row.get("user") or "").strip().casefold() == username.casefold()


def _live_rating_key(raw: Any) -> int | None:
    """Plex zeroes the key once the underlying metadata is gone, which is most of
    a long history, and ``get_children_metadata`` has nothing to answer for 0.
    """
    key = _to_int(raw)
    return key if key is not None and key > 0 else None


def _stable_id(kind: str, title: str, year: int | None) -> str:
    """Keyed on the title alone: ``rating_key`` dies with the file, and an
    episode row's ``year`` is the episode's on some Tautulli versions, so keying
    on it splits a show. Two same-titled shows therefore collapse into one.
    """
    suffix = f":{year}" if year is not None else ""
    return f"tautulli:{kind}:{title.strip().casefold()}{suffix}"


def _episode_key(row: dict[str, Any]) -> str:
    """Plex drops ``media_index`` with the rest of the metadata, so an episode
    that has lost its number is deduplicated on its own title.
    """
    number = _to_int(row.get("media_index"))
    return str(number) if number is not None else str(row.get("title") or "")


def _record_movie(movies: dict[str, _WatchedMovie], row: dict[str, Any]) -> None:
    title = str(row.get("title") or row.get("full_title") or "").strip()
    played = _played_at(row)
    if not title or played is None:
        return

    year = _release_year(row)
    movie = movies.setdefault(
        _stable_id("movie", title, year),
        _WatchedMovie(title=title, year=year, last_played=played),
    )
    movie.last_played = max(movie.last_played, played)


def _record_episode(shows: dict[str, _WatchedShow], row: dict[str, Any]) -> None:
    title = str(row.get("grandparent_title") or "").strip()
    season = _to_int(row.get("parent_media_index"))
    played = _played_at(row)
    if not title or season is None or season <= _SPECIALS_SEASON or played is None:
        return

    show = shows.setdefault(_stable_id("show", title, None), _WatchedShow(title=title))
    if show.rating_key is None:
        show.rating_key = _live_rating_key(row.get("grandparent_rating_key"))
    show.episodes.setdefault(season, set()).add(_episode_key(row))
    previous = show.last_played.get(season)
    show.last_played[season] = played if previous is None else max(previous, played)


def _movie_item(external_id: str, movie: _WatchedMovie) -> ContentItem:
    metadata: dict[str, Any] = {}
    if movie.year is not None:
        metadata["year"] = movie.year
    return ContentItem(
        id=external_id,
        title=movie.title,
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.COMPLETED,
        # Tautulli records plays, never ratings.
        rating=None,
        date_completed=movie.last_played.astimezone().date(),
        metadata=metadata,
    )


def _show_item(
    external_id: str, show: _WatchedShow, plex_season_episode_counts: dict[str, int]
) -> ContentItem:
    return ContentItem(
        id=external_id,
        title=show.title,
        content_type=ContentType.TV_SHOW,
        # What makes a show finished is the readers' question, not this plugin's:
        # it reports what was watched and what Plex holds.
        status=ConsumptionStatus.CURRENTLY_CONSUMING,
        rating=None,
        metadata={
            "episodes_watched_by_season": {
                str(season): len(episodes)
                for season, episodes in sorted(show.episodes.items())
            },
            "seasons_watched_dates": {
                str(season): played.isoformat()
                for season, played in sorted(show.last_played.items())
            },
            "plex_season_episode_counts": plex_season_episode_counts,
        },
    )


class TautulliPlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "tautulli"

    @property
    def display_name(self) -> str:
        return "Tautulli"

    @property
    def description(self) -> str:
        return "Import watched movies and TV episodes from Tautulli's Plex history"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE, ContentType.TV_SHOW]

    @property
    def requires_api_key(self) -> bool:
        return True

    @classmethod
    def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": (raw_fields.get("url") or _DEFAULT_URL).strip().rstrip("/"),
            "api_key": (raw_fields.get("api_key") or "").strip(),
            "username": (raw_fields.get("username") or "").strip(),
            "verify_ssl": raw_fields.get("verify_ssl", True),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                default=_DEFAULT_URL,
                credential_bound=True,
                description="Tautulli base URL",
            ),
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="Tautulli API key (Settings > Web Interface > API)",
            ),
            ConfigField(
                name="username",
                field_type=str,
                required=True,
                description="The Plex username whose watch history to import",
            ),
            ConfigField(
                name="verify_ssl",
                field_type=bool,
                required=False,
                default=True,
                description=(
                    "Verify the TLS certificate (disable for a self-signed "
                    "certificate or a private CA)"
                ),
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []

        if not (config.get("api_key") or "").strip():
            errors.append(
                "'api_key' is required. "
                "Find it in Tautulli: Settings > Web Interface > API"
            )
        if not (config.get("username") or "").strip():
            errors.append("'username' is required")

        url = (config.get("url") or "").strip()
        if not url:
            errors.append("'url' is required")
        else:
            url_error = source_url_error(url)
            if url_error is not None:
                errors.append(url_error)

        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        base_url = (config.get("url") or "").strip().rstrip("/")
        api_key = (config.get("api_key") or "").strip()
        username = (config.get("username") or "").strip()
        verify_ssl = config.get("verify_ssl", True)

        # A scheduled sync skips validate_config, so the api key would otherwise
        # reach whatever scheme and host the config now names.
        url_error = source_url_error(base_url)
        if url_error is not None:
            raise SourceError(self.name, url_error)

        movies: dict[str, _WatchedMovie] = {}
        shows: dict[str, _WatchedShow] = {}
        for row in self._history(base_url, api_key, verify_ssl, username, "movie"):
            _record_movie(movies, row)
        for row in self._history(base_url, api_key, verify_ssl, username, "episode"):
            _record_episode(shows, row)

        total = len(movies) + len(shows)
        processed = 0
        for movie_id, movie in movies.items():
            processed += 1
            if progress_callback:
                progress_callback(processed, total, movie.title)
            yield _movie_item(movie_id, movie)

        for show_id, show in shows.items():
            processed += 1
            if progress_callback:
                progress_callback(processed, total, show.title)
            yield _show_item(
                show_id,
                show,
                self._season_episode_counts(base_url, api_key, verify_ssl, show),
            )

        logger.info("Imported %d watched titles from Tautulli", total)

    def _season_episode_counts(
        self,
        base_url: str,
        api_key: str,
        verify_ssl: bool,
        show: _WatchedShow,
    ) -> dict[str, int]:
        """Empty for a show Plex no longer holds, which is every show whose files
        an *arr stack has since deleted. Season keys are strings: the mapping is
        stored in a JSON blob, which has no other kind of key.
        """
        if show.rating_key is None:
            return {}

        try:
            data = self._api_get(
                base_url,
                api_key,
                verify_ssl,
                "get_children_metadata",
                {"rating_key": show.rating_key, "media_type": "season"},
            )
        except SourceError as error:
            # A key still live in the history can be dead by the time it is
            # asked about, and one stale show must not end the whole sync.
            logger.warning(
                "No Plex season counts for %s: %s",
                sanitize_for_log(show.title),
                exception_for_log(error),
            )
            return {}

        children = data.get("children_list") if isinstance(data, dict) else None
        counts: dict[str, int] = {}
        for child in children or []:
            season = _to_int(child.get("media_index"))
            episodes = _to_int(child.get("children_count"))
            if season is None or season <= _SPECIALS_SEASON or episodes is None:
                continue
            counts[str(season)] = episodes
        return counts

    def _history(
        self,
        base_url: str,
        api_key: str,
        verify_ssl: bool,
        username: str,
        media_type: str,
    ) -> Iterator[dict[str, Any]]:
        """``user`` narrows the query server-side, but every row is checked
        against it too: a wrong import here is another person's viewing.
        """
        start = 0
        while True:
            data = self._api_get(
                base_url,
                api_key,
                verify_ssl,
                "get_history",
                {
                    "user": username,
                    "length": _HISTORY_PAGE_SIZE,
                    "start": start,
                    "media_type": media_type,
                    "order_column": "date",
                    "order_dir": "asc",
                },
            )
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                return

            for row in rows:
                if (
                    isinstance(row, dict)
                    and _watched_fully(row)
                    and _belongs_to(row, username)
                ):
                    yield row

            start += len(rows)
            if start >= (_to_int(data.get("recordsFiltered")) or 0):
                return

    def _api_get(
        self,
        base_url: str,
        api_key: str,
        verify_ssl: bool,
        command: str,
        params: dict[str, Any],
    ) -> Any:
        """The api key is a query parameter, so the request URL is itself a
        secret: nothing raised here may carry it, and ``requests`` would replay
        it onto whatever host a redirect names.
        """
        query = {"apikey": api_key, "cmd": command, **params}
        endpoint = f"{base_url}/api/v2"

        current = endpoint
        for _ in range(MAX_SAME_ORIGIN_REDIRECTS):
            try:
                response = requests.get(
                    current,
                    params=query,
                    timeout=REQUEST_TIMEOUT,
                    verify=verify_ssl,
                    allow_redirects=False,
                )
                location = response.headers.get("Location")
                if response.status_code not in REDIRECT_STATUSES or not location:
                    response.raise_for_status()
                    return _result_data(self.name, response.json(), command)
            except requests.RequestException as error:
                raise SourceError(
                    self.name,
                    f"Tautulli request '{command}' failed: "
                    f"{scrub_request_error(error)}",
                ) from None

            target = urljoin(current, location)
            if not same_origin(endpoint, target):
                raise SourceError(
                    self.name, redirect_refusal(endpoint, target, self.display_name)
                )
            current = target

        raise SourceError(
            self.name,
            f"Tautulli redirected {endpoint} more than "
            f"{MAX_SAME_ORIGIN_REDIRECTS} times.",
        )
