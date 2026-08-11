"""Epic Games OAuth authentication service for web UI.

Handles the OAuth flow for connecting Epic Games accounts:
1. Generate auth URL via legendary's EPCAPI
2. Accept authorization code from user (raw or JSON format)
3. Exchange code for tokens via EPCAPI.start_session()
4. Save refresh token to encrypted DB storage
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from legendary.api.egs import EPCAPI
from legendary.models.exceptions import InvalidCredentialsError

from src.web.sync_sources import is_nonempty_secret_value, resolve_input_for_plugin

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

EPIC_PLUGIN = "epic_games"

# The id of the source a plain ``inputs.epic_games`` entry gets. Every entry
# point takes the real source id instead, so a second Epic source keeps its
# own token.
EPIC_SOURCE_ID = EPIC_PLUGIN


class EpicAuthError(Exception):
    """Exception raised for Epic Games authentication errors."""

    pass


def get_epic_auth_url() -> str:
    """Generate the Epic Games OAuth authorization URL.

    Uses legendary's built-in EPCAPI to generate the correct URL.

    Returns:
        URL for user to visit to authorize the app.
    """
    api = EPCAPI()
    url: str = api.get_auth_url()
    return url


def extract_code_from_input(user_input: str) -> str:
    """Extract authorization code from user input.

    User can paste either:
    - Just the authorization code
    - JSON response containing {"authorizationCode": "..."}

    Args:
        user_input: Code or JSON pasted by user.

    Returns:
        Extracted authorization code.

    Raises:
        EpicAuthError: If code cannot be extracted.
    """
    user_input = user_input.strip()

    # Try to parse as JSON (Epic's redirect returns JSON with authorizationCode)
    try:
        data = json.loads(user_input)
        if "authorizationCode" in data:
            code = data["authorizationCode"]
            if code and isinstance(code, str):
                extracted: str = code.strip()
                return extracted
        raise EpicAuthError(
            "JSON does not contain an 'authorizationCode' field. "
            "Please copy the full JSON response from Epic's redirect page."
        )
    except json.JSONDecodeError:
        # Not JSON — fall through to raw code path.
        # json.JSONDecodeError is a subclass of ValueError; catching only
        # JSONDecodeError avoids silently swallowing unrelated ValueErrors.
        pass

    # Assume it's the raw code
    if len(user_input) < 20:
        raise EpicAuthError(
            "Input appears too short to be a valid authorization code. "
            "Please copy the full code or JSON response."
        )

    return user_input


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Exchange authorization code for tokens via EPCAPI.

    Args:
        code: Authorization code from OAuth redirect.

    Returns:
        Session dict containing access_token, refresh_token, etc.

    Raises:
        EpicAuthError: If token exchange fails.
    """
    api = EPCAPI()
    try:
        session_data: dict[str, Any] = api.start_session(authorization_code=code)

        if "refresh_token" not in session_data:
            raise EpicAuthError("Response missing refresh_token")

        return session_data

    except InvalidCredentialsError as error:
        logger.error("Epic token exchange failed (InvalidCredentialsError)")
        raise EpicAuthError(
            "Token exchange failed. The authorization code may be expired or invalid. "
            "Please try again."
        ) from error
    except EpicAuthError:
        raise  # Don't let the broad Exception handler below swallow our own errors
    except Exception as error:
        logger.error("Epic token exchange request failed: %s", type(error).__name__)
        raise EpicAuthError("Failed to connect to Epic Games servers") from error


def save_epic_token(
    storage: StorageManager,
    refresh_token: str,
    source_id: str = EPIC_SOURCE_ID,
    user_id: int = 1,
) -> None:
    """Encrypt and store the refresh token under *source_id*.

    Raises:
        EpicAuthError: If saving fails.
    """
    try:
        storage.save_credential(user_id, source_id, "refresh_token", refresh_token)
        logger.info("Saved Epic Games refresh token to database")
    except Exception as error:
        logger.error(
            "Failed to save Epic Games token to database: %s", type(error).__name__
        )
        raise EpicAuthError("Failed to save Epic Games token") from error


def _resolve_epic_source(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = EPIC_SOURCE_ID,
    user_id: int = 1,
    *,
    require_enabled: bool = True,
) -> dict[str, Any] | None:
    """*source_id*'s sync-ready config, unless it is not an Epic source.

    Reads the database as well as ``inputs``, so a source added from the Data
    tab is found too.
    """
    resolved = resolve_input_for_plugin(
        source_id,
        EPIC_PLUGIN,
        config,
        storage,
        user_id,
        require_enabled=require_enabled,
    )
    return resolved.config if resolved is not None else None


def is_epic_enabled(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = EPIC_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    """Whether *source_id* is an enabled Epic Games source."""
    return _resolve_epic_source(config, storage, source_id, user_id) is not None


def has_epic_token(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = EPIC_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    """Whether an Epic source called *source_id* has a refresh token.

    Asks the resolved config, which layers the stored secret over the
    ``inputs`` entry as the sync does. A disabled source answers too: its
    token is there to be revoked.
    """
    resolved = _resolve_epic_source(
        config, storage, source_id, user_id, require_enabled=False
    )
    return resolved is not None and is_nonempty_secret_value(
        resolved.get("refresh_token")
    )
