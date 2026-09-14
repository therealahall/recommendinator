import logging
from dataclasses import asdict
from typing import Annotated, Any, assert_never

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth.service import (
    NotConnectable,
    OAuthPlugin,
    connect_with_code,
    connection_status,
    disconnect,
    oauth_plugins,
    poll_device_flow,
    refusal_message,
    start_device_flow,
)
from src.ingestion.plugin_base import DevicePollStatus, OAuthError
from src.sources.service import SOURCE_ID_PATTERN
from src.utils.text import exception_for_log, sanitize_for_log
from src.web.guards import RequiredStorage

logger = logging.getLogger(__name__)

router = APIRouter()


def require_oauth_plugin(plugin_name: str) -> OAuthPlugin:
    declared = oauth_plugins().get(plugin_name)
    if declared is None:
        raise HTTPException(
            status_code=404, detail="No plugin by that name declares a connect flow."
        )
    return declared


DeclaredPlugin = Annotated[OAuthPlugin, Depends(require_oauth_plugin)]
SourceId = Annotated[
    str,
    Query(
        pattern=SOURCE_ID_PATTERN,
        description="The source being connected, which owns the token",
    ),
]
UserId = Annotated[int, Query(ge=1)]


class ExchangeRequest(BaseModel):
    code: str = Field(
        ...,
        max_length=4000,
        description="The code, redirect URL or JSON the service showed after sign-in",
    )


class DevicePollRequest(BaseModel):
    device_code: str = Field(
        ...,
        min_length=10,
        max_length=256,
        description="Device code returned by the start-device-flow route",
    )


def _refused(declared: OAuthPlugin, error: OAuthError) -> HTTPException:
    if not isinstance(error, NotConnectable):
        logger.warning(
            "%s auth error: %s",
            sanitize_for_log(declared.display_name),
            exception_for_log(error),
        )
    return HTTPException(status_code=400, detail=refusal_message(declared, error))


def _connected_message(declared: OAuthPlugin) -> str:
    return (
        f"{declared.display_name} account connected successfully! "
        f"You can now sync your {declared.display_name} library."
    )


@router.get("/oauth/{plugin_name}/status")
def get_oauth_status(
    storage: RequiredStorage,
    declared: DeclaredPlugin,
    source_id: SourceId,
    user_id: UserId = 1,
) -> dict[str, Any]:
    return connection_status(declared, source_id, storage, user_id)


@router.post("/oauth/{plugin_name}/exchange")
def exchange_oauth_code(
    storage: RequiredStorage,
    declared: DeclaredPlugin,
    request: ExchangeRequest,
    source_id: SourceId,
    user_id: UserId = 1,
) -> dict[str, Any]:
    try:
        connect_with_code(declared, source_id, request.code, storage, user_id)
    except OAuthError as error:
        raise _refused(declared, error) from error
    except Exception as error:
        logger.error(
            "Unexpected error during %s token exchange: %s",
            sanitize_for_log(declared.display_name),
            exception_for_log(error),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during {declared.display_name} authentication",
        ) from error

    logger.info(
        "Connected %s account for %s",
        sanitize_for_log(declared.display_name),
        sanitize_for_log(source_id),
    )
    return {"success": True, "message": _connected_message(declared)}


@router.post("/oauth/{plugin_name}/start-device-flow")
def start_oauth_device_flow(
    storage: RequiredStorage,
    declared: DeclaredPlugin,
    source_id: SourceId,
    user_id: UserId = 1,
) -> dict[str, Any]:
    """Returning ``device_code`` to the web client is inherent to the device
    flow (the browser drives the polling loop), and is a conscious, reviewed
    decision for this localhost single-user deployment.
    """
    try:
        authorization = start_device_flow(declared, source_id, storage, user_id)
    except OAuthError as error:
        raise _refused(declared, error) from error
    return asdict(authorization)


_POLL_MESSAGES: dict[DevicePollStatus, str] = {
    DevicePollStatus.PENDING: "Waiting for you to approve the request on {plugin}.",
    DevicePollStatus.SLOW_DOWN: "Polling too quickly — slowing down.",
    DevicePollStatus.EXPIRED: "The authorization code expired. Start over.",
    DevicePollStatus.DENIED: "The authorization request was denied.",
}


@router.post("/oauth/{plugin_name}/poll-device-approval")
def poll_oauth_device_approval(
    storage: RequiredStorage,
    declared: DeclaredPlugin,
    request: DevicePollRequest,
    source_id: SourceId,
    user_id: UserId = 1,
) -> dict[str, Any]:
    """The frontend calls this repeatedly at the cadence the service returned."""
    try:
        result = poll_device_flow(
            declared, source_id, request.device_code, storage, user_id
        )
    except OAuthError as error:
        raise _refused(declared, error) from error

    status = result.status
    match status:
        case DevicePollStatus.SUCCESS:
            logger.info(
                "Connected %s account %s for user %s",
                sanitize_for_log(declared.display_name),
                sanitize_for_log(source_id),
                user_id,
            )
            return {"connected": True, "message": _connected_message(declared)}
        case (
            DevicePollStatus.PENDING
            | DevicePollStatus.SLOW_DOWN
            | DevicePollStatus.EXPIRED
            | DevicePollStatus.DENIED
        ):
            return {
                "connected": False,
                "status": status.value,
                "message": _POLL_MESSAGES[status].format(plugin=declared.display_name),
            }
        case _:  # pragma: no cover - exhaustiveness guard for new enum members
            assert_never(status)


@router.delete("/oauth/{plugin_name}/token")
def disconnect_oauth(
    storage: RequiredStorage,
    declared: DeclaredPlugin,
    source_id: SourceId,
    user_id: UserId = 1,
) -> dict[str, Any]:
    if not disconnect(declared, source_id, storage, user_id):
        logger.info(
            "Disconnect refused for source_id=%s on plugin %s",
            sanitize_for_log(source_id),
            sanitize_for_log(declared.plugin.name),
        )
        raise HTTPException(
            status_code=404,
            detail=f"No active {declared.display_name} connection found",
        )
    logger.info(
        "Disconnected %s account %s for user %s",
        sanitize_for_log(declared.display_name),
        sanitize_for_log(source_id),
        user_id,
    )
    return {"success": True, "message": f"{declared.display_name} disconnected."}
