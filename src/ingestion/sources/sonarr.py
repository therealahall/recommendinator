"""Sonarr TV series import plugin."""

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


class SonarrPlugin(SourcePlugin):
    """Plugin for importing TV series from Sonarr.

    Sonarr is a TV series management tool. This plugin fetches all series
    in your Sonarr library and imports them as UNREAD (wishlisted) TV show items.

    Note: Sonarr tracks downloads, not watch status. All imported items
    are set to UNREAD. The monitored state is ignored since it can change
    based on file availability. Use ratings and manual status updates to
    track what you've actually watched.
    """

    @property
    def name(self) -> str:
        return "sonarr"

    @property
    def display_name(self) -> str:
        return "Sonarr"

    @property
    def description(self) -> str:
        return "Import TV series from Sonarr"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.TV_SHOW]

    @property
    def requires_api_key(self) -> bool:
        return True

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Strip and normalise Sonarr YAML config."""
        return {
            "url": (raw_config.get("url", "http://localhost:8989") or "").rstrip("/"),
            "api_key": (raw_config.get("api_key") or "").strip(),
        }

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

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch TV series from Sonarr API.

        Args:
            config: Must contain 'url' and 'api_key'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each series in the library

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
        total = len(series_list)
        count = 0

        for series in series_list:
            title = series.get("title", "").strip()
            if not title:
                continue

            # Sonarr tracks downloads, not watch status.
            # All imported items are UNREAD (wishlisted).
            status = ConsumptionStatus.UNREAD

            # No personal ratings - Sonarr doesn't track user ratings, only
            # external aggregate scores (TVDB) which would mislead recommendations
            rating = None

            # Build external ID for deduplication
            tvdb_id = series.get("tvdbId")
            external_id = f"tvdb:{tvdb_id}" if tvdb_id else None

            # Extract metadata
            metadata = _build_sonarr_metadata(series)

            if progress_callback:
                progress_callback(count, total, title)

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
            count += 1


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
