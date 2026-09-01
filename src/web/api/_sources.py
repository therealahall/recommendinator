import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.schedule import SYNC_INTERVAL_KEYS
from src.sources.service import (
    SourceConfigError,
    build_config_view,
    build_plugins_view,
    build_schema_view,
    clear_source_secret_value,
    create_source,
    delete_source,
    migrate_source,
    resolve_source_plugin,
    set_source_enabled_state,
    set_source_schedule,
    set_source_secret_value,
    source_plugin_not_loaded,
    unusable_detail,
    update_source_config_values,
)
from src.utils.text import sanitize_for_log
from src.web.api._shared import PluginImportErrorResponse, SourceFieldSchema
from src.web.guards import RequiredConfig, RequiredStorage

logger = logging.getLogger(__name__)

router = APIRouter()


class SyncIntervalOption(BaseModel):
    key: str
    label: str


class SourceSchemaResponse(BaseModel):
    source_id: str
    plugin: str
    plugin_display_name: str
    fields: list[SourceFieldSchema]
    sync_intervals: list[SyncIntervalOption]


class SourceConfigResponse(BaseModel):
    """Current config values for a source. Sensitive fields are never returned."""

    source_id: str
    plugin: str
    plugin_display_name: str
    enabled: bool
    migrated: bool
    migrated_at: str | None
    field_values: dict[str, Any]
    secret_status: dict[str, bool]
    sync_interval: str


class SourceConfigUpdateRequest(BaseModel):
    values: dict[str, Any]


class SourceSecretUpdateRequest(BaseModel):
    value: str


class SourceEnabledUpdateRequest(BaseModel):
    enabled: bool


class SourceScheduleUpdateRequest(BaseModel):
    interval: str


class SourceMigrationResponse(BaseModel):
    source_id: str
    migrated_at: str
    fields_migrated: list[str] = Field(
        description="Non-sensitive fields the source's database row now holds."
    )
    secrets_migrated: list[str] = Field(
        description=(
            "Sensitive fields the source now holds an encrypted credential "
            "for, whichever pass stored it — startup migrates a file-held "
            "secret before any request reaches this route."
        )
    )


class PluginInfoResponse(BaseModel):
    name: str
    display_name: str
    description: str
    content_types: list[str]
    requires_api_key: bool
    requires_network: bool
    fields: list[SourceFieldSchema]


class PluginListResponse(BaseModel):
    plugins: list[PluginInfoResponse]
    import_errors: list[PluginImportErrorResponse]


class SourceCreateRequest(BaseModel):
    id: str = Field(..., max_length=64)
    plugin: str = Field(..., max_length=128)
    values: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


@router.get("/plugins", response_model=PluginListResponse)
def list_plugins() -> PluginListResponse:
    return PluginListResponse(**build_plugins_view())


@router.post(
    "/sync/sources",
    response_model=SourceConfigResponse,
    status_code=201,
)
def create_source_endpoint(
    payload: SourceCreateRequest,
    storage: RequiredStorage,
    config: RequiredConfig,
) -> SourceConfigResponse:
    """Sensitive fields must be set via ``PUT /secret/{key}`` *after* this
    call returns; the create path rejects them in the body to keep the
    sensitive-write surface narrow.
    """
    try:
        view = create_source(
            payload.id,
            payload.plugin,
            payload.values,
            storage,
            enabled=payload.enabled,
            config=config,
        )
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**view)


@router.delete("/sync/sources/{source_id}", status_code=204)
def delete_source_endpoint(
    source_id: str, storage: RequiredStorage, config: RequiredConfig
) -> Response:
    """Config is guarded because clearing a credential stranded under the plugin
    name reads both halves of the source list: unread, a YAML source still on
    that plugin is indistinguishable from none.
    """
    try:
        delete_source(source_id, storage, config=config)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


# Per-source configuration endpoints. Business logic lives in
# ``src.sources.service``; the endpoints below adapt those helpers to
# FastAPI / Pydantic so the CLI ``source`` group can share them.


_ERROR_KIND_TO_STATUS: dict[str, int] = {
    "not_found": 404,
    "not_migrated": 404,
    "invalid_field": 400,
    "not_sensitive": 400,
    "sensitive_in_config": 400,
    "conflict": 409,
    "invalid_id": 400,
    "unknown_plugin": 400,
}

# Fixed user-facing strings keyed by error kind so HTTP responses never
# echo back caller-controlled identifiers (path params would otherwise
# end up in JSON `detail` fields).
_ERROR_KIND_TO_DETAIL: dict[str, str] = {
    "not_found": "Field or source not found.",
    "not_migrated": "Source has not been migrated to the database.",
    "invalid_field": "Request references an unknown field.",
    "not_sensitive": "Field is not sensitive — use the config endpoint instead.",
    "sensitive_in_config": "Sensitive fields must be set via the secret endpoint.",
    "conflict": "A source with that id already exists.",
    "invalid_id": (
        "Source id must start with a lowercase letter and contain only "
        "lowercase letters, digits, underscores, and hyphens."
    ),
    "unknown_plugin": "The requested plugin is not registered.",
}


