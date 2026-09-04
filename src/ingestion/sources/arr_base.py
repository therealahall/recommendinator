from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.ingestion.urls import (
    MAX_SAME_ORIGIN_REDIRECTS,
    REDIRECT_STATUSES,
    REQUEST_TIMEOUT,
    redirect_refusal,
    same_origin,
    source_url_error,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.progress import log_progress

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ArrPlugin(SourcePlugin):
    """All *arr tools track downloads, not consumption status."""

    @property
    @abstractmethod
    def default_port(self) -> int: ...

    @property
    @abstractmethod
    def arr_api_endpoint(self) -> str: ...

    @property
    @abstractmethod
    def arr_content_type(self) -> ContentType: ...

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
        """Needed for classmethod ``transform_fields``."""
        return "http://localhost"

    @classmethod
    def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
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
                description=(
                    "Verify the TLS certificate (disable for a self-signed "
                    "certificate or a private CA)"
                ),
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
    def build_external_id(self, item: dict[str, Any]) -> str | None: ...

    @abstractmethod
    def build_metadata(self, item: dict[str, Any]) -> dict[str, Any]: ...

    def fetch_context(self, base_url: str, api_key: str, verify_ssl: bool) -> Any:
        """Whatever ``post_fetch`` needs for one run (e.g. Radarr collections).

        It belongs to the run, not the plugin: the registry hands every
        configured source the same plugin instance, so a second Radarr once
        read the first one's collections off ``self``.
        """
        return None

    def post_fetch(
        self,
        item: dict[str, Any],
        metadata: dict[str, Any],
        context: Any,
    ) -> None:
        """Add data the main fetch does not carry (e.g. Radarr collections)."""

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        base_url = config.get("url", self._default_url).rstrip("/")
        api_key = config.get("api_key", "").strip()
        verify_ssl = config.get("verify_ssl", True)

        # A scheduled sync skips validate_config, so the api key would
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

        context = self.fetch_context(base_url, api_key, verify_ssl)
        total = len(item_list)
        processed_count = 0

        for item in item_list:
            title = item.get("title", "").strip()
            if not title:
                continue

            external_id = self.build_external_id(item)
            metadata = self.build_metadata(item)
            self.post_fetch(item, metadata, context)

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
            )

        logger.info("Imported %d items from %s", processed_count, self.display_name)

    def _fetch_items(
        self, base_url: str, api_key: str, verify_ssl: bool
    ) -> list[dict[str, Any]]:
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
        """``requests`` replays ``X-Api-Key`` onto any host a redirect names,
        and follows an http->https bounce silently, so a proxy reported an
        unverifiable certificate for a scheme nobody configured.
        """
        current = url
        for _ in range(MAX_SAME_ORIGIN_REDIRECTS):
            response = requests.get(
                current,
                headers={"X-Api-Key": api_key},
                timeout=REQUEST_TIMEOUT,
                verify=verify_ssl,
                allow_redirects=False,
            )
            if response.status_code not in REDIRECT_STATUSES:
                return response
            location = response.headers.get("Location")
            if not location:
                return response

            target = urljoin(current, location)
            if not same_origin(url, target):
                raise SourceError(
                    self.name,
                    redirect_refusal(current, target, self.display_name),
                )
            current = target

        raise SourceError(
            self.name,
            f"{self.display_name} redirected {url} more than "
            f"{MAX_SAME_ORIGIN_REDIRECTS} times.",
        )
