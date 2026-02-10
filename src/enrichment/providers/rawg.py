"""RAWG enrichment provider for video games.

RAWG provides comprehensive video game metadata including genres,
tags, descriptions, and more.
"""

import logging
import re
from typing import Any

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
)
from src.models.content import ContentItem, ContentType

logger = logging.getLogger(__name__)

# RAWG API base URL
RAWG_API_BASE = "https://api.rawg.io/api"

# Patterns to clean from titles before searching
# Edition suffixes: "Game - Deluxe Edition", "Game: GOTY Edition"
EDITION_PATTERN = re.compile(
    r"\s*[-:]\s*("
    r"Deluxe Edition|"
    r"GOTY Edition|"
    r"Game of the Year Edition|"
    r"Definitive Edition|"
    r"Complete Edition|"
    r"Enhanced Edition|"
    r"Ultimate Edition|"
    r"Special Edition|"
    r"Collector's Edition|"
    r"Anniversary Edition|"
    r"Remastered|"
    r"Remake"
    r")\s*$",
    re.IGNORECASE,
)
# Edition in parentheses: "(Deluxe Edition)", "(GOTY)", "(Legendary)"
EDITION_PAREN_PATTERN = re.compile(
    r"\s*\(("
    r"Deluxe|"
    r"GOTY|"
    r"Game of the Year|"
    r"Definitive|"
    r"Complete|"
    r"Enhanced|"
    r"Ultimate|"
    r"Special|"
    r"Collector's|"
    r"Anniversary|"
    r"Legendary|"
    r"Remastered|"
    r"Remake"
    r")(?:\s+Edition)?\)\s*$",
    re.IGNORECASE,
)
# Trademark symbols
TRADEMARK_PATTERN = re.compile(r"[™®©]")


def clean_title_for_search(title: str) -> str:
    """Remove edition info and trademark symbols from title for better search matching.

    Examples:
        "The Witcher 3: Wild Hunt - GOTY Edition" -> "The Witcher 3: Wild Hunt"
        "Cyberpunk 2077™" -> "Cyberpunk 2077"
        "Dark Souls (Remastered)" -> "Dark Souls"

    Args:
        title: Original game title

    Returns:
        Cleaned title without edition suffixes or trademark symbols
    """
    cleaned = title
    # Remove trademark symbols
    cleaned = TRADEMARK_PATTERN.sub("", cleaned).strip()
    # Remove edition suffix (dash/colon format)
    cleaned = EDITION_PATTERN.sub("", cleaned).strip()
    # Remove edition in parentheses
    cleaned = EDITION_PAREN_PATTERN.sub("", cleaned).strip()
    return cleaned if cleaned else title


