"""RAWG enrichment provider for video games.

RAWG provides comprehensive video game metadata including genres,
tags, descriptions, and more.
"""

import logging
import re
from collections import Counter
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
# DLC suffixes: "Game + DLC Name (DLC)"
DLC_SUFFIX_PATTERN = re.compile(r"\s*\+\s*.+?\s*\(DLC\)\s*$", re.IGNORECASE)
# Trademark symbols
TRADEMARK_PATTERN = re.compile(r"[™®©]")


def _longest_common_prefix(titles: list[str]) -> str:
    """Compute the longest common prefix of a list of titles, trimmed to word boundary.

    Used to derive a franchise name from a list of related game titles.
    For example: ["Dragon Age: Origins", "Dragon Age II", "Dragon Age: Inquisition"]
    -> "Dragon Age".

    Before computing the prefix, outlier titles are filtered out using
    majority-based first-word voting.  This handles cases like the FF XIII
    series where RAWG returns ``["Final Fantasy XIII", "Final Fantasy XIII-2",
    "Lightning Returns: Final Fantasy XIII"]`` — "Lightning" is the minority
    first word (1 of 3) so that title is excluded, and the prefix is computed
    from the two "Final Fantasy" titles.

    Args:
        titles: List of game titles.

    Returns:
        Common prefix trimmed to word boundary, or empty string if no
        meaningful prefix (< 3 characters) exists.
    """
    if not titles:
        return ""
    if len(titles) == 1:
        return titles[0]

    # Filter outlier titles by majority first-word vote
    filtered_titles = _filter_outlier_titles(titles)

    # Character-level common prefix
    prefix = filtered_titles[0]
    for title in filtered_titles[1:]:
        min_length = min(len(prefix), len(title))
        end = 0
        for index in range(min_length):
            if prefix[index].lower() != title[index].lower():
                break
            end = index + 1
        prefix = prefix[:end]

    # Trim to word boundary only if prefix ends mid-word in any title
    needs_trim = False
    for title in filtered_titles:
        if len(title) > len(prefix) and title[len(prefix)].isalnum():
            needs_trim = True
            break

    if needs_trim:
        last_space = prefix.rfind(" ")
        if last_space > 0:
            prefix = prefix[:last_space]

    # Strip trailing delimiters and whitespace
    prefix = prefix.rstrip(":- \t")

    return prefix if len(prefix) >= 3 else ""


def _filter_outlier_titles(titles: list[str]) -> list[str]:
    """Filter out outlier titles using majority-based first-word voting.

    Counts the first word (lowercased) of each title and keeps only titles
    whose first word matches the most common first word.  If fewer than 2
    titles remain after filtering, the original list is returned unchanged.

    Args:
        titles: List of game titles (must have at least 2 elements).

    Returns:
        Filtered list with outlier titles removed, or the original list
        if filtering would leave fewer than 2 titles.
    """
    first_word_counts: Counter[str] = Counter()
    for title in titles:
        first_word = title.split()[0].lower() if title.strip() else ""
        first_word_counts[first_word] += 1

    majority_word = first_word_counts.most_common(1)[0][0]
    filtered = [
        title
        for title in titles
        if (title.split()[0].lower() if title.strip() else "") == majority_word
    ]

    return filtered if len(filtered) >= 2 else titles


def _release_sort_key(entry: dict[str, Any]) -> str:
    """Sort key for game series entries by release date.

    Games without a release date sort to the end.
    """
    return entry.get("released") or "9999-12-31"


def clean_title_for_search(title: str) -> str:
    """Remove DLC suffixes, edition info, and trademark symbols for better search matching.

    Examples:
        "The Witcher 3: Wild Hunt - GOTY Edition" -> "The Witcher 3: Wild Hunt"
        "KINGDOM HEARTS III + Re Mind (DLC)" -> "KINGDOM HEARTS III"
        "Cyberpunk 2077™" -> "Cyberpunk 2077"
        "Dark Souls (Remastered)" -> "Dark Souls"

    Args:
        title: Original game title

    Returns:
        Cleaned title without DLC suffixes, edition suffixes, or trademark symbols
    """
    cleaned = title
    # Remove trademark symbols
    cleaned = TRADEMARK_PATTERN.sub("", cleaned).strip()
    # Remove DLC suffix (must run before edition patterns to avoid partial matches)
    cleaned = DLC_SUFFIX_PATTERN.sub("", cleaned).strip()
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
            logger.warning("RAWG provider does not support %s", content_type)
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
                "Cleaned title for search: '%s' -> '%s'", item.title, search_title
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

            # Extract franchise/series info (mirrors TMDB collection extraction)
            franchise_name, franchise_position = self._fetch_game_series(
                game_id=game_id,
                game_name=game.get("name", ""),
                game_released=game.get("released"),
                api_key=api_key,
            )
            if franchise_name:
                extra_metadata["franchise"] = franchise_name
            if franchise_position is not None:
                extra_metadata["series_position"] = franchise_position

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

    def _fetch_game_series(
        self,
        game_id: int,
        game_name: str,
        game_released: str | None,
        api_key: str,
    ) -> tuple[str | None, int | None]:
        """Fetch franchise info from the RAWG game-series endpoint.

        Calls ``GET /games/{game_id}/game-series`` to find related games in
        the same franchise.  Computes the franchise name from the longest
        common prefix of all game titles and the current game's 1-based
        position by release date ordering.

        Args:
            game_id: RAWG game ID.
            game_name: Name of the current game.
            game_released: Release date of the current game (YYYY-MM-DD).
            api_key: RAWG API key.

        Returns:
            Tuple of (franchise_name, position) or (None, None) on failure
            or when the game has no related entries.
        """
        try:
            response = requests.get(
                f"{RAWG_API_BASE}/games/{game_id}/game-series",
                params={"key": api_key, "page_size": "40"},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            series_results: list[dict[str, Any]] = data.get("results", [])
            if not series_results:
                return (None, None)

            # The current game may not be included in its own series results;
            # insert it so the prefix and position calculations are correct.
            if not any(entry.get("id") == game_id for entry in series_results):
                series_results.append(
                    {"id": game_id, "name": game_name, "released": game_released}
                )

            # Derive franchise name from common prefix of all titles
            all_titles = [
                entry["name"] for entry in series_results if entry.get("name")
            ]
            franchise_name = _longest_common_prefix(all_titles)
            if not franchise_name:
                return (None, None)

            # Sort by release date to determine position
            sorted_entries = sorted(series_results, key=_release_sort_key)

            # Find current game's 1-based position
            position: int | None = None
            for index, entry in enumerate(sorted_entries):
                if entry.get("id") == game_id:
                    position = index + 1
                    break

            return (franchise_name, position)

        except requests.RequestException:
            # Franchise info is optional — don't fail enrichment
            logger.warning("Failed to fetch game-series for game %s", game_id)
            return (None, None)

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
