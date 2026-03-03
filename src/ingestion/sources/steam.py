"""Steam Web API integration plugin for fetching user game library."""

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.progress import log_progress

logger = logging.getLogger(__name__)

# Steam Web API base URL
STEAM_API_BASE = "https://api.steampowered.com"


class SteamAPIError(Exception):
    """Exception raised for Steam API errors."""

    pass


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
        logger.error("Error resolving Steam vanity URL: %s", error)
        raise SteamAPIError(f"Failed to resolve Steam ID: {error}") from error


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
        logger.error("Error fetching Steam games: %s", error)
        raise SteamAPIError(f"Failed to fetch Steam games: {error}") from error


def get_game_details(
    app_ids: list[int],
    rate_limit_seconds: float = 3.0,
    max_retries: int = 3,
    backoff_multiplier: float = 2.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[int, dict[str, Any]]:
    """Fetch detailed information for multiple games with rate limiting and backoff.

    Args:
        app_ids: List of Steam app IDs
        rate_limit_seconds: Base delay between requests to avoid rate limiting.
            Steam's store API is rate-limited; 3 seconds is a safe default.
        max_retries: Maximum number of retries per request on failure.
        backoff_multiplier: Multiplier for exponential backoff on retries.
        progress_callback: Optional callback(current, total) called after each
            successful fetch for progress reporting.

    Returns:
        Dictionary mapping app_id to game details
    """
    # Steam Store API for game details
    # Note: This is a public API, no key required
    # Steam Store API has a limit on batch size (typically 1-5 app IDs)
    # Using single requests to avoid 400 Bad Request errors
    details: dict[int, dict[str, Any]] = {}
    total = len(app_ids)
    current_delay = rate_limit_seconds

    for index, app_id in enumerate(app_ids):
        app_ids_str = str(app_id)
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": app_ids_str, "l": "en"}

        current = index + 1
        log_progress(logger, "game details", current, total)

        # Retry loop with exponential backoff
        retry_delay = current_delay
        for attempt in range(max_retries + 1):
            try:
                response = requests.get(url, params=params, timeout=30)

                # Check for rate limiting (429) or server errors (5xx)
                if response.status_code == 429:
                    logger.warning(
                        "Rate limited by Steam API. Backing off for %.1fs...",
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    retry_delay *= backoff_multiplier
                    # Increase base delay for future requests
                    current_delay = min(current_delay * 1.5, 30.0)
                    continue

                response.raise_for_status()
                data = response.json()
                for app_id_str, app_data in data.items():
                    if app_data.get("success"):
                        details[int(app_id_str)] = app_data.get("data", {})
                if progress_callback:
                    progress_callback(current, total)
                # Gradually reduce delay back toward base if successful
                current_delay = max(rate_limit_seconds, current_delay * 0.9)
                break

            except requests.RequestException as error:
                if attempt < max_retries:
                    logger.warning(
                        "Error fetching game details for app ID %s: %s. "
                        "Retrying in %.1fs (attempt %d/%d)...",
                        app_id,
                        error,
                        retry_delay,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(retry_delay)
                    retry_delay *= backoff_multiplier
                else:
                    logger.warning(
                        "Error fetching game details for app ID %s: %s. "
                        "Max retries exceeded, skipping.",
                        app_id,
                        error,
                    )

        # Rate limit: wait between requests to avoid being blocked
        # Skip delay after the last request
        if index + 1 < len(app_ids) and current_delay > 0:
            time.sleep(current_delay)

    return details


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
            "api_key": raw_config.get("api_key", "").strip(),
            "steam_id": raw_config.get("steam_id", "").strip() or None,
            "vanity_url": raw_config.get("vanity_url", "").strip() or None,
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

    def validate_config(self, config: dict[str, Any]) -> list[str]:
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
        api_key = config.get("api_key", "").strip()
        steam_id = config.get("steam_id", "").strip() or None
        vanity_url = config.get("vanity_url", "").strip() or None
        min_playtime_minutes = config.get("min_playtime_minutes", 0)

        # Adapter: Steam internal (current, total, phase) -> plugin (items, total, item)
        def steam_internal_callback(current: int, total: int, phase: str) -> None:
            if progress_callback:
                phase_msg = (
                    "Fetching game details..."
                    if phase == "game_details"
                    else "Fetching library..." if phase == "owned_games" else phase
                )
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

    Args:
        api_key: Steam Web API key
        steam_id: Steam ID (64-bit)
        vanity_url: Steam vanity URL
        min_playtime_minutes: Minimum playtime filter
        source: Source identifier for ContentItems
        progress_callback: Optional callback(fetched_count, total, phase) for
            progress reporting. Phase is "owned_games", "game_details", or
            "processing".

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

    # Fetch additional game details for better metadata
    app_ids = [game["appid"] for game in games]
    total_games = len(app_ids)
    logger.info(
        "Fetching detailed information for %d games "
        "(this may take a while due to API rate limits)...",
        total_games,
    )

    def game_details_progress(current: int, total: int) -> None:
        if progress_callback:
            progress_callback(current, total, "game_details")

    game_details = get_game_details(app_ids, progress_callback=game_details_progress)
    logger.info("Successfully fetched details for %d games", len(game_details))

    # Process each game
    count = 0
    for game in games:
        app_id = game.get("appid")
        if not app_id:
            continue

        # Get playtime (in minutes)
        playtime_minutes = game.get("playtime_forever", 0)
        if playtime_minutes < min_playtime_minutes:
            continue

        # Get game name
        game_name = game.get("name", "").strip()
        if not game_name:
            # Try to get from details
            details = game_details.get(app_id, {})
            game_name = details.get("name", f"Steam App {app_id}")
            if not game_name or game_name == f"Steam App {app_id}":
                continue  # Skip games without names

        # Determine status based on playtime
        # Note: Steam doesn't provide completion data, and playtime is unreliable
        # for determining completion (a 5-hour indie vs 100-hour RPG). We only
        # distinguish between "never played" and "has been played". Users can
        # manually mark games as completed via the UI.
        if playtime_minutes == 0:
            status = ConsumptionStatus.UNREAD
        else:
            status = ConsumptionStatus.CURRENTLY_CONSUMING

        # Convert playtime to hours for metadata
        playtime_hours = playtime_minutes / 60.0

        # Get additional metadata from game details
        details = game_details.get(app_id, {})
        metadata = {
            "steam_app_id": str(app_id),
            "playtime_minutes": playtime_minutes,
            "playtime_hours": round(playtime_hours, 1),
            "playtime_2weeks": game.get("playtime_2weeks", 0),
            "playtime_windows_forever": game.get("playtime_windows_forever", 0),
            "playtime_mac_forever": game.get("playtime_mac_forever", 0),
            "playtime_linux_forever": game.get("playtime_linux_forever", 0),
        }

        # Add game details if available
        if details:
            metadata.update(
                {
                    "release_date": details.get("release_date", {}).get("date"),
                    "developers": details.get("developers", []),
                    "publishers": details.get("publishers", []),
                    "genres": [
                        genre.get("description") for genre in details.get("genres", [])
                    ],
                    "categories": [
                        category.get("description")
                        for category in details.get("categories", [])
                    ],
                    "short_description": details.get("short_description"),
                    "website": details.get("website"),
                    "metacritic_score": details.get("metacritic", {}).get("score"),
                }
            )

        count += 1

        if progress_callback:
            progress_callback(count, len(games), game_name)

        yield ContentItem(
            id=str(app_id),
            title=game_name,
            author=None,  # Games don't have authors
            content_type=ContentType.VIDEO_GAME,
            rating=None,
            review=None,  # Steam API doesn't provide user reviews
            status=status,
            date_completed=None,  # Steam doesn't track completion dates
            metadata=metadata,
            source=source,
        )
