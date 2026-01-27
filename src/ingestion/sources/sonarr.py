"""Sonarr TV series import plugin."""

import logging
from collections.abc import Iterator
from typing import Any

import requests

from src.ingestion.plugin_base import ConfigField, SourceError, SourcePlugin
from src.models.content import ConsumptionStatus, ContentItem, ContentType

logger = logging.getLogger(__name__)


class SonarrPlugin(SourcePlugin):
    """Plugin for importing TV series from Sonarr.

    Sonarr is a TV series management tool. This plugin fetches all monitored
    series and imports them as UNREAD (wishlisted) TV show items.

    Note: Sonarr tracks downloads, not watch status. All imported items
    are set to UNREAD. To mark items as completed/in-progress, use a
    watch tracker like Trakt, Jellyfin, or Plex (future integration).
    """

    @property
    def name(self) -> str:
        return "sonarr"

    @property
    def display_name(self) -> str:
        return "Sonarr"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.TV_SHOW]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                default="http://localhost:8989",
                description="Sonarr base URL",
            ),
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="Sonarr API key (Settings > General > Security)",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key", "").strip():
            errors.append(
                "'api_key' is required. "
                "Find it in Sonarr: Settings > General > Security"
            )
        if not config.get("url", "").strip():
            errors.append("'url' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        """Fetch TV series from Sonarr API.

        Args:
            config: Must contain 'url' and 'api_key'

        Yields:
            ContentItem for each monitored series

        Raises:
            SourceError: If the Sonarr API returns an error
        """
        base_url = config.get("url", "http://localhost:8989").rstrip("/")
        api_key = config.get("api_key", "").strip()

        try:
            series_list = _fetch_sonarr_series(base_url, api_key)
        except requests.RequestException as error:
            raise SourceError(
                self.name, f"Failed to connect to Sonarr at {base_url}: {error}"
            ) from error

        source = self.get_source_identifier()

        for series in series_list:
            # Skip unmonitored series (user doesn't care about these)
            if not series.get("monitored", False):
                continue

            title = series.get("title", "").strip()
            if not title:
                continue

            # Sonarr tracks downloads, not watch status.
            # All imported items are UNREAD (wishlisted).
            status = ConsumptionStatus.UNREAD

            # Extract rating if available (Sonarr's 0-10 scale → 1-5)
            rating = self._extract_rating(series)

            # Build external ID for deduplication
            tvdb_id = series.get("tvdbId")
            external_id = f"tvdb:{tvdb_id}" if tvdb_id else None

            # Extract metadata
            metadata = _build_sonarr_metadata(series)

            yield ContentItem(
                id=external_id,
                title=title,
                author=None,
                content_type=ContentType.TV_SHOW,
                rating=rating,
                status=status,
                metadata=metadata,
                source=source,
            )

    def _extract_rating(self, series: dict[str, Any]) -> int | None:
        """Extract and normalize rating from Sonarr series data.

        Sonarr provides ratings on a 0-10 scale. We divide by 2 to get 1-5.

        Args:
            series: Sonarr series data dict

        Returns:
            Normalized rating (1-5) or None
        """
        ratings = series.get("ratings", {})
        if not ratings:
            return None

        raw_value = ratings.get("value")
        if raw_value is None or raw_value == 0:
            return None

        try:
            scaled = float(raw_value) / 2.0
            return max(1, min(5, round(scaled)))
        except (ValueError, TypeError):
            return None


def _fetch_sonarr_series(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all series from Sonarr API.

    Args:
        base_url: Sonarr base URL
        api_key: Sonarr API key

    Returns:
        List of series dictionaries

    Raises:
        requests.RequestException: On network/API errors
    """
    url = f"{base_url}/api/v3/series"
    headers = {"X-Api-Key": api_key}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        logger.warning("Unexpected Sonarr API response format")
        return []

    logger.info(f"Fetched {len(data)} series from Sonarr")
    return list(data)


def _build_sonarr_metadata(series: dict[str, Any]) -> dict[str, Any]:
    """Build metadata dict from Sonarr series data.

    Args:
        series: Sonarr series data dict

    Returns:
        Metadata dictionary
    """
    metadata: dict[str, Any] = {}

    # Basic info
    if series.get("tvdbId"):
        metadata["tvdb_id"] = series["tvdbId"]
    if series.get("imdbId"):
        metadata["imdb_id"] = series["imdbId"]
    if series.get("year"):
        metadata["year"] = series["year"]
    if series.get("network"):
        metadata["network"] = series["network"]
    if series.get("overview"):
        metadata["overview"] = series["overview"]

    # Genres
    genres = series.get("genres", [])
    if genres:
        metadata["genres"] = genres

    # Season/episode info from statistics
    statistics = series.get("statistics", {})
    if statistics:
        if statistics.get("seasonCount"):
            metadata["total_seasons"] = statistics["seasonCount"]
        if statistics.get("episodeCount"):
            metadata["total_episodes"] = statistics["episodeCount"]
        if statistics.get("episodeFileCount"):
            metadata["downloaded_episodes"] = statistics["episodeFileCount"]

    # Series type and status
    if series.get("seriesType"):
        metadata["series_type"] = series["seriesType"]
    if series.get("status"):
        metadata["series_status"] = series["status"]

    return metadata
