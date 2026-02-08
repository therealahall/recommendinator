"""TMDB enrichment provider for movies and TV shows.

The Movie Database (TMDB) provides comprehensive metadata for movies
and TV shows, including genres, descriptions, and more.
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

# TMDB API base URL
TMDB_API_BASE = "https://api.themoviedb.org/3"

# Patterns to clean from titles before searching
# Year in parentheses: (2022), (1999)
YEAR_PATTERN = re.compile(r"\s*\(\d{4}\)\s*$")
# Country codes: (US), (UK), (JP), etc.
COUNTRY_PATTERN = re.compile(r"\s*\([A-Z]{2,3}\)\s*$")


def clean_title_for_search(title: str) -> str:
    """Remove year and country suffixes from title for better search matching.

    Examples:
        "Monster (2022)" -> "Monster"
        "Euphoria (US)" -> "Euphoria"
        "The Office (US)" -> "The Office"

    Args:
        title: Original movie/TV show title

    Returns:
        Cleaned title without year/country suffixes
    """
    cleaned = title
    # Remove year suffix
    cleaned = YEAR_PATTERN.sub("", cleaned).strip()
    # Remove country code suffix
    cleaned = COUNTRY_PATTERN.sub("", cleaned).strip()
    return cleaned if cleaned else title


class TMDBProvider(EnrichmentProvider):
    """Enrichment provider using The Movie Database (TMDB) API.

    Enriches movies and TV shows with:
    - Genres
    - Tags (derived from keywords)
    - Description (overview)
    - Additional metadata (runtime, vote average, etc.)

    Matching strategy:
    1. Direct ID lookup if item has tmdb_id in metadata
    2. Title + year search for movies/TV shows
    3. Falls back to title-only search if year not available
    """

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
                default="en-US",
                description="Language for results (e.g., 'en-US', 'de-DE')",
            ),
            ConfigField(
                name="include_keywords",
                field_type=bool,
                required=False,
                default=True,
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
        """Enrich a movie or TV show with TMDB metadata.

        Args:
            item: ContentItem to enrich (must be MOVIE or TV_SHOW)
            config: Provider configuration with api_key

        Returns:
            EnrichmentResult with metadata, or None if not found

        Raises:
            ProviderError: If API request fails
        """
        api_key = config.get("api_key", "")
        language = config.get("language", "en-US")
        include_keywords = config.get("include_keywords", True)

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
            logger.warning(f"TMDB provider does not support {content_type}")
            return None

    def _enrich_movie(
        self,
        item: ContentItem,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult | None:
        """Enrich a movie with TMDB data."""
        # Try to find TMDB ID
        tmdb_id = self._get_tmdb_id(item, "movie")

        if tmdb_id is None:
            # Search for the movie
            tmdb_id = self._search_movie(item, api_key, language)

        if tmdb_id is None:
            return EnrichmentResult(
                match_quality="not_found",
                provider=self.name,
            )

        # Fetch movie details
        return self._fetch_movie_details(tmdb_id, api_key, language, include_keywords)

    def _enrich_tv_show(
        self,
        item: ContentItem,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult | None:
        """Enrich a TV show with TMDB data."""
        # Try to find TMDB ID
        tmdb_id = self._get_tmdb_id(item, "tv")

        if tmdb_id is None:
            # Search for the TV show
            tmdb_id = self._search_tv_show(item, api_key, language)

        if tmdb_id is None:
            return EnrichmentResult(
                match_quality="not_found",
                provider=self.name,
            )

        # Fetch TV show details
        return self._fetch_tv_details(tmdb_id, api_key, language, include_keywords)

    def _get_tmdb_id(self, item: ContentItem, media_type: str) -> int | None:
        """Extract TMDB ID from item metadata if available.

        Args:
            item: ContentItem to check
            media_type: "movie" or "tv"

        Returns:
            TMDB ID if found, None otherwise
        """
        metadata = item.metadata or {}

        # Check for tmdb_id in metadata
        if "tmdb_id" in metadata:
            try:
                return int(metadata["tmdb_id"])
            except (ValueError, TypeError):
                pass

        # Check external_id format like "tmdb:12345"
        if item.id and item.id.startswith("tmdb:"):
            try:
                return int(item.id.split(":")[1])
            except (ValueError, IndexError):
                pass

        return None

    def _search_movie(
        self, item: ContentItem, api_key: str, language: str
    ) -> int | None:
        """Search for a movie by title and year.

        Returns:
            TMDB movie ID if found, None otherwise
        """
        # Clean title to remove year/country suffixes like "(2022)" or "(US)"
        search_title = clean_title_for_search(item.title)
        if search_title != item.title:
            logger.debug(
                f"Cleaned title for search: '{item.title}' -> '{search_title}'"
            )

        params = {
            "api_key": api_key,
            "query": search_title,
            "language": language,
        }

        # Add year if available
        metadata = item.metadata or {}
        year = metadata.get("release_year") or metadata.get("year_published")
        if year:
            params["year"] = str(year)

        try:
            response = requests.get(
                f"{TMDB_API_BASE}/search/movie",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if results:
                # Return the first match
                return int(results[0]["id"])

            # Try without year if no results
            if year and "year" in params:
                del params["year"]
                response = requests.get(
                    f"{TMDB_API_BASE}/search/movie",
                    params=params,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                if results:
                    return int(results[0]["id"])

            return None

        except requests.RequestException as error:
            raise ProviderError(self.name, f"Failed to search TMDB: {error}") from error

    def _search_tv_show(
        self, item: ContentItem, api_key: str, language: str
    ) -> int | None:
        """Search for a TV show by title and year.

        Returns:
            TMDB TV show ID if found, None otherwise
        """
        # Clean title to remove year/country suffixes like "(2022)" or "(US)"
        search_title = clean_title_for_search(item.title)
        if search_title != item.title:
            logger.debug(
                f"Cleaned title for search: '{item.title}' -> '{search_title}'"
            )

        params = {
            "api_key": api_key,
            "query": search_title,
            "language": language,
        }

        # Add first air date year if available
        metadata = item.metadata or {}
        year = metadata.get("release_year") or metadata.get("year_published")
        if year:
            params["first_air_date_year"] = str(year)

        try:
            response = requests.get(
                f"{TMDB_API_BASE}/search/tv",
                params=params,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if results:
                return int(results[0]["id"])

            # Try without year if no results
            if year and "first_air_date_year" in params:
                del params["first_air_date_year"]
                response = requests.get(
                    f"{TMDB_API_BASE}/search/tv",
                    params=params,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                if results:
                    return int(results[0]["id"])

            return None

        except requests.RequestException as error:
            raise ProviderError(self.name, f"Failed to search TMDB: {error}") from error

    def _fetch_movie_details(
        self,
        tmdb_id: int,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult:
        """Fetch detailed movie information from TMDB.

        Args:
            tmdb_id: TMDB movie ID
            api_key: API key
            language: Language code
            include_keywords: Whether to fetch keywords

        Returns:
            EnrichmentResult with movie metadata
        """
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/movie/{tmdb_id}",
                params={"api_key": api_key, "language": language},
                timeout=10,
            )
            response.raise_for_status()
            movie = response.json()

            # Extract genres
            genres = [genre["name"] for genre in movie.get("genres", [])]

            # Extract keywords/tags if requested
            tags = None
            if include_keywords:
                tags = self._fetch_movie_keywords(tmdb_id, api_key)

            # Build extra metadata
            extra_metadata: dict[str, Any] = {}
            if movie.get("runtime"):
                extra_metadata["runtime"] = movie["runtime"]
            if movie.get("vote_average"):
                extra_metadata["tmdb_rating"] = movie["vote_average"]
            if movie.get("release_date"):
                extra_metadata["release_date"] = movie["release_date"]
                # Extract year
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

            return EnrichmentResult(
                external_id=f"tmdb:{tmdb_id}",
                genres=genres if genres else None,
                tags=tags,
                description=movie.get("overview"),
                extra_metadata=extra_metadata,
                match_quality="high",
                provider=self.name,
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to fetch movie details: {error}"
            ) from error

    def _fetch_tv_details(
        self,
        tmdb_id: int,
        api_key: str,
        language: str,
        include_keywords: bool,
    ) -> EnrichmentResult:
        """Fetch detailed TV show information from TMDB.

        Args:
            tmdb_id: TMDB TV show ID
            api_key: API key
            language: Language code
            include_keywords: Whether to fetch keywords

        Returns:
            EnrichmentResult with TV show metadata
        """
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/tv/{tmdb_id}",
                params={"api_key": api_key, "language": language},
                timeout=10,
            )
            response.raise_for_status()
            show = response.json()

            # Extract genres
            genres = [genre["name"] for genre in show.get("genres", [])]

            # Extract keywords/tags if requested
            tags = None
            if include_keywords:
                tags = self._fetch_tv_keywords(tmdb_id, api_key)

            # Build extra metadata
            extra_metadata: dict[str, Any] = {}
            if show.get("number_of_seasons"):
                extra_metadata["seasons"] = show["number_of_seasons"]
            if show.get("number_of_episodes"):
                extra_metadata["episodes"] = show["number_of_episodes"]
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
            if show.get("created_by"):
                creators = [creator["name"] for creator in show["created_by"][:3]]
                if creators:
                    extra_metadata["creators"] = ", ".join(creators)
            if show.get("status"):
                extra_metadata["status"] = show["status"]

            return EnrichmentResult(
                external_id=f"tmdb:{tmdb_id}",
                genres=genres if genres else None,
                tags=tags,
                description=show.get("overview"),
                extra_metadata=extra_metadata,
                match_quality="high",
                provider=self.name,
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to fetch TV show details: {error}"
            ) from error

    def _fetch_movie_keywords(self, tmdb_id: int, api_key: str) -> list[str] | None:
        """Fetch keywords for a movie.

        Args:
            tmdb_id: TMDB movie ID
            api_key: API key

        Returns:
            List of keyword strings, or None if unavailable
        """
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/movie/{tmdb_id}/keywords",
                params={"api_key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            keywords = [
                keyword["name"] for keyword in data.get("keywords", [])[:20]  # Limit
            ]
            return keywords if keywords else None

        except requests.RequestException:
            # Keywords are optional, don't fail the whole enrichment
            logger.warning(f"Failed to fetch keywords for movie {tmdb_id}")
            return None

    def _fetch_tv_keywords(self, tmdb_id: int, api_key: str) -> list[str] | None:
        """Fetch keywords for a TV show.

        Args:
            tmdb_id: TMDB TV show ID
            api_key: API key

        Returns:
            List of keyword strings, or None if unavailable
        """
        try:
            response = requests.get(
                f"{TMDB_API_BASE}/tv/{tmdb_id}/keywords",
                params={"api_key": api_key},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            keywords = [
                keyword["name"] for keyword in data.get("results", [])[:20]  # Limit
            ]
            return keywords if keywords else None

        except requests.RequestException:
            # Keywords are optional, don't fail the whole enrichment
            logger.warning(f"Failed to fetch keywords for TV show {tmdb_id}")
            return None
