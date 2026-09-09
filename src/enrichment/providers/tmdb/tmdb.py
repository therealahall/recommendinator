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

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"

_POSTER_BASE = "https://image.tmdb.org/t/p/w500"

YEAR_PATTERN = re.compile(r"\s*\(\d{4}\)\s*$")
COUNTRY_PATTERN = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")


def clean_media_title_for_search(title: str) -> str:
    cleaned = title
    cleaned = YEAR_PATTERN.sub("", cleaned).strip()
    cleaned = COUNTRY_PATTERN.sub("", cleaned).strip()
    return cleaned if cleaned else title


def _poster_url(payload: dict[str, Any]) -> str | None:
    poster_path = payload.get("poster_path")
    return f"{_POSTER_BASE}{poster_path}" if poster_path else None


def _searched(item: ContentItem) -> tuple[str, int | None]:
    metadata = item.metadata or {}
    return clean_media_title_for_search(item.title), year_of(
        metadata.get("release_year") or metadata.get("year_published")
    )


def _candidate(result: dict[str, Any]) -> Candidate:
    titles = [
        str(title)
        for title in (
            result.get("title"),
            result.get("name"),
            result.get("original_title"),
            result.get("original_name"),
        )
        if title
    ]
    return Candidate(
        record_id=str(result["id"]),
        title=titles[0] if titles else "",
        year=year_of(result.get("release_date") or result.get("first_air_date")),
        cover_url=_poster_url(result),
        also_titled=tuple(titles[1:]),
    )


# The provider's own defaults, consumed by BOTH get_config_schema() and enrich()
# so the two cannot drift.
_DEFAULT_LANGUAGE = "en-US"
_DEFAULT_INCLUDE_KEYWORDS = True

_SEARCH_ROUTES = {
    "movie": ("search/movie", "year"),
    "tv_show": ("search/tv", "first_air_date_year"),
}


