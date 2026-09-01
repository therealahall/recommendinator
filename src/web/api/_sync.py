import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.ingestion.sync import (
    ALL_SOURCES_KEY,
    ALL_SOURCES_LABEL,
    MAX_WORKERS_CEILING,
    already_syncing_detail,
    claim_sources,
    release_sources,
)
from src.sources.service import (
    ResolvedInput,
    build_runs_view,
    get_available_sync_sources,
    misconfigured_detail,
    redact_credentials,
    resolve_inputs,
    source_plugin_not_loaded,
    unusable_detail,
)
from src.utils.text import humanize_source_id, sanitize_for_log
from src.web.api._shared import PluginImportErrorResponse
from src.web.guards import RequiredConfig, RequiredStorage
from src.web.sync_dispatch import build_sync_job
from src.web.sync_manager import get_sync_manager

logger = logging.getLogger(__name__)

router = APIRouter()


class UpdateRequest(BaseModel):
    source: str = Field(
        ...,
        description=(
            "Source id, or 'all' for every enabled source. "
            "GET /api/sync/sources lists the ones this install has."
        ),
    )
    max_workers: int | None = Field(
        None,
        ge=1,
        le=MAX_WORKERS_CEILING,
        description=(
            "Override config['sync']['max_workers'] for this invocation. "
            "Mirrors the CLI's --workers flag."
        ),
    )


class PluginNotLoadedResponse(BaseModel):
    """``failures`` is the whole pass: none of them can be tied to ``plugin``."""

    plugin: str
    failures: list[PluginImportErrorResponse]


class SyncSourceResponse(BaseModel):
    """``sync_interval`` is resolved, so a client never has to know the plugin's
    default to render the cadence."""

    id: str
    display_name: str
    plugin_display_name: str
    enabled: bool
    plugin_not_loaded: PluginNotLoadedResponse | None = None
    sync_interval: str
    last_run_at: str | None
    last_run_status: str | None
    next_run_at: str | None


class SyncSourceProgressResponse(BaseModel):
    source: str
    items_processed: int
    total_items: int | None
    current_item: str | None
    progress_percent: int | None
    items_added: int
    items_updated: int
    items_unchanged: int
    #: Failures past the executor's cap, so a door showing fewer than it was
    #: sent can still name the run's true error total.
    omitted_errors: int


class SyncErrorResponse(BaseModel):
    source: str
    message: str


class SyncJobResponse(BaseModel):
    source: str
    status: str
    started_at: str | None
    completed_at: str | None
    items_processed: int
    total_items: int | None
    current_item: str | None
    current_source: str | None
    error_message: str | None
    progress_percent: int | None
    items_added: int
    items_updated: int
    items_unchanged: int
    errors: list[SyncErrorResponse] = []
    sources: list[SyncSourceProgressResponse] = []


class SyncStatusResponse(BaseModel):
    status: str
    jobs: list[SyncJobResponse] = []


class SyncRunResponse(BaseModel):
    source_id: str
    started_at: str
    finished_at: str
    status: str
    items_added: int
    items_updated: int
    items_unchanged: int
    total_items: int
    errors: list[str]
    omitted_errors: int


def _refusal(entry: ResolvedInput, errors: list[str]) -> str:
    """Name the settings, as the CLI does; log the plugin's own reason."""
    logger.warning(
        "Sync config validation failed for %s: %s",
        sanitize_for_log(entry.source_id),
        sanitize_for_log(
            redact_credentials("; ".join(errors), entry.plugin, entry.config)
        ),
    )
    return misconfigured_detail(entry.plugin, errors)


