"""Steam Web API integration plugin for fetching user game library."""

import logging
from collections.abc import Iterator
from typing import Any

import requests

from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType

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
        logger.error(f"Error resolving Steam vanity URL: {error}")
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
        logger.error(f"Error fetching Steam games: {error}")
        raise SteamAPIError(f"Failed to fetch Steam games: {error}") from error


def get_game_details(
    app_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Fetch detailed information for multiple games.

    Args:
        app_ids: List of Steam app IDs

    Returns:
        Dictionary mapping app_id to game details
    """
    # Steam Store API for game details
    # Note: This is a public API, no key required
    # Steam Store API has a limit on batch size (typically 1-5 app IDs)
    # Using single requests to avoid 400 Bad Request errors
    details: dict[int, dict[str, Any]] = {}
    batch_size = 1  # Steam Store API works best with single requests
    total = len(app_ids)

    for index in range(0, len(app_ids), batch_size):
        batch = app_ids[index : index + batch_size]
        app_ids_str = ",".join(str(app_id) for app_id in batch)
        url = "https://store.steampowered.com/api/appdetails"
        params = {"appids": app_ids_str, "l": "en"}

        # Log progress every 10 games or for the first few
        current = index + 1
        if current <= 5 or current % 10 == 0 or current == total:
            logger.info(
                f"Fetching game details: {current}/{total} "
                f"({current * 100 // total}%)"
            )

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            for app_id_str, app_data in data.items():
                if app_data.get("success"):
                    details[int(app_id_str)] = app_data.get("data", {})
        except requests.RequestException as error:
            logger.warning(
                f"Error fetching game details for app IDs {batch}: {error}. "
                "Skipping this batch."
            )
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
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

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
        if not config.get("api_key", "").strip():
            errors.append(
                "'api_key' is required. "
                "Get one from https://steamcommunity.com/dev/apikey"
            )
        if (
            not config.get("steam_id", "").strip()
            and not config.get("vanity_url", "").strip()
        ):
            errors.append("Either 'steam_id' or 'vanity_url' must be provided")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        """Fetch games from a Steam library.

        Args:
            config: Must contain 'api_key' and either 'steam_id' or 'vanity_url'

        Yields:
            ContentItem for each game in the library

        Raises:
            SourceError: If the Steam API returns an error
        """
        api_key = config.get("api_key", "").strip()
        steam_id = config.get("steam_id", "").strip() or None
        vanity_url = config.get("vanity_url", "").strip() or None
        min_playtime_minutes = config.get("min_playtime_minutes", 0)

        try:
            yield from _fetch_steam_games(
                api_key=api_key,
                steam_id=steam_id,
                vanity_url=vanity_url,
                min_playtime_minutes=min_playtime_minutes,
                source=self.get_source_identifier(),
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
) -> Iterator[ContentItem]:
    """Fetch and parse Steam game library.

    Args:
        api_key: Steam Web API key
        steam_id: Steam ID (64-bit)
        vanity_url: Steam vanity URL
        min_playtime_minutes: Minimum playtime filter
        source: Source identifier for ContentItems

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
    try:
        logger.info(f"Fetching owned games from Steam API for Steam ID: {steam_id}")
        games = get_owned_games(api_key, steam_id, include_appinfo=True)
        logger.info(f"Found {len(games)} games in Steam library")
    except SteamAPIError:
        raise

    if not games:
        logger.warning("No games found in Steam library")
        return

    # Fetch additional game details for better metadata
    app_ids = [game["appid"] for game in games]
    logger.info(
        f"Fetching detailed information for {len(app_ids)} games "
        "(this may take a while due to API rate limits)..."
    )
    game_details = get_game_details(app_ids)
    logger.info(f"Successfully fetched details for {len(game_details)} games")

    # Process each game
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
        if playtime_minutes == 0:
            status = ConsumptionStatus.UNREAD
        elif playtime_minutes < 60:  # Less than 1 hour
            status = ConsumptionStatus.CURRENTLY_CONSUMING
        else:  # 1+ hours
            status = ConsumptionStatus.COMPLETED

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

        # Estimate rating based on playtime (rough heuristic)
        rating = None
        if playtime_hours >= 20:
            rating = 5  # Highly engaged
        elif playtime_hours >= 10:
            rating = 4
        elif playtime_hours >= 5:
            rating = 3
        elif playtime_hours >= 1:
            rating = 2
        # Don't assign rating for games with < 1 hour

        yield ContentItem(
            id=str(app_id),
            title=game_name,
            author=None,  # Games don't have authors
            content_type=ContentType.VIDEO_GAME,
            rating=rating,
            review=None,  # Steam API doesn't provide user reviews
            status=status,
            date_completed=None,  # Steam doesn't track completion dates
            metadata=metadata,
            source=source,
        )


def parse_steam_games(
    api_key: str,
    steam_id: str | None = None,
    vanity_url: str | None = None,
    min_playtime_minutes: int = 0,
) -> Iterator[ContentItem]:
    """Parse Steam game library and yield ContentItem objects.

    Convenience function that wraps the SteamPlugin for backward
    compatibility with existing CLI and web API code.

    Args:
        api_key: Steam Web API key
        steam_id: Steam ID (64-bit)
        vanity_url: Steam vanity URL
        min_playtime_minutes: Minimum playtime in minutes to include a game

    Yields:
        ContentItem objects for each game in the library
    """
    yield from _fetch_steam_games(
        api_key=api_key,
        steam_id=steam_id,
        vanity_url=vanity_url,
        min_playtime_minutes=min_playtime_minutes,
    )
