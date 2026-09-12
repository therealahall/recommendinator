import logging
import re
from typing import Any

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    log_search_title,
    pinned_record,
)
from src.models.content import ContentItem, ContentType, get_enum_value
from src.utils.matching import Candidate, best_match, year_of
from src.utils.request_errors import scrub_request_error
from src.utils.text import clean_game_title_for_search

logger = logging.getLogger(__name__)

RAWG_API_BASE = "https://api.rawg.io/api"


def _https_cover(background_image: Any) -> str | None:
    """The one cover URL a third party chooses freely, so it is narrowed here."""
    if isinstance(background_image, str) and background_image.startswith("https://"):
        return background_image
    return None


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

        game_id = self._matched_id(item, api_key)

        if game_id is None:
            return EnrichmentResult(match_quality="not_found")

        return self._fetch_game_details(game_id, api_key)

    def search(self, item: ContentItem, config: dict[str, Any]) -> list[Candidate]:
        if get_enum_value(item.content_type) != ContentType.VIDEO_GAME.value:
            return []
        return self._search_game(item, config.get("api_key", ""))

    def accepts_record_id(self, record_id: str) -> bool:
        return record_id.isdigit()

    def _matched_id(self, item: ContentItem, api_key: str) -> int | None:
        pinned = pinned_record(item, self.name)
        if pinned is not None:
            try:
                return int(pinned)
            except ValueError:
                pass

        metadata = item.metadata or {}
        matched = best_match(
            clean_game_title_for_search(item.title),
            year_of(metadata.get("release_year")),
            self._search_game(item, api_key),
        )
        return None if matched is None else int(matched.record_id)

    def _search_game(self, item: ContentItem, api_key: str) -> list[Candidate]:
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

            return [
                Candidate(
                    record_id=str(result["id"]),
                    title=str(result.get("name") or ""),
                    year=year_of(result.get("released")),
                    cover_url=result.get("background_image"),
                )
                for result in response.json().get("results", [])
            ]

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
                year = year_of(game["released"])
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

            return EnrichmentResult(
                genres=genres if genres else None,
                tags=tags if tags else None,
                description=description,
                cover_url=_https_cover(game.get("background_image")),
                extra_metadata=extra_metadata,
                match_quality="high",
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name,
                f"Failed to fetch game details: {scrub_request_error(error)}",
            ) from None

    def _clean_description(self, description: str | None) -> str | None:
        if not description:
            return None

        text = re.sub(r"<[^>]+>", "", description)

        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > 2000:
            text = text[:1997] + "..."

        return text if text else None
