from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import requests

from src.auth.oauth_sources import OAuthSourceBinding
from src.ingestion.sources.gog import GOG_CLIENT_ID, GOG_CLIENT_SECRET
from src.utils.text import exception_for_log

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
    pass


_GOG = OAuthSourceBinding(GOG_PLUGIN, "GOG", GogAuthError)


def get_gog_auth_url() -> str:
    params = (
        f"client_id={GOG_CLIENT_ID}"
        f"&redirect_uri={GOG_REDIRECT_URI}"
        "&response_type=code"
        "&layout=client2"
    )
    return f"{GOG_AUTH_URL}?{params}"


def extract_code_from_input(user_input: str) -> str:
    user_input = user_input.strip()

    if user_input.startswith("http"):
        parsed = urlparse(user_input)
        query_params = parse_qs(parsed.query)
        if "code" in query_params:
            return query_params["code"][0]
        raise GogAuthError(
            "URL does not contain a 'code' parameter. "
            "Make sure you copied the full redirect URL after logging in."
        )

    if len(user_input) < 20:
        raise GogAuthError(
            "Input appears too short to be a valid authorization code. "
            "Please copy the full code or URL."
        )

    return user_input


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
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
        logger.error("GOG token exchange request failed: %s", exception_for_log(error))
        raise GogAuthError("Failed to connect to GOG servers") from None


def save_gog_token(
    storage: StorageManager,
    refresh_token: str,
    source_id: str = GOG_SOURCE_ID,
    user_id: int = 1,
) -> None:
    _GOG.save_token(storage, refresh_token, source_id, user_id)


def is_gog_enabled(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = GOG_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    return _GOG.is_enabled(config, storage, source_id, user_id)


def has_gog_token(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = GOG_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    return _GOG.has_token(config, storage, source_id, user_id)
