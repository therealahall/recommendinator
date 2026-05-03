"""Radarr movie import plugin."""

import logging
from typing import Any

import requests

from src.ingestion.sources.arr_base import ArrPlugin
from src.models.content import ContentType

logger = logging.getLogger(__name__)


class RadarrPlugin(ArrPlugin):
    """Plugin for importing movies from Radarr.

    Radarr is a movie management tool. This plugin fetches all movies
    in your Radarr library and imports them as UNREAD (wishlisted) movie items.

    Note: Radarr tracks downloads, not watch status. All imported items
    are set to UNREAD. The monitored state is ignored since it can change
    based on file availability. Use ratings and manual status updates to
    track what you've actually watched.
    """

    @property
    def name(self) -> str:
        return "radarr"

    @property
    def display_name(self) -> str:
        return "Radarr"

    @property
    def description(self) -> str:
        return "Import movies from Radarr"

    @property
    def default_port(self) -> int:
        return 7878

    @property
    def arr_api_endpoint(self) -> str:
        return "movie"

    @property
    def arr_content_type(self) -> ContentType:
        return ContentType.MOVIE

    @classmethod
    def _get_default_url(cls) -> str:
        return "http://localhost:7878"

    def build_external_id(self, item: dict[str, Any]) -> str | None:
        tmdb_id = item.get("tmdbId")
        return f"tmdb:{tmdb_id}" if tmdb_id else None

    def build_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        return _build_radarr_metadata(item)

    def post_fetch(
        self,
        base_url: str,
        api_key: str,
        item: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Add collection/series info from Radarr collections."""
        # Lazy-load collection map on first call
        if not hasattr(self, "_collection_map"):
            self._collection_map = _fetch_radarr_collections(base_url, api_key)

        tmdb_id = item.get("tmdbId")
        collection_info = self._collection_map.get(tmdb_id) if tmdb_id else None
        if collection_info:
            metadata["series_name"] = collection_info["title"]
            metadata["movie_number"] = collection_info["order"]


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
        logger.warning("Could not fetch Radarr collections: %s", error)
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
        logger.info("Loaded collection info for %d movies", len(result))
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
