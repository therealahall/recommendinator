"""Steam Web API integration plugin for fetching user game library."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Steam Web API base URL
STEAM_API_BASE = "https://api.steampowered.com"


class SteamAPIError(Exception):
    """Exception raised for Steam API errors."""

    pass


def _scrub_request_error(error: requests.RequestException) -> str:
    """Render a requests exception without leaking the URL/query string.

    The default ``str()`` of ``requests.HTTPError`` includes the request URL,
    which embeds the Steam Web API key (``?key=<api_key>&...``). The wrapped
    exception flows into ``SourceError`` and from there into the sync job's
    ``error_message`` field that the web API returns to the browser. Strip the
    URL and surface only the HTTP status (when known) plus the exception type.
    """
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"HTTP {error.response.status_code}"
    return type(error).__name__


def get_steam_id_from_vanity_url(api_key: str, vanity_url: str) -> str | None:
    """Resolve Steam vanity URL to Steam ID.

    Args:
        api_key: Steam Web API key
        vanity_url: Steam vanity URL (e.g., username or custom URL)

    Returns:
        Steam ID (64-bit) or None if not found
    """
    url = f"{STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v0001/"
    params = {"key": api_key, "vanityurl": vanity_url}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        result = data.get("response", {})
        if result.get("success") == 1:
            steamid = result.get("steamid")
            return str(steamid) if steamid else None
        return None
    except requests.RequestException as error:
        scrubbed = _scrub_request_error(error)
        logger.error("Error resolving Steam vanity URL: %s", scrubbed)
        raise SteamAPIError(f"Failed to resolve Steam ID: {scrubbed}") from error


def get_owned_games(
    api_key: str, steam_id: str, include_appinfo: bool = True
) -> list[dict[str, Any]]:
    """Fetch user's owned games from Steam API.

    Args:
        api_key: Steam Web API key
        steam_id: Steam ID (64-bit)
        include_appinfo: Whether to include app details (name, etc.)

    Returns:
        List of game dictionaries with appid, name, playtime_forever, etc.
    """
    url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v0001/"
    params = {
        "key": api_key,
        "steamid": steam_id,
        "include_appinfo": "1" if include_appinfo else "0",
        "include_played_free_games": "1",
        "format": "json",
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        games = data.get("response", {}).get("games", [])
        return list(games) if games else []
    except requests.RequestException as error:
        scrubbed = _scrub_request_error(error)
        logger.error("Error fetching Steam games: %s", scrubbed)
        raise SteamAPIError(f"Failed to fetch Steam games: {scrubbed}") from error


class SteamPlugin(SourcePlugin):
    """Plugin for importing video games from a Steam library.

    Uses the Steam Web API to fetch owned games, playtime, and metadata.
    Requires a Steam API key and either a Steam ID or vanity URL.
    """

    @property
    def name(self) -> str:
        return "steam"

    @property
    def display_name(self) -> str:
        return "Steam"

    @property
    def description(self) -> str:
        return "Import games from Steam library"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Normalise Steam YAML config (strip whitespace, coerce empty to None)."""
        return {
            "api_key": (raw_config.get("api_key") or "").strip(),
            "steam_id": (raw_config.get("steam_id") or "").strip() or None,
            "vanity_url": (raw_config.get("vanity_url") or "").strip() or None,
            "min_playtime_minutes": raw_config.get("min_playtime_minutes", 0),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="Steam Web API key from https://steamcommunity.com/dev/apikey",
            ),
            ConfigField(
                name="steam_id",
                field_type=str,
                required=False,
                description="Steam ID (64-bit). Required if vanity_url not provided.",
            ),
            ConfigField(
                name="vanity_url",
                field_type=str,
                required=False,
                description="Steam vanity URL. Used to resolve steam_id if not provided.",
            ),
            ConfigField(
                name="min_playtime_minutes",
                field_type=int,
                required=False,
                default=0,
                description="Minimum playtime in minutes to include a game.",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors = []
        if not (config.get("api_key") or "").strip():
            errors.append(
                "'api_key' is required. "
                "Get one from https://steamcommunity.com/dev/apikey"
            )
        if (
            not (config.get("steam_id") or "").strip()
            and not (config.get("vanity_url") or "").strip()
        ):
            errors.append("Either 'steam_id' or 'vanity_url' must be provided")
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch games from a Steam library.

        Args:
            config: Must contain 'api_key' and either 'steam_id' or 'vanity_url'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each game in the library

        Raises:
            SourceError: If the Steam API returns an error
        """
        config = self.__class__.transform_config(config)
        api_key = config["api_key"]
        steam_id = config.get("steam_id")
        vanity_url = config.get("vanity_url")
        min_playtime_minutes = config.get("min_playtime_minutes", 0)

        # Adapter: Steam internal (current, total, phase) -> plugin (items, total, item)
        def steam_internal_callback(current: int, total: int, phase: str) -> None:
            if progress_callback:
                phase_msg = "Fetching library..." if phase == "owned_games" else phase
                progress_callback(current, total, phase_msg)

        try:
            yield from _fetch_steam_games(
                api_key=api_key,
                steam_id=steam_id,
                vanity_url=vanity_url,
                min_playtime_minutes=min_playtime_minutes,
                source=self.get_source_identifier(config),
                progress_callback=steam_internal_callback,
            )
        except SteamAPIError as error:
            raise SourceError(self.name, str(error)) from error
        except ValueError as error:
            raise SourceError(self.name, str(error)) from error


def _fetch_steam_games(
    api_key: str,
    steam_id: str | None = None,
    vanity_url: str | None = None,
    min_playtime_minutes: int = 0,
    source: str = "steam",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Iterator[ContentItem]:
    """Fetch and parse Steam game library.

    Only the GetOwnedGames endpoint is called. Richer per-game metadata
    (release date, developers, publishers, genres, Metacritic score, etc.)
    is filled in asynchronously by the RAWG enrichment provider so initial
    sync stays fast even for large libraries.

    Args:
        api_key: Steam Web API key
        steam_id: Steam ID (64-bit)
        vanity_url: Steam vanity URL
        min_playtime_minutes: Minimum playtime filter
        source: Source identifier for ContentItems
        progress_callback: Optional callback(fetched_count, total, phase) for
            progress reporting. Phase is "owned_games" or a per-item title.

    Yields:
        ContentItem objects for each game
    """
    # Resolve Steam ID if needed
    if not steam_id:
        if not vanity_url:
            raise ValueError("Either steam_id or vanity_url must be provided")
        steam_id = get_steam_id_from_vanity_url(api_key, vanity_url)
        if not steam_id:
            raise SteamAPIError(
                f"Could not resolve Steam ID from vanity URL: {vanity_url}"
            )

    # Fetch owned games
    logger.info("Fetching owned games from Steam API for Steam ID: %s", steam_id)
    games = get_owned_games(api_key, steam_id, include_appinfo=True)
    logger.info("Found %d games in Steam library", len(games))
    if progress_callback:
        progress_callback(len(games), len(games), "owned_games")

    if not games:
        logger.warning("No games found in Steam library")
        return

    count = 0
    for game in games:
        app_id = game.get("appid")
        if not app_id:
            continue

        playtime_minutes = game.get("playtime_forever", 0)
        if playtime_minutes < min_playtime_minutes:
            continue

        game_name = game.get("name", "").strip()
        if not game_name:
            continue

        # Steam exposes no explicit "currently playing" or "completed" signal,
        # so we never infer status from playtime. Users mark progress in the UI.
        status = ConsumptionStatus.UNREAD
        playtime_hours = playtime_minutes / 60.0

        metadata: dict[str, Any] = {
            "steam_app_id": str(app_id),
            "playtime_minutes": playtime_minutes,
            "playtime_hours": round(playtime_hours, 1),
            "playtime_2weeks": game.get("playtime_2weeks", 0),
            "playtime_windows_forever": game.get("playtime_windows_forever", 0),
            "playtime_mac_forever": game.get("playtime_mac_forever", 0),
            "playtime_linux_forever": game.get("playtime_linux_forever", 0),
        }

        count += 1

        if progress_callback:
            progress_callback(count, len(games), game_name)

        yield ContentItem(
            id=str(app_id),
            title=game_name,
            author=None,
            content_type=ContentType.VIDEO_GAME,
            rating=None,
            review=None,
            status=status,
            date_completed=None,
            metadata=metadata,
            source=source,
        )