def require_plugin(
    source_id: str, storage: RequiredStorage, config: RequiredConfig
) -> SourcePlugin:
    """Resolve a source id to its plugin, or 404 if no source carries that id.

    Both halves are guarded before the lookup because either one missing runs
    the resolution off the other alone.
    """
    plugin = resolve_source_plugin(source_id, config, storage)
    if plugin is None:
        # A source whose plugin died stays in the listing, so answering "not
        # found" here contradicts what the user is looking at. Same words as
        # the sync refusal, per the disclosure carve-out in docs/SECURITY.md.
        not_loaded = source_plugin_not_loaded(source_id, config, storage)
        if not_loaded is not None:
            raise HTTPException(status_code=400, detail=unusable_detail(not_loaded))
        # Server-side log carries the identifier; the wire response stays generic.
        logger.info("Source lookup miss for source_id=%s", sanitize_for_log(source_id))
        raise HTTPException(status_code=404, detail="Source not found.")
    return plugin


ResolvedPlugin = Annotated[SourcePlugin, Depends(require_plugin)]


# Deliberately absent from the maps above: the source service builds these two
# messages from schema field names and the containment guard alone — never from
# a plugin's own words or caller input.
_KINDS_ANSWERED_WITH_THEIR_MESSAGE = {"invalid_values", "credential_move"}


def _config_error_to_http(error: SourceConfigError) -> HTTPException:
    # error.message embeds caller-supplied values, so only the kind is logged.
    logger.info("Source config error kind=%s", sanitize_for_log(error.kind))
    if error.kind in _KINDS_ANSWERED_WITH_THEIR_MESSAGE:
        return HTTPException(status_code=400, detail=error.message)
    return HTTPException(
        status_code=_ERROR_KIND_TO_STATUS.get(error.kind, 400),
        detail=_ERROR_KIND_TO_DETAIL.get(error.kind, "Invalid request."),
    )


@router.get("/sync/sources/{source_id}/schema", response_model=SourceSchemaResponse)
def get_source_schema(source_id: str, plugin: ResolvedPlugin) -> SourceSchemaResponse:
    return SourceSchemaResponse(**build_schema_view(source_id, plugin))


@router.get("/sync/sources/{source_id}/config", response_model=SourceConfigResponse)
def get_source_config_endpoint(
    source_id: str,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    """Return current config values for a source. Sensitive fields are stripped."""
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


@router.post(
    "/sync/sources/{source_id}/migrate", response_model=SourceMigrationResponse
)
def migrate_source_to_db(
    source_id: str,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceMigrationResponse:
    """Copy a YAML source entry into the database (idempotent)."""
    return SourceMigrationResponse(**migrate_source(source_id, plugin, config, storage))


@router.put("/sync/sources/{source_id}/config", response_model=SourceConfigResponse)
def update_source_config_endpoint(
    source_id: str,
    payload: SourceConfigUpdateRequest,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    try:
        update_source_config_values(source_id, plugin, storage, payload.values)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


@router.put("/sync/sources/{source_id}/secret/{key}", status_code=204)
def set_source_secret_endpoint(
    source_id: str,
    key: str,
    payload: SourceSecretUpdateRequest,
    plugin: ResolvedPlugin,
    storage: RequiredStorage,
) -> Response:
    try:
        set_source_secret_value(source_id, plugin, storage, key, payload.value)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


@router.delete("/sync/sources/{source_id}/secret/{key}", status_code=204)
def clear_source_secret_endpoint(
    source_id: str,
    key: str,
    plugin: ResolvedPlugin,
    storage: RequiredStorage,
) -> Response:
    try:
        clear_source_secret_value(source_id, plugin, storage, key)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


@router.put("/sync/sources/{source_id}/enabled", response_model=SourceConfigResponse)
def set_source_enabled_endpoint(
    source_id: str,
    payload: SourceEnabledUpdateRequest,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    try:
        set_source_enabled_state(source_id, storage, payload.enabled)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


@router.put("/sync/sources/{source_id}/schedule", response_model=SourceConfigResponse)
def set_source_schedule_endpoint(
    source_id: str,
    payload: SourceScheduleUpdateRequest,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    if payload.interval not in SYNC_INTERVAL_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Interval must be one of: {', '.join(SYNC_INTERVAL_KEYS)}.",
        )
    try:
        set_source_schedule(source_id, storage, payload.interval)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))
