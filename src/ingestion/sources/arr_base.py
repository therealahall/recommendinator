"""Base class for *arr (Radarr, Sonarr) import plugins."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.ingestion.urls import source_url_error
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.progress import log_progress
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Only same-origin hops are followed at all, so this caps a redirect loop
# rather than a chain any real *arr install produces.
_MAX_REDIRECTS = 5


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
        """Get default URL. Needed for classmethod ``transform_fields``."""
        # Subclasses should override if needed; fallback for the class
        return "http://localhost"

    @classmethod
    def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
        """Strip and normalise *arr config fields."""
        return {
            "url": (raw_fields.get("url", cls._get_default_url()) or "").rstrip("/"),
            "api_key": (raw_fields.get("api_key") or "").strip(),
            "verify_ssl": raw_fields.get("verify_ssl", True),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                default=self._default_url,
                credential_bound=True,
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
            ConfigField(
                name="verify_ssl",
                field_type=bool,
                required=False,
                default=True,
                description="Verify the TLS certificate (disable for self-signed)",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors = []
        if not config.get("api_key", "").strip():
            errors.append(
                "'api_key' is required. "
                f"Find it in {self.display_name}: Settings > General > Security"
            )
        url = (config.get("url") or "").strip()
        if not url:
            errors.append("'url' is required")
        else:
            url_error = source_url_error(url)
            if url_error is not None:
                errors.append(url_error)
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
        verify_ssl: bool,
        item: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        """Hook for subclasses to augment ``metadata`` in place.

        Called for each item before it is yielded, so an override can add
        data the main fetch does not carry (e.g. Radarr collections).
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
        verify_ssl = config.get("verify_ssl", True)

        # A sync of every source skips validate_config, so the api key would
        # otherwise reach whatever scheme and host the config now names.
        url_error = source_url_error(base_url)
        if url_error is not None:
            raise SourceError(self.name, url_error)

        logger.info("Fetching items from %s...", self.display_name)
        try:
            item_list = self._fetch_items(base_url, api_key, verify_ssl)
        except requests.exceptions.SSLError as error:
            # Naming TLS separately is the whole point: the generic wording
            # below reads as "unreachable" and sent operators host-hunting.
            raise SourceError(
                self.name,
                f"TLS verification failed for {self.display_name} at "
                f"{base_url}: {error}. Set verify_ssl to false if the "
                "certificate is not publicly trusted.",
            ) from error
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
            self.post_fetch(base_url, api_key, verify_ssl, item, metadata)

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

    def _fetch_items(
        self, base_url: str, api_key: str, verify_ssl: bool
    ) -> list[dict[str, Any]]:
        """Fetch all items from the *arr API.

        Raises:
            SourceError: If a redirect leaves the configured origin
            requests.RequestException: On network/API errors
        """
        url = f"{base_url}/api/v3/{self.arr_api_endpoint}"

        response = self._api_get(url, api_key, verify_ssl)
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, list):
            logger.warning("Unexpected %s API response format", self.display_name)
            return []

        logger.info("Fetched %d items from %s", len(data), self.display_name)
        return list(data)

    def _api_get(self, url: str, api_key: str, verify_ssl: bool) -> requests.Response:
        """GET an *arr API url, refusing a redirect off its origin.

        ``requests`` replays ``X-Api-Key`` onto any host a redirect names, and
        follows an http->https bounce silently, so a proxy reported an
        unverifiable certificate for a scheme nobody configured.
        """
        origin = urlsplit(url)
        current = url
        for _ in range(_MAX_REDIRECTS):
            response = requests.get(
                current,
                headers={"X-Api-Key": api_key},
                timeout=_REQUEST_TIMEOUT,
                verify=verify_ssl,
                allow_redirects=False,
            )
            if response.status_code not in _REDIRECT_STATUSES:
                return response
            location = response.headers.get("Location")
            if not location:
                return response

            target = urljoin(current, location)
            target_parts = urlsplit(target)
            if (target_parts.scheme, target_parts.netloc) != (
                origin.scheme,
                origin.netloc,
            ):
                raise SourceError(self.name, self._redirect_refusal(current, target))
            current = target

        raise SourceError(
            self.name,
            f"{self.display_name} redirected {url} more than "
            f"{_MAX_REDIRECTS} times.",
        )

    def _redirect_refusal(self, url: str, target: str) -> str:
        """Word a refused redirect as the config change it asks for.

        The target is whatever ``Location`` said and this message is logged on
        one line, so it is escaped like any other header text (CWE-117).
        """
        origin = urlsplit(url)
        safe_target = sanitize_for_log(target)
        return (
            f"Refused a redirect from {url} to {safe_target}. It leaves the "
            f"configured origin {origin.scheme}://{origin.netloc}, and the API "
            f"key only goes where the source url points. If {self.display_name} "
            f"really is at {safe_target}, set the source url to it (and "
            "verify_ssl to false if its certificate is not publicly trusted)."
        )
