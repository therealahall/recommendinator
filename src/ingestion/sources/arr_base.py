"""Base class for *arr (Radarr, Sonarr) import plugins."""

from __future__ import annotations

import logging
from abc import abstractmethod
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
from src.utils.progress import log_progress

logger = logging.getLogger(__name__)


class ArrPlugin(SourcePlugin):
    """Base class for Radarr/Sonarr-style media management plugins.

    Subclasses must implement the abstract properties and methods that
    define the service-specific details (name, port, API endpoint, etc.).

    All *arr tools track downloads, not consumption status. Imported items
    are set to UNREAD with no personal rating.
    """

    @property
    @abstractmethod
    def default_port(self) -> int:
        """Default port for this *arr service (e.g. 7878 for Radarr)."""

    @property
    @abstractmethod
    def arr_api_endpoint(self) -> str:
        """API endpoint path relative to /api/v3/ (e.g. 'movie' or 'series')."""

    @property
    @abstractmethod
    def arr_content_type(self) -> ContentType:
        """Content type produced by this plugin."""

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def content_types(self) -> list[ContentType]:
        return [self.arr_content_type]

    @property
    def _default_url(self) -> str:
        return f"http://localhost:{self.default_port}"

    @classmethod
    def _get_default_url(cls) -> str:
        """Get default URL. Needed for classmethod transform_config."""
        # Subclasses should override if needed; fallback for the class
        return "http://localhost"

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Strip and normalise *arr YAML config."""
        return {
            "url": (raw_config.get("url", cls._get_default_url()) or "").rstrip("/"),
            "api_key": (raw_config.get("api_key") or "").strip(),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                default=self._default_url,
                description=f"{self.display_name} base URL",
            ),
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description=(
                    f"{self.display_name} API key (Settings > General > Security)"
                ),
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key", "").strip():
            errors.append(
                "'api_key' is required. "
                f"Find it in {self.display_name}: Settings > General > Security"
            )
        if not config.get("url", "").strip():
            errors.append("'url' is required")
        return errors

    @abstractmethod
    def build_external_id(self, item: dict[str, Any]) -> str | None:
        """Build external ID for deduplication.

        Args:
            item: Raw API item dict

        Returns:
            External ID string (e.g. 'tmdb:12345') or None
        """

    @abstractmethod
    def build_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """Build metadata dict from an API item.

        Args:
            item: Raw API item dict

        Returns:
            Metadata dictionary
        """

    def post_fetch(
        self,
        base_url: str,
        api_key: str,
        item: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Hook for subclasses to augment metadata after the main fetch.

        Called for each item before yielding. Override to add extra
        data (e.g. Radarr collections).

        Args:
            base_url: Service base URL
            api_key: Service API key
            item: Raw API item dict
            metadata: Metadata dict (modified in place)
        """

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch items from the *arr API.

        Args:
            config: Must contain 'url' and 'api_key'
            progress_callback: Optional callback for progress updates

        Yields:
            ContentItem for each item in the library

        Raises:
            SourceError: If the API returns an error
        """
        base_url = config.get("url", self._default_url).rstrip("/")
        api_key = config.get("api_key", "").strip()

        logger.info("Fetching items from %s...", self.display_name)
        try:
            item_list = self._fetch_items(base_url, api_key)
        except requests.RequestException as error:
            raise SourceError(
                self.name,
                f"Failed to connect to {self.display_name} at {base_url}: {error}",
            ) from error

        source = self.get_source_identifier(config)
        total = len(item_list)
        processed_count = 0

        for item in item_list:
            title = item.get("title", "").strip()
            if not title:
                continue

            external_id = self.build_external_id(item)
            metadata = self.build_metadata(item)
            self.post_fetch(base_url, api_key, item, metadata)

            processed_count += 1
            log_progress(logger, f"{self.display_name} items", processed_count, total)

            if progress_callback:
                progress_callback(processed_count, total, title)

            yield ContentItem(
                id=external_id,
                title=title,
                author=None,
                content_type=self.arr_content_type,
                rating=None,
                status=ConsumptionStatus.UNREAD,
                metadata=metadata,
                source=source,
            )

        logger.info("Imported %d items from %s", processed_count, self.display_name)

    def _fetch_items(self, base_url: str, api_key: str) -> list[dict[str, Any]]:
        """Fetch all items from the *arr API.

        Args:
            base_url: Service base URL
            api_key: Service API key

        Returns:
            List of item dictionaries

        Raises:
            requests.RequestException: On network/API errors
        """
        url = f"{base_url}/api/v3/{self.arr_api_endpoint}"
        headers = {"X-Api-Key": api_key}

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, list):
            logger.warning("Unexpected %s API response format", self.display_name)
            return []

        logger.info("Fetched %d items from %s", len(data), self.display_name)
        return list(data)
