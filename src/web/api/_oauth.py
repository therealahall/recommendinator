import logging
from typing import Any, assert_never

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth.epic import (
    EPIC_PLUGIN,
    EPIC_SOURCE_ID,
    EpicAuthError,
    get_epic_auth_url,
    has_epic_token,
    is_epic_enabled,
    save_epic_token,
)
from src.auth.epic import exchange_code_for_tokens as exchange_epic_tokens
from src.auth.epic import extract_code_from_input as extract_epic_code
from src.auth.gog import (
    GOG_PLUGIN,
    GOG_SOURCE_ID,
    GogAuthError,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from src.auth.gog import exchange_code_for_tokens as exchange_gog_tokens
from src.auth.gog import extract_code_from_input as extract_gog_code
from src.auth.oauth_sources import REFRESH_TOKEN_KEY, may_revoke
from src.auth.trakt import (
    TRAKT_PLUGIN,
    TRAKT_SOURCE_ID,
    DevicePollStatus,
    TraktAuthError,
    has_trakt_token,
    poll_device_token,
    resolve_trakt_client_credentials,
    save_trakt_token,
    start_device_auth_flow,
)
from src.sources.service import SOURCE_ID_PATTERN
from src.storage.manager import StorageManager
from src.utils.text import exception_for_log, sanitize_for_log
from src.web.guards import RequiredConfig, RequiredStorage

logger = logging.getLogger(__name__)

router = APIRouter()


class GogExchangeRequest(BaseModel):
    code_or_url: str = Field(
        ...,
        max_length=2000,
        description="Authorization code or full redirect URL from GOG",
    )


class EpicExchangeRequest(BaseModel):
    code_or_json: str = Field(
        ...,
        max_length=4000,
        description="Authorization code or JSON response from Epic Games",
    )


class TraktPollRequest(BaseModel):
    device_code: str = Field(
        ...,
        min_length=10,
        max_length=256,
        description="Device code returned by POST /trakt/start-device-flow",
    )


def _source_id_query(default: str) -> Any:
    """The id of the source being connected, which owns the stored token.
    Defaulted to the plugin's own name so a client written before the
    parameter existed still addresses the source it used to.
    """
    return Query(default, pattern=SOURCE_ID_PATTERN)


def _disconnect_source(
    source_id: str,
    plugin_name: str,
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int,
    detail: str,
) -> None:
    """An id this route may not act on gets the same refusal as one holding no
    token: telling them apart names sources the caller did not ask about.
    """
    if not may_revoke(plugin_name, source_id, config, storage, user_id):
        logger.info(
            "Disconnect refused for source_id=%s on plugin %s",
            sanitize_for_log(source_id),
            sanitize_for_log(plugin_name),
        )
        raise HTTPException(status_code=404, detail=detail)

    if not storage.credentials.delete(user_id, source_id, REFRESH_TOKEN_KEY):
        raise HTTPException(status_code=404, detail=detail)


@router.get("/gog/status")
def get_gog_status(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(GOG_SOURCE_ID),
) -> dict[str, Any]:
    enabled = is_gog_enabled(config, storage=storage, source_id=source_id)
    connected = has_gog_token(config, storage=storage, source_id=source_id)

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": get_gog_auth_url() if enabled else None,
    }


@router.post("/gog/exchange")
def exchange_gog_token(
    request: GogExchangeRequest,
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(GOG_SOURCE_ID),
) -> dict[str, Any]:
    if not is_gog_enabled(config, storage=storage, source_id=source_id):
        raise HTTPException(
            status_code=400,
            detail="GOG is not enabled for that source.",
        )

    try:
        code = extract_gog_code(request.code_or_url)
        tokens = exchange_gog_tokens(code)
        refresh_token = tokens["refresh_token"]
        save_gog_token(storage, refresh_token, source_id=source_id)
        logger.info("Connected GOG account for %s", sanitize_for_log(source_id))

        return {
            "success": True,
            "message": "GOG account connected successfully! You can now sync your GOG library.",
        }

    except GogAuthError as error:
        logger.warning("GOG auth error: %s", exception_for_log(error))
        raise HTTPException(
            status_code=400, detail="GOG authentication failed"
        ) from error
    except Exception as error:
        logger.error(
            "Unexpected error during GOG token exchange: %s", exception_for_log(error)
        )
        raise HTTPException(
            status_code=500, detail="Unexpected error during GOG authentication"
        ) from error


