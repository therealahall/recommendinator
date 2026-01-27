"""Radarr movie import plugin."""

import logging
from collections.abc import Iterator
from typing import Any

import requests

from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType

logger = logging.getLogger(__name__)


class RadarrPlugin(SourcePlugin):
    """Plugin for importing movies from Radarr.

    Radarr is a movie management tool. This plugin fetches all monitored
    movies and imports them as UNREAD (wishlisted) movie items.

    Note: Radarr tracks downloads, not watch status. All imported items
    are set to UNREAD. To mark items as completed/in-progress, use a
    watch tracker like Trakt, Jellyfin, or Plex (future integration).
    """

    @property
    def name(self) -> str:
        return "radarr"

    @property
    def display_name(self) -> str:
        return "Radarr"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                default="http://localhost:7878",
                description="Radarr base URL",
            ),
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="Radarr API key (Settings > General > Security)",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key", "").strip():
            errors.append(
                "'api_key' is required. "
                "Find it in Radarr: Settings > General > Security"
            )
        if not config.get("url", "").strip():
            errors.append("'url' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        """Fetch movies from Radarr API.

        Args:
            config: Must contain 'url' and 'api_key'

        Yields:
            ContentItem for each monitored movie

        Raises:
            SourceError: If the Radarr API returns an error
        """
        base_url = config.get("url", "http://localhost:7878").rstrip("/")
        api_key = config.get("api_key", "").strip()

        try:
            movie_list = _fetch_radarr_movies(base_url, api_key)
        except requests.RequestException as error:
            raise SourceError(
                self.name, f"Failed to connect to Radarr at {base_url}: {error}"
            ) from error

        source = self.get_source_identifier()

        for movie in movie_list:
            # Skip unmonitored movies
            if not movie.get("monitored", False):
                continue

            title = movie.get("title", "").strip()
            if not title:
                continue

            # Radarr tracks downloads, not watch status.
            # All imported items are UNREAD (wishlisted).
            status = ConsumptionStatus.UNREAD

            # Extract rating
            rating = _extract_movie_rating(movie)

            # Build external ID for deduplication
            tmdb_id = movie.get("tmdbId")
            external_id = f"tmdb:{tmdb_id}" if tmdb_id else None

            # Extract metadata
            metadata = _build_radarr_metadata(movie)

            yield ContentItem(
                id=external_id,
                title=title,
                author=None,
                content_type=ContentType.MOVIE,
                rating=rating,
                status=status,
                metadata=metadata,
                source=source,
            )


def _fetch_radarr_movies(
    base_url: str, api_key: str
) -> list[dict[str, Any]]:
    """Fetch all movies from Radarr API.

    Args:
        base_url: Radarr base URL
        api_key: Radarr API key

    Returns:
        List of movie dictionaries

    Raises:
        requests.RequestException: On network/API errors
    """
    url = f"{base_url}/api/v3/movie"
    headers = {"X-Api-Key": api_key}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        logger.warning("Unexpected Radarr API response format")
        return []

    logger.info(f"Fetched {len(data)} movies from Radarr")
    return list(data)


def _extract_movie_rating(movie: dict[str, Any]) -> int | None:
    """Extract and normalize rating from Radarr movie data.

    Tries IMDb rating first, then TMDB. Both use 0-10 scale, divided by 2.

    Args:
        movie: Radarr movie data dict

    Returns:
        Normalized rating (1-5) or None
    """
    ratings = movie.get("ratings", {})
    if not ratings:
        return None

    # Try IMDb rating first, then TMDB
    for source_key in ("imdb", "tmdb"):
        source_ratings = ratings.get(source_key, {})
        raw_value = source_ratings.get("value")
        if raw_value is not None and raw_value != 0:
            try:
                scaled = float(raw_value) / 2.0
                return max(1, min(5, round(scaled)))
            except (ValueError, TypeError):
                continue

    return None


def _build_radarr_metadata(movie: dict[str, Any]) -> dict[str, Any]:
    """Build metadata dict from Radarr movie data.

    Args:
        movie: Radarr movie data dict

    Returns:
        Metadata dictionary
    """
    metadata: dict[str, Any] = {}

    # Basic info
    if movie.get("tmdbId"):
        metadata["tmdb_id"] = movie["tmdbId"]
    if movie.get("imdbId"):
        metadata["imdb_id"] = movie["imdbId"]
    if movie.get("year"):
        metadata["year"] = movie["year"]
    if movie.get("studio"):
        metadata["studio"] = movie["studio"]
    if movie.get("overview"):
        metadata["overview"] = movie["overview"]
    if movie.get("runtime"):
        metadata["runtime_minutes"] = movie["runtime"]

    # Genres
    genres = movie.get("genres", [])
    if genres:
        metadata["genres"] = genres

    # Movie status
    if movie.get("status"):
        metadata["movie_status"] = movie["status"]
    if movie.get("hasFile") is not None:
        metadata["has_file"] = movie["hasFile"]

    return metadata
