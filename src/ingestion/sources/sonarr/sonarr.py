import logging
from typing import Any

from src.ingestion.sources.arr_base import ArrPlugin
from src.models.content import ContentType

logger = logging.getLogger(__name__)


class SonarrPlugin(ArrPlugin):
    """The monitored state is ignored since it can change based on file availability."""

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
    def default_port(self) -> int:
        return 8989

    @property
    def arr_api_endpoint(self) -> str:
        return "series"

    @property
    def arr_content_type(self) -> ContentType:
        return ContentType.TV_SHOW

    @classmethod
    def _get_default_url(cls) -> str:
        return "http://localhost:8989"

    def build_external_id(self, item: dict[str, Any]) -> str | None:
        tvdb_id = item.get("tvdbId")
        return f"tvdb:{tvdb_id}" if tvdb_id else None

    def build_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        return _build_sonarr_metadata(item)


def _build_sonarr_metadata(series: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

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

    genres = series.get("genres", [])
    if genres:
        metadata["genres"] = genres

    # Keys must match tv_show_details column mapping in sqlite_db.py
    statistics = series.get("statistics", {})
    if statistics:
        if statistics.get("seasonCount"):
            metadata["seasons"] = statistics["seasonCount"]
        if statistics.get("episodeCount"):
            metadata["episodes"] = statistics["episodeCount"]
        if statistics.get("episodeFileCount"):
            metadata["downloaded_episodes"] = statistics["episodeFileCount"]

    if series.get("seriesType"):
        metadata["series_type"] = series["seriesType"]
    if series.get("status"):
        metadata["series_status"] = series["status"]

    return metadata
