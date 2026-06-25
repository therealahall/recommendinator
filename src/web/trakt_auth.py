"""Trakt OAuth device-code authentication service for web UI and CLI.

Handles the device-code OAuth flow for connecting a Trakt account:

1. ``start_device_auth_flow`` requests a device code + user code from Trakt.
2. ``poll_device_token`` performs a single poll of the device-token endpoint,
   returning either the issued tokens or a typed pending/slow-down/expired/
   denied result. Callers (the web endpoint and the CLI loop) decide how to
   repeat the poll rather than blocking inside one HTTP handler.
3. ``save_trakt_token`` persists the issued ``refresh_token`` to the encrypted
   credential store under source_id ``"trakt"``.

The user registers their OWN Trakt API application, so ``client_id`` and
``client_secret`` are saved to the source config/credential store before the
device flow runs. ``resolve_trakt_client_credentials`` reads those back from
storage so neither the web client nor the CLI ever has to supply secrets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests

from src.web.sync_sources import resolve_inputs

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

TRAKT_SOURCE_ID = "trakt"
TRAKT_API_URL = "https://api.trakt.tv"
TRAKT_DEVICE_CODE_URL = f"{TRAKT_API_URL}/oauth/device/code"
TRAKT_DEVICE_TOKEN_URL = f"{TRAKT_API_URL}/oauth/device/token"


class TraktAuthError(Exception):
    """Exception raised for Trakt device-code authentication errors."""


class DevicePollStatus(str, Enum):
    """Outcome of a single device-token poll attempt.

    Mirrors Trakt's documented device-token status codes:

    - ``SUCCESS``  (200) — tokens issued.
    - ``PENDING``  (400) — user has not yet approved; keep polling.
    - ``SLOW_DOWN`` (429) — polling too fast; back off then keep polling.
    - ``EXPIRED``  (410) — the device code expired; restart the flow.
    - ``DENIED``   (418) — the user denied the request.
    """

    SUCCESS = "success"
    PENDING = "pending"
    SLOW_DOWN = "slow_down"
    EXPIRED = "expired"
    DENIED = "denied"


@dataclass(frozen=True)
class DevicePollResult:
    """Result of a single device-token poll.

    ``refresh_token`` is only populated when ``status`` is ``SUCCESS``.
    """

    status: DevicePollStatus
    refresh_token: str | None = None


def start_device_auth_flow(client_id: str) -> dict[str, Any]:
    """Request a device code and user code from Trakt.

    Args:
        client_id: The user's Trakt API application client id.

    Returns:
        Dict with ``device_code``, ``user_code``, ``verification_url``,
        ``expires_in`` and ``interval`` (poll cadence in seconds).

    Raises:
        TraktAuthError: If the request fails or the response is malformed.
    """
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
    """Poll the Trakt device-token endpoint exactly once.

    Args:
        device_code: The device code from ``start_device_auth_flow``.
        client_id: The user's Trakt API application client id.
        client_secret: The user's Trakt API application secret.

    Returns:
        A ``DevicePollResult`` describing this attempt. On ``SUCCESS`` the
        result carries the issued ``refresh_token``.

    Raises:
        TraktAuthError: If the device code is invalid, already used, or the
            success response omits a refresh token.
    """
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
    storage: StorageManager, refresh_token: str, user_id: int = 1
) -> None:
    """Save a Trakt refresh token to encrypted database storage.

    Args:
        storage: StorageManager instance.
        refresh_token: Trakt OAuth refresh token to save.
        user_id: User ID to associate the token with.

    Raises:
        TraktAuthError: If saving fails.
    """
    try:
        storage.save_credential(
            user_id, TRAKT_SOURCE_ID, "refresh_token", refresh_token
        )
        logger.info("Saved Trakt refresh token to database")
    except Exception as error:
        logger.error("Failed to save Trakt token to database: %s", type(error).__name__)
        raise TraktAuthError("Failed to save Trakt token") from error


def resolve_trakt_client_credentials(
    config: dict[str, Any],
    storage: StorageManager | None,
    user_id: int = 1,
) -> tuple[str, str]:
    """Resolve the saved Trakt ``client_id`` and ``client_secret``.

    Reuses ``resolve_inputs`` so resolution matches what the plugin sees at
    sync time: ``client_id`` from the source config (YAML or DB) and the
    sensitive ``client_secret`` merged from the encrypted credential store.

    Args:
        config: Full application config.
        storage: StorageManager for DB config/credential lookup.
        user_id: User ID for credential lookup.

    Returns:
        ``(client_id, client_secret)``.

    Raises:
        TraktAuthError: If the Trakt source is not configured or either
            credential is missing.
    """
    trakt_config: dict[str, Any] | None = None
    for resolved in resolve_inputs(config, storage=storage, user_id=user_id):
        if resolved.source_id == TRAKT_SOURCE_ID:
            trakt_config = resolved.config
            break

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


def is_trakt_connected(storage: StorageManager | None, user_id: int = 1) -> bool:
    """Check whether a Trakt refresh token exists in the credential store.

    Args:
        storage: StorageManager for credential lookup.
        user_id: User ID for credential lookup.

    Returns:
        True if a non-empty refresh_token is stored for Trakt.
    """
    if storage is None:
        return False
    token = storage.get_credential(user_id, TRAKT_SOURCE_ID, "refresh_token")
    return bool(token and token.strip())
