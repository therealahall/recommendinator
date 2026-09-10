from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.enrichment.manager import (
    EnrichmentManager,
    EnrichmentStart,
    PinRefused,
    job_status,
)
from src.enrichment.provider_base import pins_of
from src.models.content import ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.item_serialization import (
    ENRICHMENT_UNAVAILABLE,
    enrichment_candidates_to_dict,
    enrichment_pin_to_dict,
    enrichment_reset_to_dict,
)
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.web.guards import RequiredConfig, RequiredStorage

router = APIRouter()


def _item_or_404(storage: StorageManager, db_id: int, user_id: int) -> ContentItem:
    item = storage.get_content_item(db_id, user_id=user_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {db_id} not found")
    return item


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
        None, ge=1, description="Re-queue this one item, whatever left it settled"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")


class EnrichmentPinRequest(BaseModel):
    item_id: int = Field(..., ge=1, description="Library item to pin")
    provider: str = Field(..., description="Provider whose record is being pinned")
    record_id: str | None = Field(
        None, description="Provider's record id; null returns the item to searching"
    )
    user_id: int = Field(1, ge=1, description="User ID for authorization")


class EnrichmentCandidateResponse(BaseModel):
    provider: str
    record_id: str
    title: str
    year: int | None = None
    creator: str | None = None
    cover_url: str | None = None


class EnrichmentCandidatesResponse(BaseModel):
    item_id: int
    candidates: list[EnrichmentCandidateResponse] = Field(default_factory=list)
    pinned: dict[str, str] = Field(default_factory=dict)


class EnrichmentPinResponse(BaseModel):
    item_id: int
    pinned: dict[str, str] = Field(default_factory=dict)
    message: str
    run: str | None = None


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
    if started is EnrichmentStart.UNAVAILABLE:
        raise HTTPException(status_code=400, detail=ENRICHMENT_UNAVAILABLE)
    if started is EnrichmentStart.ALREADY_RUNNING:
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


@router.get("/enrichment/candidates", response_model=EnrichmentCandidatesResponse)
def get_enrichment_candidates(
    storage: RequiredStorage,
    config: RequiredConfig,
    item_id: int = Query(..., ge=1, description="Library item to search for"),
    query: str | None = Query(
        None,
        max_length=MAX_SEARCH_LENGTH,
        description="Title to search under, in place of the item's own",
    ),
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> EnrichmentCandidatesResponse:
    """Every enabled provider's own search results for one item."""
    item = _item_or_404(storage, item_id, user_id)
    manager = EnrichmentManager(storage, config)
    payload = enrichment_candidates_to_dict(
        item_id, manager.candidates(item, query), pins_of(item.metadata)
    )
    return EnrichmentCandidatesResponse.model_validate(payload)


@router.post("/enrichment/pin", response_model=EnrichmentPinResponse)
def pin_enrichment_record(
    request: EnrichmentPinRequest,
    storage: RequiredStorage,
    config: RequiredConfig,
) -> EnrichmentPinResponse:
    """Bind one item to one provider record, or hand it back to title search."""
    item = _item_or_404(storage, request.item_id, request.user_id)
    try:
        # The run this starts is fire-and-forget: it publishes to the job record
        # the status endpoint already serves.
        pinned, started = EnrichmentManager(storage, config).pin(
            request.item_id,
            item,
            request.provider,
            request.record_id,
            user_id=request.user_id,
        )
    except PinRefused as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    payload = enrichment_pin_to_dict(
        request.item_id, request.provider, request.record_id, pinned, started
    )
    return EnrichmentPinResponse.model_validate(payload)


@router.post("/enrichment/reset")
def reset_enrichment(
    request: EnrichmentResetRequest,
    storage: RequiredStorage,
    config: RequiredConfig,
) -> dict[str, Any]:
    """Re-queue items the next run would otherwise skip: everything a provider
    settled, everything of one content type, or the one item that failed, which
    asks for a run of its own.
    """
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
        _item_or_404(storage, request.item_id, request.user_id)

    count = storage.enrichment.reset(
        provider=request.provider,
        content_type=content_type,
        user_id=request.user_id,
        content_item_id=request.item_id,
    )

    started = None
    if request.item_id is not None:
        started = EnrichmentManager(storage, config).start_enrichment(
            user_id=request.user_id, content_item_id=request.item_id
        )
    return enrichment_reset_to_dict(count, started)
