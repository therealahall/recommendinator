"""GOG.com integration plugin for fetching user game library and wishlist."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    CredentialUpdateCallback,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.progress import log_progress

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# GOG Galaxy public client credentials — used by all Galaxy-compatible integrations.
# These are NOT user secrets; they are published in GOG Galaxy's open-source SDK
# and widely used by community integrations.
GOG_CLIENT_ID = "46899977096215655"
GOG_CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"

GOG_AUTH_URL = "https://auth.gog.com/token"
GOG_EMBED_URL = "https://embed.gog.com"
GOG_API_URL = "https://api.gog.com"


class GogAPIError(Exception):
    """Exception raised for GOG API errors."""

    pass


def refresh_access_token(refresh_token: str) -> dict[str, str]:
    """Exchange a GOG OAuth refresh token for a new access token.

    Args:
        refresh_token: GOG OAuth refresh token obtained from browser login.

    Returns:
        Dictionary with 'access_token' and 'refresh_token' keys.

    Raises:
        GogAPIError: If the token refresh fails.
    """
    params = {
        "client_id": GOG_CLIENT_ID,
        "client_secret": GOG_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        response = requests.get(GOG_AUTH_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise GogAPIError("Response missing access_token")
        return {
            "access_token": str(access_token),
            "refresh_token": str(data.get("refresh_token", refresh_token)),
        }
    except requests.RequestException as error:
        logger.error("Error refreshing GOG access token: %s", error)
        raise GogAPIError(
            f"Failed to refresh access token: {type(error).__name__}"
        ) from error


def get_owned_games(
    access_token: str,
    rate_limit_seconds: float = 1.0,
) -> list[dict[str, Any]]:
    """Fetch all owned games from GOG account, paginating through results.

    Args:
        access_token: Valid GOG OAuth access token.
        rate_limit_seconds: Delay between paginated requests.

    Returns:
        Flat list of all product dictionaries from the GOG API.

    Raises:
        GogAPIError: If the API request fails.
    """
    all_products: list[dict[str, Any]] = []
    page = 1
    total_pages = 1  # Will be updated from first response

    headers = {"Authorization": f"Bearer {access_token}"}

    while page <= total_pages:
        url = f"{GOG_EMBED_URL}/account/getFilteredProducts"
        params: dict[str, Any] = {"mediaType": 1, "page": page}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()

            total_pages = data.get("totalPages", 1)
            products = data.get("products", [])
            all_products.extend(products)

            logger.info(
                "Fetched GOG library page %d/%d (%d products)",
                page,
                total_pages,
                len(products),
            )

            page += 1
            if page <= total_pages and rate_limit_seconds > 0:
                time.sleep(rate_limit_seconds)

        except requests.RequestException as error:
            logger.error("Error fetching GOG owned games (page %d): %s", page, error)
            raise GogAPIError(
                f"Failed to fetch owned games: {type(error).__name__}"
            ) from error

    return all_products


def get_wishlist_product_ids(access_token: str) -> list[int]:
    """Fetch product IDs from the user's GOG wishlist.

    Args:
        access_token: Valid GOG OAuth access token.

    Returns:
        List of product IDs on the wishlist.

    Raises:
        GogAPIError: If the API request fails.
    """
    url = f"{GOG_EMBED_URL}/user/wishlist.json"
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        wishlist = data.get("wishlist", {})
        return [int(product_id) for product_id in wishlist.keys()]
    except requests.RequestException as error:
        logger.error("Error fetching GOG wishlist: %s", error)
        raise GogAPIError(
            f"Failed to fetch wishlist: {type(error).__name__}"
        ) from error


def get_product_details(product_id: int) -> dict[str, Any] | None:
    """Fetch detailed product information from GOG's public API.

    Args:
        product_id: GOG product ID.

    Returns:
        Product details dictionary, or None if product not found (404).

    Raises:
        GogAPIError: If the API request fails (except 404).
    """
    url = f"{GOG_API_URL}/products/{product_id}"
    params = {"expand": "description"}

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 404:
            logger.warning("GOG product %d not found (404)", product_id)
            return None
        response.raise_for_status()
        return dict(response.json())
    except requests.RequestException as error:
        logger.error("Error fetching GOG product %d: %s", product_id, error)
        raise GogAPIError(
            f"Failed to fetch product details for {product_id}: {type(error).__name__}"
        ) from error


def get_multiple_product_details(
    product_ids: list[int],
    rate_limit_seconds: float = 1.0,
    max_retries: int = 3,
    backoff_multiplier: float = 2.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[int, dict[str, Any]]:
    """Fetch detailed information for multiple GOG products with rate limiting.

    Args:
        product_ids: List of GOG product IDs.
        rate_limit_seconds: Base delay between requests.
        max_retries: Maximum number of retries per request on failure.
        backoff_multiplier: Multiplier for exponential backoff on retries.
        progress_callback: Optional callback(current, total) called after each fetch.

    Returns:
        Dictionary mapping product_id to product details.
    """
    details: dict[int, dict[str, Any]] = {}
    total = len(product_ids)

    for index, product_id in enumerate(product_ids):
        current = index + 1

        log_progress(logger, "GOG product details", current, total)

        retry_delay = rate_limit_seconds
        for attempt in range(max_retries + 1):
            try:
                result = get_product_details(product_id)
                if result is not None:
                    details[product_id] = result
                if progress_callback:
                    progress_callback(current, total)
                break
            except GogAPIError:
                if attempt < max_retries:
                    logger.warning(
                        "Retrying GOG product %d in %.1fs (attempt %d/%d)...",
                        product_id,
                        retry_delay,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(retry_delay)
                    retry_delay *= backoff_multiplier
                else:
                    logger.warning(
                        "Max retries exceeded for GOG product %d, skipping.",
                        product_id,
                    )

        # Rate limit between requests (skip after last)
        if index < len(product_ids) - 1 and rate_limit_seconds > 0:
            time.sleep(rate_limit_seconds)

    return details


class GogPlugin(SourcePlugin):
    """Plugin for importing video games from a GOG.com library.

    Uses GOG's embed API to fetch owned games and wishlist items.
    Requires a GOG OAuth refresh token for authentication.
    """

    @property
    def name(self) -> str:
        return "gog"

    @property
    def display_name(self) -> str:
        return "GOG"

    @property
    def description(self) -> str:
        return "Import games from GOG.com library"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Normalise GOG YAML config (strip whitespace, apply defaults)."""
        return {
            "refresh_token": raw_config.get("refresh_token", "").strip(),
            "include_wishlist": raw_config.get("include_wishlist", True),
            "enrich_wishlist": raw_config.get("enrich_wishlist", True),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="refresh_token",
                field_type=str,
                required=True,
                sensitive=True,
                description="GOG OAuth refresh token for API access",
            ),
            ConfigField(
                name="include_wishlist",
                field_type=bool,
                required=False,
                default=True,
                description="Import wishlisted games as unread items",
            ),
            ConfigField(
                name="enrich_wishlist",
                field_type=bool,
                required=False,
                default=True,
                description="Fetch detailed metadata for wishlisted games",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []
        if not (config.get("refresh_token") or "").strip():
            # Check DB credentials before rejecting
            source_id = config.get("_source_id", self.name)
            if storage is not None:
                db_creds = storage.get_credentials_for_source(user_id, source_id)
                if (db_creds.get("refresh_token") or "").strip():
                    return errors
            errors.append(
                "'refresh_token' is required. "
                "Use the web UI (Data tab) to connect your GOG account, "
                "or see README.md for manual setup instructions."
            )
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch games from a GOG library and optional wishlist.

        Args:
            config: Must contain 'refresh_token'. Optional: 'include_wishlist',
                'enrich_wishlist'.
            progress_callback: Optional callback for progress updates.

        Yields:
            ContentItem for each game in the library/wishlist.

        Raises:
            SourceError: If the GOG API returns an error.
        """

        # Adapter: GOG internal (current, total, phase) -> plugin callback
        def gog_internal_callback(current: int, total: int, phase: str) -> None:
            if progress_callback:
                phase_message = (
                    "Fetching wishlist details..."
                    if phase == "wishlist_details"
                    else "Fetching library..." if phase == "owned_games" else phase
                )
                progress_callback(current, total, phase_message)

        try:
            yield from _fetch_gog_games(
                refresh_token=config.get("refresh_token", "").strip(),
                include_wishlist=config.get("include_wishlist", True),
                enrich_wishlist=config.get("enrich_wishlist", True),
                source=self.get_source_identifier(config),
                progress_callback=gog_internal_callback,
                on_credential_rotated=(
                    config.get("_on_credential_rotated")
                    if callable(config.get("_on_credential_rotated"))
                    else None
                ),
            )
        except GogAPIError as error:
            raise SourceError(self.name, str(error)) from error


def _fetch_gog_games(
    refresh_token: str,
    include_wishlist: bool = True,
    enrich_wishlist: bool = True,
    source: str = "gog",
    progress_callback: Callable[[int, int, str], None] | None = None,
    on_credential_rotated: CredentialUpdateCallback | None = None,
) -> Iterator[ContentItem]:
    """Fetch and parse GOG game library and wishlist.

    Args:
        refresh_token: GOG OAuth refresh token.
        include_wishlist: Whether to import wishlisted games.
        enrich_wishlist: Whether to fetch detailed metadata for wishlist items.
        source: Source identifier for ContentItems.
        progress_callback: Optional callback(current, total, phase).
        on_credential_rotated: Optional callback(key, value) called when the
            refresh token is rotated by the OAuth server.

    Yields:
        ContentItem objects for each game.
    """
    # Phase 1: Authenticate
    logger.info("Refreshing GOG access token...")
    tokens = refresh_access_token(refresh_token)
    access_token = tokens["access_token"]

    # Persist rotated refresh token if it changed
    new_refresh_token = tokens.get("refresh_token")
    if new_refresh_token and new_refresh_token != refresh_token:
        if on_credential_rotated:
            on_credential_rotated("refresh_token", new_refresh_token)
            logger.info("GOG refresh token rotated and persisted")

    # Phase 2: Fetch owned games
    logger.info("Fetching owned games from GOG...")
    owned_products = get_owned_games(access_token)
    logger.info("Found %d owned games on GOG", len(owned_products))

    if progress_callback:
        progress_callback(len(owned_products), len(owned_products), "owned_games")

    # Track owned product IDs for deduplication with wishlist
    owned_product_ids: set[int] = set()

    # Phase 3: Yield owned games
    count = 0
    for product in owned_products:
        product_id = product.get("id")
        if not product_id:
            continue

        owned_product_ids.add(int(product_id))

        title = (product.get("title") or "").strip()
        if not title:
            continue

        metadata: dict[str, Any] = {
            "gog_product_id": str(product_id),
            "gog_owned": True,
            "gog_wishlisted": False,
        }

        # Extract available metadata from owned games response
        if product.get("slug"):
            metadata["slug"] = product["slug"]
            metadata["url"] = f"https://www.gog.com/game/{product['slug']}"
        if product.get("category"):
            metadata["category"] = product["category"]
        if product.get("globalReleaseDate"):
            metadata["release_date"] = product["globalReleaseDate"]
        if product.get("genres"):
            metadata["genres"] = product["genres"]
        if product.get("tags"):
            metadata["tags"] = product["tags"]
        if product.get("dlcCount") is not None:
            metadata["dlc_count"] = product["dlcCount"]

        # Extract platform availability
        works_on = product.get("worksOn", {})
        if works_on:
            metadata["platforms"] = {
                "windows": works_on.get("Windows", False),
                "mac": works_on.get("Mac", False),
                "linux": works_on.get("Linux", False),
            }

        count += 1

        if progress_callback:
            progress_callback(count, len(owned_products), title)

        yield ContentItem(
            id=str(product_id),
            title=title,
            author=None,
            content_type=ContentType.VIDEO_GAME,
            rating=None,
            review=None,
            status=ConsumptionStatus.UNREAD,
            date_completed=None,
            metadata=metadata,
            source=source,
        )

    # Phase 4: Wishlist (if enabled)
    if not include_wishlist:
        return

    logger.info("Fetching GOG wishlist...")
    wishlist_ids = get_wishlist_product_ids(access_token)
    logger.info("Found %d items on GOG wishlist", len(wishlist_ids))

    # Filter out already-owned games
    new_wishlist_ids = [
        product_id for product_id in wishlist_ids if product_id not in owned_product_ids
    ]
    logger.info(
        "%d wishlist items not already owned (filtered %d duplicates)",
        len(new_wishlist_ids),
        len(wishlist_ids) - len(new_wishlist_ids),
    )

    if not new_wishlist_ids:
        return

    # Optionally enrich wishlist items with public API details
    wishlist_details: dict[int, dict[str, Any]] = {}
    if enrich_wishlist:
        logger.info("Enriching wishlist items with product details...")

        def wishlist_progress(current: int, total: int) -> None:
            if progress_callback:
                progress_callback(current, total, "wishlist_details")

        wishlist_details = get_multiple_product_details(
            new_wishlist_ids,
            progress_callback=wishlist_progress,
        )

    # Yield wishlist items
    for product_id in new_wishlist_ids:
        details = wishlist_details.get(product_id, {})
        title = (details.get("title") or "").strip()

        if not title:
            # Without enrichment, we don't have titles for wishlist items
            logger.debug(
                "Skipping wishlist product %d — no title available", product_id
            )
            continue

        metadata = {
            "gog_product_id": str(product_id),
            "gog_owned": False,
            "gog_wishlisted": True,
        }

        if details:
            if details.get("slug"):
                metadata["slug"] = details["slug"]
                metadata["url"] = f"https://www.gog.com/game/{details['slug']}"
            if details.get("genres"):
                metadata["genres"] = [
                    genre.get("name", "")
                    for genre in details["genres"]
                    if genre.get("name")
                ]
            if details.get("developers"):
                metadata["developers"] = details["developers"]
            if details.get("publishers"):
                metadata["publishers"] = details["publishers"]
            if details.get("description", {}).get("full"):
                metadata["description"] = details["description"]["full"]
            if details.get("release_date"):
                metadata["release_date"] = details["release_date"]

        yield ContentItem(
            id=str(product_id),
            title=title,
            author=None,
            content_type=ContentType.VIDEO_GAME,
            rating=None,
            review=None,
            status=ConsumptionStatus.UNREAD,
            date_completed=None,
            metadata=metadata,
            source=source,
        )
