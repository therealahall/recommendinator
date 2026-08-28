"""The user registers their OWN Trakt API application, so ``client_id`` and
``client_secret`` are saved to the source config/credential store before the
device flow runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from src.auth.oauth_sources import OAuthSourceBinding

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

TRAKT_PLUGIN = "trakt"

# The id of the source a plain ``inputs.trakt`` entry gets. Every entry point
# takes the real source id instead, so a second Trakt source keeps its own
# client credentials and token.
TRAKT_SOURCE_ID = TRAKT_PLUGIN
TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_DEVICE_CODE_URL = f"{TRAKT_API_URL}/oauth/device/code"
TRAKT_DEVICE_TOKEN_URL = f"{TRAKT_API_URL}/oauth/device/token"


class TraktAuthError(Exception):
    pass


_TRAKT = OAuthSourceBinding(TRAKT_PLUGIN, "Trakt", TraktAuthError)


class DevicePollStatus(str, Enum):
    SUCCESS = "success"
    PENDING = "pending"
    SLOW_DOWN = "slow_down"
    EXPIRED = "expired"
    DENIED = "denied"


@dataclass(frozen=True)
class DevicePollResult:
    """``refresh_token`` is only populated when ``status`` is ``SUCCESS``."""

    status: DevicePollStatus
    refresh_token: str | None = None


def start_device_auth_flow(client_id: str) -> dict[str, Any]:
    try:
        response = requests.post(
            TRAKT_DEVICE_CODE_URL,
            json={"client_id": client_id},
            timeout=10,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except requests.RequestException as error:
        logger.error("Trakt device-code request failed: %s", type(error).__name__)
        raise TraktAuthError("Failed to start Trakt device authorization") from error

    for field in ("device_code", "user_code", "verification_url"):
        if not data.get(field):
            raise TraktAuthError("Trakt device-code response was incomplete")

    if urlparse(data["verification_url"]).scheme not in ("http", "https"):
        raise TraktAuthError("Trakt returned an invalid verification URL")

    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_url": data["verification_url"],
        "expires_in": int(data.get("expires_in", 600)),
        "interval": int(data.get("interval", 5)),
    }


def poll_device_token(
    device_code: str, client_id: str, client_secret: str
) -> DevicePollResult:
    try:
        response = requests.post(
            TRAKT_DEVICE_TOKEN_URL,
            json={
                "code": device_code,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
    except requests.RequestException as error:
        logger.error("Trakt device-token poll failed: %s", type(error).__name__)
        raise TraktAuthError("Failed to reach Trakt during authorization") from error

    status = response.status_code
    if status == 200:
        data: dict[str, Any] = response.json()
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise TraktAuthError("Trakt token response missing refresh_token")
        return DevicePollResult(DevicePollStatus.SUCCESS, refresh_token)
    if status == 400:
        return DevicePollResult(DevicePollStatus.PENDING)
    if status == 429:
        return DevicePollResult(DevicePollStatus.SLOW_DOWN)
    if status == 410:
        return DevicePollResult(DevicePollStatus.EXPIRED)
    if status == 418:
        return DevicePollResult(DevicePollStatus.DENIED)
    if status == 404:
        raise TraktAuthError("Trakt rejected the device code (invalid or unknown)")
    if status == 409:
        raise TraktAuthError("This Trakt device code has already been used")
    raise TraktAuthError(f"Unexpected Trakt response while polling (status {status})")


def save_trakt_token(
    storage: StorageManager,
    refresh_token: str,
    source_id: str = TRAKT_SOURCE_ID,
    user_id: int = 1,
) -> None:
    _TRAKT.save_token(storage, refresh_token, source_id, user_id)


def resolve_trakt_client_credentials(
    config: dict[str, Any],
    storage: StorageManager | None,
    source_id: str = TRAKT_SOURCE_ID,
    user_id: int = 1,
) -> tuple[str, str]:
    """Refuses a source running another plugin: the token this unlocks is stored
    under the id.
    """
    trakt_config = _TRAKT.resolve(config, storage, source_id, user_id)

    if trakt_config is None:
        raise TraktAuthError(
            "Trakt is not configured. Add the Trakt source and save your "
            "Trakt API client id and secret first."
        )

    client_id = (trakt_config.get("client_id") or "").strip()
    client_secret = (trakt_config.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise TraktAuthError(
            "Trakt client id and secret are required. Save your Trakt API "
            "application credentials before connecting your account."
        )

    return client_id, client_secret


def has_trakt_token(
    config: dict[str, Any],
    storage: StorageManager | None = None,
    source_id: str = TRAKT_SOURCE_ID,
    user_id: int = 1,
) -> bool:
    return _TRAKT.has_token(config, storage, source_id, user_id)