@router.post("/update")
def update_data(
    request: UpdateRequest, storage: RequiredStorage, config: RequiredConfig
) -> dict[str, Any]:
    """Different sources can sync concurrently; a duplicate of a running label,
    or anything overlapping the all-sources run, is rejected with 409.
    """
    sync_manager = get_sync_manager()
    source = request.source
    source_label = humanize_source_id(source) if source != "all" else ALL_SOURCES_LABEL
    job_key = ALL_SOURCES_KEY if source == "all" else source_label

    # Two POSTs racing on the same label both pass any pre-check here, so that
    # duplicate is left to ``start_sync``'s atomic check-and-set below.

    # ``resolve_inputs`` is the single source of truth: it merges YAML ``inputs`` with
    # DB-backed ``source_configs``, injects ``_source_id``, and layers decrypted
    # credentials — so it covers sources created via the Add-source modal that live
    # only in the database.
    misconfigured: list[str] = []
    if source == "all":
        # Overlapping whatever single run is already going would fetch and save
        # that source twice.
        if sync_manager.is_running():
            raise HTTPException(status_code=409, detail="A sync is already in progress")
        # Validating the entries resolved here, rather than resolving each id
        # again, keeps the run to one credential decrypt per source. Excluding a
        # failing source and naming it mirrors the CLI's all-sources run.
        resolved: list[ResolvedInput] = []
        for entry in resolve_inputs(config, storage=storage):
            validation_errors = entry.plugin.validate_config(
                entry.config, storage=storage
            )
            if validation_errors:
                misconfigured.append(
                    f"{humanize_source_id(entry.source_id)}: "
                    f"{_refusal(entry, validation_errors)}"
                )
                continue
            resolved.append(entry)
    else:
        if sync_manager.is_running(ALL_SOURCES_KEY):
            raise HTTPException(status_code=409, detail="A sync is already in progress")
        # Filtering the resolved list (not the YAML ``inputs`` map) is what lets
        # a DB-only source sync — and a disabled/unknown source yields no entry.
        resolved = [
            entry
            for entry in resolve_inputs(config, storage=storage)
            if entry.source_id == source
        ]
        if not resolved:
            # A 4xx (not a 200 "message") is required so the frontend ``catch``
            # clears the optimistic "syncing" flag; a 200 leaves the Sync button
            # stuck because no SyncJob is ever created to end the polling.
            logger.info(
                "Sync requested for unavailable source_id=%s", sanitize_for_log(source)
            )
            not_loaded = source_plugin_not_loaded(source, config, storage=storage)
            raise HTTPException(
                status_code=400,
                detail=(
                    unusable_detail(not_loaded)
                    if not_loaded is not None
                    else "Source is disabled or not configured."
                ),
            )
        # Validate the entry resolved above rather than resolving the id again:
        # a delete landing between the two makes the second lookup miss, and its
        # "unknown source" text carries the caller's own id onto the wire.
        source_entry = resolved[0]
        validation_errors = source_entry.plugin.validate_config(
            source_entry.config, storage=storage
        )
        if validation_errors:
            raise HTTPException(
                status_code=400, detail=_refusal(source_entry, validation_errors)
            )

    if not resolved:
        # A run where every source was excluded is not a run with nothing
        # configured, so the refusals are what the operator needs to read — and
        # they carry a 4xx for the reason the single-source branch above does.
        if misconfigured:
            raise HTTPException(status_code=400, detail=" ".join(misconfigured))
        return {"message": "No sources enabled or configured for sync", "count": 0}

    claimed, refused = claim_sources(storage, [entry.source_id for entry in resolved])
    if not claimed:
        raise HTTPException(status_code=409, detail=already_syncing_detail(refused))
    resolved = [entry for entry in resolved if entry.source_id in claimed]

    sources_to_sync = [entry.source_id for entry in resolved]

    # The same builder the scheduler dispatches through, so a requested run and
    # a scheduled one are the same job.
    dispatch = build_sync_job(
        sync_manager,
        job_key,
        resolved,
        list(claimed.values()),
        storage,
        config,
        max_workers=request.max_workers,
    )

    refusal = sync_manager.start_sync(
        job_key, dispatch.run, on_complete=dispatch.on_complete
    )

    if refusal is not None:
        release_sources(storage, claimed.values())
        raise HTTPException(status_code=409, detail="A sync is already in progress")

    # humanize_source_id title-cases but strips nothing, so the request's own
    # source id reaches here with its newlines intact.
    logger.info(
        "[SYNC] Started background sync for: %s", sanitize_for_log(source_label)
    )
    started = f"Sync started for {source_label}. Use GET /api/sync/status to monitor progress."
    # The CLI names a source it could not claim or could not validate and syncs
    # the rest; dropping those would read to the operator as "all of them synced".
    details = [*misconfigured]
    if refused:
        details.append(already_syncing_detail(refused))
    details.append(started)
    return {"message": " ".join(details), "sources": sources_to_sync}


@router.get("/sync/sources", response_model=list[SyncSourceResponse])
def get_sync_sources(
    config: RequiredConfig, storage: RequiredStorage
) -> list[SyncSourceResponse]:
    """Both components are guarded because the answer is assembled from both, and
    a missing half read as a wrong library rather than an outage.
    """
    sources = get_available_sync_sources(config, storage=storage)
    return [
        SyncSourceResponse(
            id=source.id,
            display_name=source.display_name,
            plugin_display_name=source.plugin_display_name,
            enabled=source.enabled,
            plugin_not_loaded=(
                PluginNotLoadedResponse(
                    plugin=source.plugin_not_loaded.plugin,
                    failures=[
                        PluginImportErrorResponse(
                            module=failure.module, reason=failure.reason
                        )
                        for failure in source.plugin_not_loaded.failures
                    ],
                )
                if source.plugin_not_loaded is not None
                else None
            ),
            sync_interval=source.sync_interval,
            last_run_at=source.last_run_at,
            last_run_status=source.last_run_status,
            next_run_at=source.next_run_at,
        )
        for source in sources
    ]


@router.get("/sync/runs", response_model=list[SyncRunResponse])
def get_sync_runs(
    storage: RequiredStorage,
    source_id: str | None = Query(None, description="Only this source's runs"),
    limit: int = Query(20, ge=1, le=100, description="Maximum runs to return"),
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[SyncRunResponse]:
    """Finished sync runs, newest first, for one source or every source."""
    runs = (
        storage.sync_runs.list_for_source(user_id, source_id, limit)
        if source_id is not None
        else storage.sync_runs.list_recent(user_id, limit)
    )
    return [SyncRunResponse(**view) for view in build_runs_view(runs)]


@router.get("/sync/status", response_model=SyncStatusResponse)
def get_sync_status() -> SyncStatusResponse:
    sync_manager = get_sync_manager()
    status_dict = sync_manager.get_status()

    return SyncStatusResponse(
        status=status_dict["status"],
        jobs=[SyncJobResponse(**job) for job in status_dict.get("jobs", [])],
    )