@router.delete("/gog/token")
def disconnect_gog(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(GOG_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    _disconnect_source(
        source_id,
        GOG_PLUGIN,
        config,
        storage,
        user_id,
        "No active GOG connection found",
    )
    logger.info(
        "Disconnected GOG account %s for user %s", sanitize_for_log(source_id), user_id
    )
    return {"success": True, "message": "GOG disconnected."}


@router.get("/epic/status")
def get_epic_status(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(EPIC_SOURCE_ID),
) -> dict[str, Any]:
    enabled = is_epic_enabled(config, storage=storage, source_id=source_id)
    connected = has_epic_token(config, storage=storage, source_id=source_id)

    auth_url: str | None = None
    if enabled:
        try:
            auth_url = get_epic_auth_url()
        except Exception as error:
            logger.warning(
                "Failed to generate Epic auth URL: %s", exception_for_log(error)
            )

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": auth_url,
    }


@router.post("/epic/exchange")
def exchange_epic_token(
    request: EpicExchangeRequest,
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(EPIC_SOURCE_ID),
) -> dict[str, Any]:
    if not is_epic_enabled(config, storage=storage, source_id=source_id):
        raise HTTPException(
            status_code=400,
            detail="Epic Games is not enabled in the current configuration.",
        )

    try:
        code = extract_epic_code(request.code_or_json)
        tokens = exchange_epic_tokens(code)
        refresh_token = tokens["refresh_token"]
        save_epic_token(storage, refresh_token, source_id=source_id)
        logger.info("Connected Epic Games account for %s", sanitize_for_log(source_id))

        return {
            "success": True,
            "message": "Epic Games account connected successfully! You can now sync your Epic library.",
        }

    except EpicAuthError as error:
        logger.warning("Epic Games auth error: %s", exception_for_log(error))
        raise HTTPException(
            status_code=400, detail="Epic Games authentication failed"
        ) from error
    except Exception as error:
        logger.error(
            "Unexpected error during Epic Games token exchange: %s",
            exception_for_log(error),
        )
        raise HTTPException(
            status_code=500,
            detail="Unexpected error during Epic Games authentication",
        ) from error


@router.delete("/epic/token")
def disconnect_epic(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(EPIC_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    _disconnect_source(
        source_id,
        EPIC_PLUGIN,
        config,
        storage,
        user_id,
        "No active Epic Games connection found",
    )
    logger.info(
        "Disconnected Epic Games account %s for user %s",
        sanitize_for_log(source_id),
        user_id,
    )
    return {"success": True, "message": "Epic Games disconnected."}


_TRAKT_POLL_MESSAGES: dict[DevicePollStatus, str] = {
    DevicePollStatus.PENDING: "Waiting for you to approve the request on Trakt.",
    DevicePollStatus.SLOW_DOWN: "Polling too quickly — slowing down.",
    DevicePollStatus.EXPIRED: "The authorization code expired. Start over.",
    DevicePollStatus.DENIED: "The authorization request was denied.",
}


@router.get("/trakt/status")
def get_trakt_status(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """``enabled`` means an enabled Trakt source with client credentials saved,
    so the device flow can run. ``connected`` means a refresh token is stored
    under an id this route owns.
    """
    try:
        resolve_trakt_client_credentials(
            config, storage, source_id=source_id, user_id=user_id
        )
        enabled = True
    except TraktAuthError:
        enabled = False

    # Ownership, not credential completeness: clearing the client secret would
    # otherwise read as disconnected while the token is still stored, and the
    # Data tab hangs its only revoke control off ``connected``.
    connected = has_trakt_token(config, storage, source_id, user_id)

    return {"enabled": enabled, "connected": connected}


@router.post("/trakt/start-device-flow")
def start_trakt_device_flow(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """The client_id and client_secret are never returned.

    Returning ``device_code`` to the web client is inherent to the OAuth
    device-code flow (the browser drives the polling loop), and is a conscious,
    reviewed decision for this localhost single-user deployment.
    """
    try:
        client_id, _ = resolve_trakt_client_credentials(
            config, storage, source_id=source_id, user_id=user_id
        )
        flow = start_device_auth_flow(client_id)
    except TraktAuthError as error:
        logger.warning("Trakt device-flow start failed: %s", exception_for_log(error))
        raise HTTPException(
            status_code=400, detail="Trakt authentication failed"
        ) from error

    return {
        "user_code": flow["user_code"],
        "verification_url": flow["verification_url"],
        "device_code": flow["device_code"],
        "expires_in": flow["expires_in"],
        "interval": flow["interval"],
    }


@router.post("/trakt/poll-device-approval")
def poll_trakt_device_approval(
    request: TraktPollRequest,
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """The frontend calls this repeatedly at the cadence Trakt returned."""
    try:
        client_id, client_secret = resolve_trakt_client_credentials(
            config, storage, source_id=source_id, user_id=user_id
        )
        result = poll_device_token(request.device_code, client_id, client_secret)
    except TraktAuthError as error:
        logger.warning(
            "Trakt device-approval poll failed: %s", exception_for_log(error)
        )
        raise HTTPException(
            status_code=400, detail="Trakt authentication failed"
        ) from error

    status = result.status
    match status:
        case DevicePollStatus.SUCCESS:
            if result.refresh_token is None:
                logger.error("Trakt poll reported success without a refresh token")
                raise HTTPException(
                    status_code=500, detail="Trakt authentication failed"
                )
            save_trakt_token(
                storage, result.refresh_token, source_id=source_id, user_id=user_id
            )
            logger.info(
                "Connected Trakt account %s for user %s",
                sanitize_for_log(source_id),
                user_id,
            )
            return {
                "connected": True,
                "message": (
                    "Trakt account connected successfully! "
                    "You can now sync your Trakt library."
                ),
            }
        case (
            DevicePollStatus.PENDING
            | DevicePollStatus.SLOW_DOWN
            | DevicePollStatus.EXPIRED
            | DevicePollStatus.DENIED
        ):
            return {
                "connected": False,
                "status": status.value,
                "message": _TRAKT_POLL_MESSAGES[status],
            }
        case _:  # pragma: no cover - exhaustiveness guard for new enum members
            assert_never(status)


@router.delete("/trakt/token")
def disconnect_trakt(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    _disconnect_source(
        source_id,
        TRAKT_PLUGIN,
        config,
        storage,
        user_id,
        "No active Trakt connection found",
    )
    logger.info(
        "Disconnected Trakt account %s for user %s",
        sanitize_for_log(source_id),
        user_id,
    )
    return {"success": True, "message": "Trakt disconnected."}
