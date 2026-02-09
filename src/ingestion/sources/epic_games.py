"""Epic Games Store integration plugin for fetching user game library.

Uses the legendary launcher's EPCAPI client to interact with Epic's
reverse-engineered APIs.  Authentication requires an OAuth refresh token
obtained by running ``legendary auth`` and extracting the token from
``~/.config/legendary/user.json``.

Limitations:
- No wishlist support (legendary doesn't implement it).
- No playtime data (Epic doesn't expose it).
"""

import logging
from collections.abc import Iterator
from typing import Any

from legendary.api.egs import EPCAPI
from legendary.models.exceptions import InvalidCredentialsError

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

logger = logging.getLogger(__name__)


class EpicGamesAPIError(Exception):
    """Exception raised for Epic Games API errors."""

    pass


# ---------------------------------------------------------------------------
# Module-level helper functions (individually testable)
# ---------------------------------------------------------------------------


def authenticate(refresh_token: str) -> EPCAPI:
    """Create an authenticated EPCAPI session using a refresh token.

    Args:
        refresh_token: Epic Games OAuth refresh token.

    Returns:
        Authenticated EPCAPI instance ready for API calls.

    Raises:
        EpicGamesAPIError: If authentication fails.
    """
    api = EPCAPI()
    try:
        api.start_session(refresh_token=refresh_token)
    except InvalidCredentialsError as error:
        raise EpicGamesAPIError(
            f"Authentication failed (invalid or expired refresh token): {error}"
        ) from error
    except Exception as error:
        raise EpicGamesAPIError(
            f"Failed to authenticate with Epic Games: {error}"
        ) from error
    return api


def get_library_items(api: EPCAPI) -> list[dict[str, Any]]:
    """Fetch all library records from the authenticated Epic Games account.

    Cursor-based pagination is handled internally by EPCAPI.

    Args:
        api: Authenticated EPCAPI instance.

    Returns:
        Flat list of all library record dicts.

    Raises:
        EpicGamesAPIError: If the API request fails.
    """
    try:
        records: list[dict[str, Any]] = api.get_library_items(include_metadata=True)
        return records
    except Exception as error:
        raise EpicGamesAPIError(f"Failed to fetch library items: {error}") from error


def get_game_metadata(
    api: EPCAPI, namespace: str, catalog_item_id: str
) -> dict[str, Any] | None:
    """Fetch detailed catalog metadata for a single game.

    Args:
        api: Authenticated EPCAPI instance.
        namespace: Epic Games namespace for the game.
        catalog_item_id: Catalog item identifier.

    Returns:
        Catalog metadata dict, or None if the item was not found.

    Raises:
        EpicGamesAPIError: If the API request fails.
    """
    try:
        result: dict[str, Any] | None = api.get_game_info(namespace, catalog_item_id)
        return result
    except Exception as error:
        raise EpicGamesAPIError(
            f"Failed to fetch metadata for {catalog_item_id}: {error}"
        ) from error


def is_base_game(game_metadata: dict[str, Any]) -> bool:
    """Determine whether a catalog item represents a base game (not DLC/mod).

    Args:
        game_metadata: Catalog metadata dict from :func:`get_game_metadata`.

    Returns:
        True if this is a standalone base game, False for DLC or mods.
    """
    # DLC has a reference to the main game
    if "mainGameItem" in game_metadata:
        return False

    # Mods are tagged via the categories list
    categories = game_metadata.get("categories", [])
    if any(
        category.get("path") == "mods"
        for category in categories
        if isinstance(category, dict)
    ):
        return False

    return True


