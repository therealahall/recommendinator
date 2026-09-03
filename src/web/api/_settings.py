import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.settings.metadata import get_entry
from src.settings.service import (
    SettingsValidationError,
    apply_settings,
    build_settings_view,
    clear_secret,
    reset_setting,
    set_secret,
)
from src.utils.text import sanitize_for_log
from src.web.guards import (
    RequiredConfig,
    RequiredStorage,
    require_config,
    writable_config,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class SettingValidationView(BaseModel):
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    pattern: str | None = None


class SettingView(BaseModel):
    """``value``, ``db_overridden`` and ``has_stored_value`` are present only for
    non-sensitive settings; ``has_secret`` is present only for sensitive ones.
    The omitted fields are dropped via ``response_model_exclude_unset``.
    """

    key: str
    section: str
    label: str
    help: str
    type: str
    widget: str
    choices: list[str] | None
    validation: SettingValidationView | None
    advanced: bool
    restart_required: bool
    sensitive: bool
    value: Any = None
    db_overridden: bool | None = None
    has_stored_value: bool | None = None
    has_secret: bool | None = None


class SettingsSection(BaseModel):
    section: str
    settings: list[SettingView]


class SettingsResponse(BaseModel):
    sections: list[SettingsSection]


class SettingsUpdateRequest(BaseModel):
    updates: dict[str, Any]


class SettingSecretRequest(BaseModel):
    key: str
    value: str


# Global settings endpoints. Business logic lives in ``src.settings.service``
# (shared with the CLI ``settings`` group); the routes below adapt those
# framework-agnostic helpers to FastAPI / Pydantic.


@router.get(
    "/settings",
    response_model=SettingsResponse,
    response_model_exclude_unset=True,
)
def get_settings(config: RequiredConfig, storage: RequiredStorage) -> SettingsResponse:
    """Return every in-scope setting grouped by section (secrets masked)."""
    return SettingsResponse(**build_settings_view(config, storage))


@router.put(
    "/settings",
    response_model=SettingsResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require_config)],
)
def update_settings(
    request: SettingsUpdateRequest, storage: RequiredStorage
) -> SettingsResponse:
    """The config arrives through ``writable_config`` rather than as a
    ``RequiredConfig`` parameter because the live-apply is a read-copy-store of
    the running config and has to be serialised against the other writers of it.
    """
    try:
        with writable_config() as config:
            apply_settings(config, storage, request.updates)
            view = build_settings_view(config, storage)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"key": error.key, "reason": error.reason},
        ) from error
    return SettingsResponse(**view)


@router.delete(
    "/settings/{key}",
    response_model=SettingsResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require_config)],
)
def reset_setting_endpoint(key: str, storage: RequiredStorage) -> SettingsResponse:
    if get_entry(key) is None:
        logger.info("Settings reset miss for key=%s", sanitize_for_log(key))
        raise HTTPException(status_code=404, detail="Unknown setting.")
    try:
        with writable_config() as config:
            reset_setting(config, storage, key)
            view = build_settings_view(config, storage)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"key": error.key, "reason": error.reason},
        ) from error
    return SettingsResponse(**view)


@router.put("/settings/secret", status_code=204)
def set_setting_secret(
    request: SettingSecretRequest, storage: RequiredStorage
) -> Response:
    try:
        set_secret(storage, request.key, request.value)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=400, detail="Not a configurable secret."
        ) from error
    return Response(status_code=204)


@router.delete("/settings/secret/{key}", status_code=204)
def clear_setting_secret(key: str, storage: RequiredStorage) -> Response:
    try:
        clear_secret(storage, key)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=400, detail="Not a configurable secret."
        ) from error
    return Response(status_code=204)
