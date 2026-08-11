"""GOG OAuth authentication service for web UI.

Handles the OAuth flow for connecting GOG accounts:
1. Generate auth URL for user to visit
2. Exchange authorization code for tokens
3. Save refresh token to encrypted DB storage
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import requests

from src.ingestion.sources.gog import GOG_CLIENT_ID, GOG_CLIENT_SECRET
from src.utils.request_errors import scrub_request_error
from src.web.oauth_sources import OAuthSourceBinding

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

GOG_PLUGIN = "gog"

# The id of the source a plain ``inputs.gog`` entry gets. Every entry point
# takes the real source id instead, so a second GOG source keeps its own token.
GOG_SOURCE_ID = GOG_PLUGIN

GOG_AUTH_URL = "https://auth.gog.com/auth"
GOG_TOKEN_URL = "https://auth.gog.com/token"
GOG_REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"


class GogAuthError(Exception):
    """Exception raised for GOG authentication errors."""

    pass


_GOG = OAuthSourceBinding(GOG_PLUGIN, "GOG", GogAuthError)


def get_gog_auth_url() -> str:
    """Generate the GOG OAuth authorization URL.

    Returns:
        URL for user to visit to authorize the app.
    """
    params = (
        f"client_id={GOG_CLIENT_ID}"
        f"&redirect_uri={GOG_REDIRECT_URI}"
        "&response_type=code"
        "&layout=client2"
    )
    return f"{GOG_AUTH_URL}?{params}"


def extract_code_from_input(user_input: str) -> str:
    """Extract authorization code from user input.

    User can paste either:
    - Just the code
    - The full redirect URL containing the code

    Args:
        user_input: Code or URL pasted by user.

    Returns:
        Extracted authorization code.

    Raises:
        GogAuthError: If code cannot be extracted.
    """
    user_input = user_input.strip()

    # Check if it's a URL
    if user_input.startswith("http"):
        parsed = urlparse(user_input)
        query_params = parse_qs(parsed.query)
        if "code" in query_params:
            return query_params["code"][0]
        raise GogAuthError(
            "URL does not contain a 'code' parameter. "
            "Make sure you copied the full redirect URL after logging in."
        )

    # Assume it's the raw code
    if len(user_input) < 20:
        raise GogAuthError(
            "Input appears too short to be a valid authorization code. "
            "Please copy the full code or URL."
        )

    return user_input


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Exchange authorization code for access and refresh tokens.

    Args:
        code: Authorization code from OAuth redirect.

    Returns:
        Token response dict with access_token, refresh_token, etc.

    Raises:
        GogAuthError: If token exchange fails.
    """
    params = {
        "client_id": GOG_CLIENT_ID,
        "client_secret": GOG_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": GOG_REDIRECT_URI,
    }

    try:
        response = requests.get(GOG_TOKEN_URL, params=params, timeout=30)

        if not response.ok:
            logger.error(
                "GOG token exchange failed with status %d", response.status_code
            )
            raise GogAuthError(
                "Token exchange failed. Please try again or check your authorization code."
            )

        data: dict[str, Any] = response.json()

        if "refresh_token" not in data:
            raise GogAuthError("Response missing refresh_token")

        return data

    except requests.RequestException as error:
        # The authorization code and client secret are query parameters here, so
        # the URL inside ``error`` is a secret — it may reach neither the log nor
        # the ``__cause__`` chain the CLI renders with ``exc_info=True``.
        logger.error(
            "GOG token exchange request failed: %s", scrub_request_error(error)
        )
        raise GogAuthError("Failed to connect to GOG servers") from None


def save_gog_token(
    storage: StorageManager,
    refresh_token: str,
    source_id: str = GOG_SOURCE_ID,
    user_id: int = 1,
) -> None:
    """Encrypt and store the refresh token under *source_id*.

    Raises:
        GogAuthError: If saving fails.
    """
    _GOG.save_token(storage, refresh_token, source_id, user_id)


def is_gog_enabled(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = GOG_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    """Whether *source_id* is an enabled GOG source."""
    return _GOG.is_enabled(config, storage, source_id, user_id)


def has_gog_token(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = GOG_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    """Whether a GOG source called *source_id* has a refresh token."""
    return _GOG.has_token(config, storage, source_id, user_id)
