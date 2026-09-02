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
    log_search_title,
)
from src.models.content import ContentItem, ContentType
from src.utils.request_errors import scrub_request_error

logger = logging.getLogger(__name__)

RAWG_API_BASE = "https://api.rawg.io/api"

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
TRADEMARK_PATTERN = re.compile(r"[™®©]")


def _https_cover(background_image: Any) -> str | None:
    """The one cover URL a third party chooses freely, so it is narrowed here."""
    if isinstance(background_image, str) and background_image.startswith("https://"):
        return background_image
    return None


def _longest_common_prefix(titles: list[str]) -> str:
    if not titles:
        return ""
    if len(titles) == 1:
        return titles[0]

    filtered_titles = _filter_outlier_titles(titles)

    prefix = filtered_titles[0]
    for title in filtered_titles[1:]:
        min_length = min(len(prefix), len(title))
        end = 0
        for index in range(min_length):
            if prefix[index].lower() != title[index].lower():
                break
            end = index + 1
        prefix = prefix[:end]

    needs_trim = False
    for title in filtered_titles:
        if len(title) > len(prefix) and title[len(prefix)].isalnum():
            needs_trim = True
            break

    if needs_trim:
        last_space = prefix.rfind(" ")
        if last_space > 0:
            prefix = prefix[:last_space]

    prefix = prefix.rstrip(":- \t")

    return prefix if len(prefix) >= 3 else ""


def _filter_outlier_titles(titles: list[str]) -> list[str]:
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
    """Games without a release date sort to the end."""
    return entry.get("released") or "9999-12-31"


def clean_game_title_for_search(title: str) -> str:
    cleaned = title
    cleaned = TRADEMARK_PATTERN.sub("", cleaned).strip()
    # Remove DLC suffix (must run before edition patterns to avoid partial matches)
    cleaned = DLC_SUFFIX_PATTERN.sub("", cleaned).strip()
    cleaned = EDITION_PATTERN.sub("", cleaned).strip()
    cleaned = EDITION_PAREN_PATTERN.sub("", cleaned).strip()
    return cleaned if cleaned else title


class RAWGProvider(EnrichmentProvider):
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
        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )

        if content_type != ContentType.VIDEO_GAME:
            logger.warning("RAWG provider does not support %s", content_type)
            return None

        api_key = config.get("api_key", "")

        game_id = self._search_game(item, api_key)

        if game_id is None:
            return EnrichmentResult(
                match_quality="not_found",
                provider=self.name,
            )

        return self._fetch_game_details(game_id, api_key)

    def _search_game(self, item: ContentItem, api_key: str) -> int | None:
        search_title = clean_game_title_for_search(item.title)
        log_search_title(logger, item.title, search_title)

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

            metadata = item.metadata or {}
            release_year = metadata.get("release_year")

            for result in results:
                if result.get("name", "").lower() == search_title.lower():
                    if release_year and result.get("released"):
                        result_year = self._extract_year(result["released"])
                        if result_year and result_year == release_year:
                            return int(result["id"])
                    else:
                        return int(result["id"])

            return int(results[0]["id"])

        except requests.RequestException as error:
            # ``from None``: the key is a query parameter, so the URL on
            # ``__cause__`` is a credential a caller's traceback would print.
            raise ProviderError(
                self.name, f"Failed to search RAWG: {scrub_request_error(error)}"
            ) from None

    def _fetch_game_details(self, game_id: int, api_key: str) -> EnrichmentResult:
        try:
            response = requests.get(
                f"{RAWG_API_BASE}/games/{game_id}",
                params={"key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            game = response.json()

            genres = [genre["name"] for genre in game.get("genres", [])]

            tags = [tag["name"] for tag in game.get("tags", [])[:20]]

            description = self._clean_description(game.get("description"))

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
                extra_metadata["average_playtime_hours"] = game["playtime"]

            if game.get("esrb_rating"):
                extra_metadata["esrb_rating"] = game["esrb_rating"]["name"]

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
                cover_url=_https_cover(game.get("background_image")),
                extra_metadata=extra_metadata,
                match_quality="high",
                provider=self.name,
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name,
                f"Failed to fetch game details: {scrub_request_error(error)}",
            ) from None

    def _fetch_game_series(
        self,
        game_id: int,
        game_name: str,
        game_released: str | None,
        api_key: str,
    ) -> tuple[str | None, int | None]:
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

            all_titles = [
                entry["name"] for entry in series_results if entry.get("name")
            ]
            franchise_name = _longest_common_prefix(all_titles)
            if not franchise_name:
                return (None, None)

            sorted_entries = sorted(series_results, key=_release_sort_key)

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
        try:
            return int(date_str[:4])
        except (ValueError, IndexError):
            return None

    def _clean_description(self, description: str | None) -> str | None:
        if not description:
            return None

        text = re.sub(r"<[^>]+>", "", description)

        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > 2000:
            text = text[:1997] + "..."

        return text if text else None