class TMDBProvider(EnrichmentProvider):
    @property
    def name(self) -> str:
        return "tmdb"

    @property
    def display_name(self) -> str:
        return "TMDB"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE, ContentType.TV_SHOW]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        # TMDB allows 40 requests per second
        return 40.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="TMDB API key (get from https://www.themoviedb.org/settings/api)",
                sensitive=True,
            ),
            ConfigField(
                name="language",
                field_type=str,
                required=False,
                default=_DEFAULT_LANGUAGE,
                description="Language for results (e.g., 'en-US', 'de-DE')",
            ),
            ConfigField(
                name="include_keywords",
                field_type=bool,
                required=False,
                default=_DEFAULT_INCLUDE_KEYWORDS,
                description="Fetch keywords as tags (requires extra API call)",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required for TMDB provider")
        return errors

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        api_key = config.get("api_key", "")
        language = config.get("language", _DEFAULT_LANGUAGE)
        include_keywords = config.get("include_keywords", _DEFAULT_INCLUDE_KEYWORDS)

        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )

        if content_type == ContentType.MOVIE:
            return self._enrich_movie(item, api_key, language, include_keywords)
        elif content_type == ContentType.TV_SHOW:
            return self._enrich_tv_show(item, api_key, language, include_keywords)
        else:
            logger.warning("TMDB provider does not support %s", content_type)
            return None

    def _enrich_movie(
        self,
        item: ContentItem,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult | None:
        tmdb_id = self._get_tmdb_id(item, "movie")

        if tmdb_id is None:
            tmdb_id = self._matched_id(
                item, api_key, language, *_SEARCH_ROUTES["movie"]
            )

        if tmdb_id is None:
            return EnrichmentResult(match_quality="not_found")

        return self._fetch_movie_details(tmdb_id, api_key, language, include_keywords)

    def _enrich_tv_show(
        self,
        item: ContentItem,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult | None:
        tmdb_id = self._get_tmdb_id(item, "tv")

        if tmdb_id is None:
            tmdb_id = self._matched_id(
                item, api_key, language, *_SEARCH_ROUTES["tv_show"]
            )

        if tmdb_id is None:
            return EnrichmentResult(match_quality="not_found")

        return self._fetch_tv_details(tmdb_id, api_key, language, include_keywords)

    def search(self, item: ContentItem, config: dict[str, Any]) -> list[Candidate]:
        route = _SEARCH_ROUTES.get(get_enum_value(item.content_type))
        if route is None:
            return []
        # Unfiltered by the stored year: an operator reaches the picker having
        # decided the stored data is wrong, and TMDB filters the year exactly.
        search_title = clean_media_title_for_search(item.title)
        log_search_title(logger, item.title, search_title)
        return self._request_candidates(
            route[0],
            config.get("api_key", ""),
            {
                "query": search_title,
                "language": config.get("language", _DEFAULT_LANGUAGE),
            },
        )

    def accepts_record_id(self, record_id: str) -> bool:
        return record_id.isdigit()

    def _get_tmdb_id(self, item: ContentItem, media_type: str) -> int | None:
        metadata = item.metadata or {}

        pinned = pinned_record(item, self.name)
        if pinned is not None:
            try:
                return int(pinned)
            except ValueError:
                pass

        if "tmdb_id" in metadata:
            try:
                return int(metadata["tmdb_id"])
            except (ValueError, TypeError):
                pass

        if item.id and item.id.startswith("tmdb:"):
            try:
                return int(item.id.split(":")[1])
            except (ValueError, IndexError):
                pass

        return None

    def _search_media(
        self,
        item: ContentItem,
        api_key: str,
        language: str,
        endpoint: str,
        year_param: str,
    ) -> list[Candidate]:
        search_title, year = _searched(item)
        log_search_title(logger, item.title, search_title)

        params = {"query": search_title, "language": language}
        if year:
            params[year_param] = str(year)
            candidates = self._request_candidates(endpoint, api_key, params)
            if best_match(search_title, year, candidates) is not None:
                return candidates
            # TMDB filters on an exact year where the gate allows three of
            # drift, so a near-year release only surfaces on this retry.
            del params[year_param]

        return self._request_candidates(endpoint, api_key, params)

    def _request_candidates(
        self, endpoint: str, api_key: str, params: dict[str, str]
    ) -> list[Candidate]:
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/{endpoint}",
                params={"api_key": api_key, **params},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            # ``from None``: the api_key is a query parameter, so the URL on
            # ``__cause__`` is a credential a caller's traceback would print.
            raise ProviderError(
                self.name, f"Failed to search TMDB: {scrub_request_error(error)}"
            ) from None
        return [_candidate(result) for result in response.json().get("results", [])]

    def _matched_id(
        self,
        item: ContentItem,
        api_key: str,
        language: str,
        endpoint: str,
        year_param: str,
    ) -> int | None:
        search_title, year = _searched(item)
        matched = best_match(
            search_title,
            year,
            self._search_media(item, api_key, language, endpoint, year_param),
        )
        return None if matched is None else int(matched.record_id)

    def _fetch_movie_details(
        self,
        tmdb_id: int,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult:
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/movie/{tmdb_id}",
                params={
                    "api_key": api_key,
                    "language": language,
                    "append_to_response": "credits",
                },
                timeout=10,
            )
            response.raise_for_status()
            movie = response.json()

            genres = [genre["name"] for genre in movie.get("genres", [])]

            tags = None
            if include_keywords:
                tags = self._fetch_movie_keywords(tmdb_id, api_key)

            extra_metadata: dict[str, Any] = {}
            if movie.get("runtime"):
                extra_metadata["runtime"] = movie["runtime"]
            if movie.get("vote_average"):
                extra_metadata["tmdb_rating"] = movie["vote_average"]
            if movie.get("release_date"):
                extra_metadata["release_date"] = movie["release_date"]
                try:
                    extra_metadata["release_year"] = int(movie["release_date"][:4])
                except (ValueError, IndexError):
                    pass
            if movie.get("original_language"):
                extra_metadata["original_language"] = movie["original_language"]
            if movie.get("production_companies"):
                studios = [
                    company["name"] for company in movie["production_companies"][:3]
                ]
                if studios:
                    extra_metadata["studio"] = studios[0]

            # The truthiness guard on `name` excludes missing/None/empty values;
            # the str() cast hardens against non-string names in the API payload.
            crew = movie.get("credits", {}).get("crew", [])
            directors = [
                str(member["name"])
                for member in crew
                if member.get("job") == "Director" and member.get("name")
            ][:3]
            if directors:
                extra_metadata["director"] = ", ".join(directors)

            collection = movie.get("belongs_to_collection")
            if collection:
                extra_metadata["series_name"] = collection.get("name")
                extra_metadata["tmdb_collection_id"] = collection.get("id")

            return EnrichmentResult(
                genres=genres if genres else None,
                tags=tags,
                description=movie.get("overview"),
                cover_url=_poster_url(movie),
                extra_metadata=extra_metadata,
                match_quality="high",
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name,
                f"Failed to fetch movie details: {scrub_request_error(error)}",
            ) from None

    def _fetch_tv_details(
        self,
        tmdb_id: int,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult:
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/tv/{tmdb_id}",
                params={"api_key": api_key, "language": language},
                timeout=10,
            )
            response.raise_for_status()
            show = response.json()

            genres = [genre["name"] for genre in show.get("genres", [])]

            tags = None
            if include_keywords:
                tags = self._fetch_tv_keywords(tmdb_id, api_key)

            extra_metadata: dict[str, Any] = {}
            if show.get("number_of_seasons"):
                extra_metadata["seasons"] = show["number_of_seasons"]
            if show.get("number_of_episodes"):
                extra_metadata["episodes"] = show["number_of_episodes"]
            # Season 0 is the specials, and its falsy number drops out here.
            season_counts = {
                str(season["season_number"]): season["episode_count"]
                for season in show.get("seasons") or []
                if season.get("season_number") and season.get("episode_count")
            }
            if season_counts:
                extra_metadata["season_episode_counts"] = season_counts
            if show.get("vote_average"):
                extra_metadata["tmdb_rating"] = show["vote_average"]
            if show.get("first_air_date"):
                extra_metadata["first_air_date"] = show["first_air_date"]
                try:
                    extra_metadata["release_year"] = int(show["first_air_date"][:4])
                except (ValueError, IndexError):
                    pass
            if show.get("original_language"):
                extra_metadata["original_language"] = show["original_language"]
            if show.get("networks"):
                networks = [network["name"] for network in show["networks"][:2]]
                if networks:
                    extra_metadata["network"] = networks[0]
            # The truthiness guard on `name` excludes missing/None/empty values;
            # the str() cast hardens against non-string names in the API payload.
            creators = [
                str(creator["name"])
                for creator in show.get("created_by", [])
                if creator.get("name")
            ][:3]
            if creators:
                extra_metadata["creators"] = ", ".join(creators)
            if show.get("status"):
                extra_metadata["status"] = show["status"]

            return EnrichmentResult(
                genres=genres if genres else None,
                tags=tags,
                description=show.get("overview"),
                cover_url=_poster_url(show),
                extra_metadata=extra_metadata,
                match_quality="high",
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name,
                f"Failed to fetch TV show details: {scrub_request_error(error)}",
            ) from None

    def _fetch_keywords(
        self,
        media_type: str,
        tmdb_id: int,
        api_key: str,
        result_key: str = "keywords",
    ) -> list[str] | None:
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/{media_type}/{tmdb_id}/keywords",
                params={"api_key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            keywords = [keyword["name"] for keyword in data.get(result_key, [])[:20]]
            return keywords if keywords else None

        except requests.RequestException:
            logger.warning("Failed to fetch keywords for %s %s", media_type, tmdb_id)
            return None

    def _fetch_movie_keywords(self, tmdb_id: int, api_key: str) -> list[str] | None:
        return self._fetch_keywords("movie", tmdb_id, api_key, "keywords")

    def _fetch_tv_keywords(self, tmdb_id: int, api_key: str) -> list[str] | None:
        return self._fetch_keywords("tv", tmdb_id, api_key, "results")