class RAWGProvider(EnrichmentProvider):
    """Enrichment provider using RAWG API.

    Enriches video games with:
    - Genres
    - Tags
    - Description
    - Additional metadata (developers, publishers, platforms, etc.)

    Requires a free API key from https://rawg.io/apidocs.

    Matching strategy:
    1. Search by title
    2. Match against year if available
    """

    @property
    def name(self) -> str:
        return "rawg"

    @property
    def display_name(self) -> str:
        return "RAWG"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        # RAWG free tier: 5 requests per second
        return 5.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="RAWG API key (get from https://rawg.io/apidocs)",
                sensitive=True,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required for RAWG provider")
        return errors

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        """Enrich a video game with RAWG metadata.

        Args:
            item: ContentItem to enrich (must be VIDEO_GAME)
            config: Provider configuration with api_key

        Returns:
            EnrichmentResult with metadata, or None if not found

        Raises:
            ProviderError: If API request fails
        """
        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )

        if content_type != ContentType.VIDEO_GAME:
            logger.warning(f"RAWG provider does not support {content_type}")
            return None

        api_key = config.get("api_key", "")

        # Search for the game
        game_id = self._search_game(item, api_key)

        if game_id is None:
            return EnrichmentResult(
                match_quality="not_found",
                provider=self.name,
            )

        # Fetch detailed game info
        return self._fetch_game_details(game_id, api_key)

    def _search_game(self, item: ContentItem, api_key: str) -> int | None:
        """Search for a game by title.

        Args:
            item: ContentItem with game title
            api_key: RAWG API key

        Returns:
            RAWG game ID if found, None otherwise
        """
        # Clean title to remove edition suffixes and trademark symbols
        search_title = clean_title_for_search(item.title)
        if search_title != item.title:
            logger.debug(
                f"Cleaned title for search: '{item.title}' -> '{search_title}'"
            )

        params: dict[str, str | int] = {
            "key": api_key,
            "search": search_title,
            "page_size": 5,
        }

        try:
            response = requests.get(
                f"{RAWG_API_BASE}/games",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if not results:
                return None

            # Try to find best match
            metadata = item.metadata or {}
            release_year = metadata.get("release_year")

            for result in results:
                # Exact title match (case-insensitive) - compare against cleaned title
                if result.get("name", "").lower() == search_title.lower():
                    # If we have a year, verify it matches
                    if release_year and result.get("released"):
                        result_year = self._extract_year(result["released"])
                        if result_year and result_year == release_year:
                            return int(result["id"])
                    else:
                        return int(result["id"])

            # No exact match, return first result
            return int(results[0]["id"])

        except requests.RequestException as error:
            raise ProviderError(self.name, f"Failed to search RAWG: {error}") from error

    def _fetch_game_details(self, game_id: int, api_key: str) -> EnrichmentResult:
        """Fetch detailed game information.

        Args:
            game_id: RAWG game ID
            api_key: RAWG API key

        Returns:
            EnrichmentResult with game metadata
        """
        try:
            response = requests.get(
                f"{RAWG_API_BASE}/games/{game_id}",
                params={"key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            game = response.json()

            # Extract genres
            genres = [genre["name"] for genre in game.get("genres", [])]

            # Extract tags (limit to most relevant)
            tags = [tag["name"] for tag in game.get("tags", [])[:20]]

            # Get description (strip HTML)
            description = self._clean_description(game.get("description"))

            # Build extra metadata
            extra_metadata: dict[str, Any] = {}

            if game.get("released"):
                extra_metadata["release_date"] = game["released"]
                year = self._extract_year(game["released"])
                if year:
                    extra_metadata["release_year"] = year

            if game.get("developers"):
                developers = [dev["name"] for dev in game["developers"][:2]]
                if developers:
                    extra_metadata["developer"] = developers[0]

            if game.get("publishers"):
                publishers = [pub["name"] for pub in game["publishers"][:2]]
                if publishers:
                    extra_metadata["publisher"] = publishers[0]

            if game.get("platforms"):
                platforms = [
                    plat["platform"]["name"]
                    for plat in game["platforms"]
                    if plat.get("platform")
                ]
                if platforms:
                    extra_metadata["platforms"] = platforms

            if game.get("rating"):
                extra_metadata["rawg_rating"] = game["rating"]

            if game.get("metacritic"):
                extra_metadata["metacritic"] = game["metacritic"]

            if game.get("playtime"):
                extra_metadata["playtime_hours"] = game["playtime"]

            # ESRB rating
            if game.get("esrb_rating"):
                extra_metadata["esrb_rating"] = game["esrb_rating"]["name"]

            return EnrichmentResult(
                external_id=f"rawg:{game_id}",
                genres=genres if genres else None,
                tags=tags if tags else None,
                description=description,
                extra_metadata=extra_metadata,
                match_quality="high",
                provider=self.name,
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to fetch game details: {error}"
            ) from error

    def _extract_year(self, date_str: str) -> int | None:
        """Extract year from date string (YYYY-MM-DD format).

        Args:
            date_str: Date string

        Returns:
            Year as integer, or None
        """
        try:
            return int(date_str[:4])
        except (ValueError, IndexError):
            return None

    def _clean_description(self, description: str | None) -> str | None:
        """Clean HTML from description.

        Args:
            description: Raw description with possible HTML

        Returns:
            Cleaned description text
        """
        if not description:
            return None

        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", description)

        # Clean up whitespace
        text = re.sub(r"\s+", " ", text).strip()

        # Limit length
        if len(text) > 2000:
            text = text[:1997] + "..."

        return text if text else None
