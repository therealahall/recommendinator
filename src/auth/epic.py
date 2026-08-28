from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from legendary.api.egs import EPCAPI
from legendary.models.exceptions import InvalidCredentialsError

from src.auth.oauth_sources import OAuthSourceBinding

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

EPIC_PLUGIN = "epic_games"

# The id of the source a plain ``inputs.epic_games`` entry gets. Every entry
# point takes the real source id instead, so a second Epic source keeps its
# own token.
EPIC_SOURCE_ID = EPIC_PLUGIN


class EpicAuthError(Exception):
    pass


_EPIC = OAuthSourceBinding(EPIC_PLUGIN, "Epic Games", EpicAuthError)


def get_epic_auth_url() -> str:
    api = EPCAPI()
    url: str = api.get_auth_url()
    return url


def extract_code_from_input(user_input: str) -> str:
    user_input = user_input.strip()

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
        # json.JSONDecodeError is a subclass of ValueError; catching only
        # JSONDecodeError avoids silently swallowing unrelated ValueErrors.
        pass

    if len(user_input) < 20:
        raise EpicAuthError(
            "Input appears too short to be a valid authorization code. "
            "Please copy the full code or JSON response."
        )

    return user_input


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
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
    _EPIC.save_token(storage, refresh_token, source_id, user_id)


def is_epic_enabled(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = EPIC_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    return _EPIC.is_enabled(config, storage, source_id, user_id)


def has_epic_token(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = EPIC_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    return _EPIC.has_token(config, storage, source_id, user_id)
