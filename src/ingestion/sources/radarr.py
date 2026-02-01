"""Radarr movie import plugin."""

import logging
from collections.abc import Iterator
from typing import Any

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
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

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch movies from Radarr API.

        Args:
            config: Must contain 'url' and 'api_key'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each monitored movie

        Raises:
            SourceError: If the Radarr API returns an error
        """
        base_url = config.get("url", "http://localhost:7878").rstrip("/")
        api_key = config.get("api_key", "").strip()

        try:
            movie_list = _fetch_radarr_movies(base_url, api_key)
            collection_map = _fetch_radarr_collections(base_url, api_key)
        except requests.RequestException as error:
            raise SourceError(
                self.name, f"Failed to connect to Radarr at {base_url}: {error}"
            ) from error

        source = self.get_source_identifier()
        total = len(movie_list)
        count = 0

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

            # No personal ratings - Radarr doesn't track user ratings, only
            # external aggregate scores (IMDb/TMDB) which would mislead recommendations
            rating = None

            # Build external ID for deduplication
            tmdb_id = movie.get("tmdbId")
            external_id = f"tmdb:{tmdb_id}" if tmdb_id else None

            # Extract metadata
            metadata = _build_radarr_metadata(movie)

            # Add series info from Radarr collections (e.g., Back to the Future 1,2,3)
            collection_info = collection_map.get(tmdb_id) if tmdb_id else None
            if collection_info:
                metadata["series_name"] = collection_info["title"]
                metadata["movie_number"] = collection_info["order"]

            if progress_callback:
                progress_callback(count, total, title)

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
            count += 1


def _fetch_radarr_movies(base_url: str, api_key: str) -> list[dict[str, Any]]:
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


def _fetch_radarr_collections(base_url: str, api_key: str) -> dict[int, dict[str, Any]]:
    """Fetch collections and build tmdb_id -> (title, order) map.

    Radarr collections (e.g., Back to the Future) provide movie order for
    series-aware recommendations.

    Args:
        base_url: Radarr base URL
        api_key: Radarr API key

    Returns:
        Map of tmdb_id -> {"title": str, "order": int} for movies in collections
    """
    url = f"{base_url}/api/v3/collection"
    headers = {"X-Api-Key": api_key}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        logger.warning(f"Could not fetch Radarr collections: {error}")
        return {}

    data = response.json()
    if not isinstance(data, list):
        return {}

    result: dict[int, dict[str, Any]] = {}
    for collection in data:
        title = collection.get("title") or collection.get("name") or ""
        if not title:
            continue

        movies = collection.get("movies") or collection.get("items") or []
        for order, movie in enumerate(movies, start=1):
            tmdb_id = None
            if isinstance(movie, dict):
                tmdb_id = movie.get("tmdbId") or movie.get("tmdb_id")
            if tmdb_id is not None:
                result[int(tmdb_id)] = {"title": title, "order": order}

    if result:
        logger.info(f"Loaded collection info for {len(result)} movies")
    return result


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
