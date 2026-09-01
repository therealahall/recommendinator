from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.enrichment.manager import EnrichmentManager, job_status
from src.models.content import ContentType
from src.web.guards import RequiredConfig, RequiredStorage

router = APIRouter()


class EnrichmentStartRequest(BaseModel):
    content_type: str | None = Field(
        None, description="Content type filter (book, movie, tv_show, video_game)"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")
    retry_not_found: bool = Field(
        False, description="Re-process items previously marked as not_found"
    )


class EnrichmentResetRequest(BaseModel):
    provider: str | None = Field(
        None,
        description="Reset items enriched by this provider (tmdb, openlibrary, rawg)",
    )
    content_type: str | None = Field(
        None, description="Reset items of this content type"
    )
    item_id: int | None = Field(
        None, ge=1, description="Restore this one item to automatic enrichment"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")


class EnrichmentJobStatusResponse(BaseModel):
    running: bool = False
    completed: bool = False
    cancelled: bool = False
    items_processed: int = 0
    items_enriched: int = 0
    items_failed: int = 0
    items_not_found: int = 0
    total_items: int = 0
    current_item: str = ""
    content_type: str | None = None
    errors: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    progress_percent: float = 0.0


class EnrichmentStatsResponse(BaseModel):
    enabled: bool = False
    total: int = 0
    resettable: int = 0
    enriched: int = 0
    pending: int = 0
    not_found: int = 0
    failed: int = 0
    by_provider: dict[str, int] = Field(default_factory=dict)
    by_quality: dict[str, int] = Field(default_factory=dict)


@router.post("/enrichment/start")
def start_enrichment(
    request: EnrichmentStartRequest,
    storage: RequiredStorage,
    config: RequiredConfig,
) -> dict[str, Any]:
    enrichment_config = config.get("enrichment", {})
    if not enrichment_config.get("enabled", False):
        raise HTTPException(
            status_code=400,
            detail=(
                "Enrichment is disabled. Turn it on from the Data tab, or run: "
                "settings set enrichment.enabled true"
            ),
        )

    content_type = None
    if request.content_type:
        try:
            content_type = ContentType.from_string(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    # The claim inside is the mutual exclusion, and it holds against the CLI
    # too — which a check-then-build here never could.
    started = EnrichmentManager(storage, config).start_enrichment(
        content_type=content_type,
        user_id=request.user_id,
        include_not_found=request.retry_not_found,
    )
    if not started:
        raise HTTPException(status_code=409, detail="Enrichment job already running")

    type_desc = content_type.value if content_type else "all types"
    retry_msg = " (retrying not_found)" if request.retry_not_found else ""
    return {
        "message": f"Started enrichment for {type_desc}{retry_msg}",
        "status": "started",
    }


@router.post("/enrichment/stop")
def stop_enrichment(storage: RequiredStorage) -> dict[str, Any]:
    """Stop the current enrichment job, whichever process started it."""
    if not storage.enrichment_jobs.request_stop():
        raise HTTPException(status_code=400, detail="No enrichment job is running.")

    return {"message": "Enrichment job stop requested", "status": "stopping"}


@router.get("/enrichment/status", response_model=EnrichmentJobStatusResponse)
def get_enrichment_status(storage: RequiredStorage) -> EnrichmentJobStatusResponse:
    """The live enrichment job, whichever process started it."""
    status = job_status(storage)

    return EnrichmentJobStatusResponse(
        running=status.running,
        completed=status.completed,
        cancelled=status.cancelled,
        items_processed=status.items_processed,
        items_enriched=status.items_enriched,
        items_failed=status.items_failed,
        items_not_found=status.items_not_found,
        total_items=status.total_items,
        current_item=status.current_item,
        content_type=status.content_type,
        errors=status.errors,
        elapsed_seconds=status.elapsed_seconds,
        progress_percent=status.progress_percent,
    )


@router.get("/enrichment/stats", response_model=EnrichmentStatsResponse)
def get_enrichment_stats(
    config: RequiredConfig,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for filtering stats"),
) -> EnrichmentStatsResponse:
    enrichment_config = config.get("enrichment", {})
    enrichment_enabled = enrichment_config.get("enabled", False)

    stats = storage.enrichment.stats(user_id=user_id)

    return EnrichmentStatsResponse(
        enabled=enrichment_enabled,
        total=cast(int, stats.get("total", 0)),
        resettable=cast(int, stats.get("resettable", 0)),
        enriched=cast(int, stats.get("enriched", 0)),
        pending=cast(int, stats.get("pending", 0)),
        not_found=cast(int, stats.get("not_found", 0)),
        failed=cast(int, stats.get("failed", 0)),
        by_provider=cast(dict[str, int], stats.get("by_provider", {})),
        by_quality=cast(dict[str, int], stats.get("by_quality", {})),
    )


@router.post("/enrichment/reset")
def reset_enrichment(
    request: EnrichmentResetRequest,
    storage: RequiredStorage,
) -> dict[str, Any]:
    content_type = None
    if request.content_type:
        try:
            content_type = ContentType.from_string(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    if request.item_id is not None:
        if request.provider or request.content_type:
            raise HTTPException(
                status_code=400,
                detail="item_id cannot be combined with provider or content_type.",
            )
        if not storage.get_content_item(request.item_id, user_id=request.user_id):
            raise HTTPException(status_code=404, detail="Item not found")

    count = storage.enrichment.reset(
        provider=request.provider,
        content_type=content_type,
        user_id=request.user_id,
        content_item_id=request.item_id,
    )

    return {"message": f"Reset enrichment status for {count} item(s)", "count": count}