def extract_metadata_fields(
    game_metadata: dict[str, Any],
    library_record: dict[str, Any],
) -> dict[str, Any]:
    """Build a metadata dict from catalog data and the library record.

    Args:
        game_metadata: Catalog metadata from :func:`get_game_metadata`.
        library_record: Library record dict from :func:`get_library_items`.

    Returns:
        Flat dict of metadata fields suitable for :class:`ContentItem`.
    """
    metadata: dict[str, Any] = {
        "epic_namespace": library_record.get("namespace", ""),
        "epic_catalog_item_id": game_metadata.get("id", ""),
        "epic_app_name": library_record.get("appName", ""),
    }

    developer = game_metadata.get("developer")
    if developer:
        metadata["developer"] = developer

    description = game_metadata.get("description")
    if description:
        metadata["description"] = description

    categories = game_metadata.get("categories")
    if categories:
        metadata["categories"] = [
            category.get("path", "")
            for category in categories
            if isinstance(category, dict)
        ]

    release_info = game_metadata.get("releaseInfo")
    if release_info and isinstance(release_info, list) and len(release_info) > 0:
        first_release = release_info[0]
        date_published = first_release.get("dateAdded")
        if date_published:
            metadata["release_date"] = date_published

    return metadata


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class EpicGamesPlugin(SourcePlugin):
    """Plugin for importing video games from an Epic Games Store library.

    Uses the legendary launcher's EPCAPI to fetch owned games.
    Requires an Epic Games OAuth refresh token for authentication.
    """

    @property
    def name(self) -> str:
        return "epic_games"

    @property
    def display_name(self) -> str:
        return "Epic Games Store"

    @property
    def description(self) -> str:
        return "Import games from Epic Games Store library"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Normalise Epic Games YAML config (strip whitespace)."""
        return {
            "refresh_token": raw_config.get("refresh_token", "").strip(),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="refresh_token",
                field_type=str,
                required=True,
                sensitive=True,
                description=(
                    "Epic Games OAuth refresh token. "
                    "Run `legendary auth` then extract from "
                    "~/.config/legendary/user.json"
                ),
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not (config.get("refresh_token") or "").strip():
            errors.append(
                "'refresh_token' is required. "
                "Run `legendary auth` to authenticate, then copy "
                "the refresh_token from ~/.config/legendary/user.json."
            )
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch games from an Epic Games Store library.

        Args:
            config: Must contain 'refresh_token'.
            progress_callback: Optional callback for progress updates.

        Yields:
            ContentItem for each base game in the library.

        Raises:
            SourceError: If the Epic Games API returns an error.
        """
        try:
            yield from _fetch_epic_games(
                refresh_token=config.get("refresh_token", "").strip(),
                source=self.get_source_identifier(),
                progress_callback=progress_callback,
            )
        except EpicGamesAPIError as error:
            raise SourceError(self.name, str(error)) from error


# ---------------------------------------------------------------------------
# Internal fetch implementation
# ---------------------------------------------------------------------------


def _fetch_epic_games(
    refresh_token: str,
    source: str = "epic_games",
    progress_callback: ProgressCallback | None = None,
) -> Iterator[ContentItem]:
    """Fetch and parse Epic Games Store library.

    Args:
        refresh_token: Epic Games OAuth refresh token.
        source: Source identifier for ContentItems.
        progress_callback: Optional callback(current, total, message).

    Yields:
        ContentItem objects for each base game.
    """
    # Phase 1: Authenticate
    logger.info("Authenticating with Epic Games Store...")
    api = authenticate(refresh_token)

    # Phase 2: Fetch all library records
    logger.info("Fetching Epic Games library...")
    library_records = get_library_items(api)
    logger.info(f"Found {len(library_records)} items in Epic Games library")

    if progress_callback:
        progress_callback(0, len(library_records), "Fetching library...")

    # Phase 3: Process each record
    count = 0
    for index, record in enumerate(library_records):
        namespace = record.get("namespace", "")
        catalog_item_id = record.get("catalogItemId", "")
        app_name = record.get("appName", "")

        # Skip Unreal Engine marketplace items
        if namespace == "ue":
            continue

        # Skip private sandbox items
        if record.get("sandboxType") == "PRIVATE":
            continue

        # Skip broken placeholder records
        if app_name == "1":
            continue

        # Fetch detailed metadata
        try:
            game_metadata = get_game_metadata(api, namespace, catalog_item_id)
        except EpicGamesAPIError:
            logger.warning(
                f"Failed to fetch metadata for {app_name} "
                f"({catalog_item_id}), skipping."
            )
            continue

        if game_metadata is None:
            logger.warning(
                f"No metadata found for {app_name} ({catalog_item_id}), skipping."
            )
            continue

        # Skip DLC and mods
        if not is_base_game(game_metadata):
            continue

        # Extract title
        title = (game_metadata.get("title") or "").strip()
        if not title:
            continue

        # Build metadata
        metadata = extract_metadata_fields(game_metadata, record)

        if progress_callback:
            progress_callback(index + 1, len(library_records), title)

        yield ContentItem(
            id=catalog_item_id,
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
        count += 1

    logger.info(f"Imported {count} base games from Epic Games Store")
