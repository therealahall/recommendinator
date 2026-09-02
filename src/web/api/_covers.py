from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.covers import cache
from src.covers.service import fill_cover, start_backfill
from src.web.guards import RequiredConfig, RequiredStorage

router = APIRouter()


class CoverBackfillResponse(BaseModel):
    running: bool = False
    completed: bool = False
    cancelled: bool = False
    total_items: int = 0
    items_processed: int = 0
    items_cached: int = 0
    items_cleared: int = 0
    items_failed: int = 0
    current_item: str = ""
    errors: list[str] = Field(default_factory=list)


@router.post("/covers/backfill", response_model=CoverBackfillResponse)
def start_cover_backfill(
    storage: RequiredStorage,
    config: RequiredConfig,
    user_id: int = Query(1, ge=1, description="User ID whose library to walk"),
) -> CoverBackfillResponse:
    started = start_backfill(storage, config, user_id=user_id)
    if started is None:
        raise HTTPException(
            status_code=409, detail="A cover backfill is already running."
        )
    return CoverBackfillResponse(**started.payload())


@router.post("/covers/backfill/stop")
def stop_cover_backfill(storage: RequiredStorage) -> dict[str, str]:
    """Stop the running cover backfill, whichever process started it."""
    if not storage.cover_jobs.request_stop():
        raise HTTPException(status_code=400, detail="No cover backfill is running.")

    return {"message": "Cover backfill stop requested", "status": "stopping"}


@router.get("/covers/backfill/status", response_model=CoverBackfillResponse)
def get_cover_backfill_status(storage: RequiredStorage) -> CoverBackfillResponse:
    """The live backfill, whichever process started it."""
    return CoverBackfillResponse(**storage.cover_jobs.read().payload())


@router.get("/covers/{item_id}")
def get_cover(
    item_id: int,
    storage: RequiredStorage,
    config: RequiredConfig,
    user_id: int = Query(1, ge=1, description="User ID owning the item"),
) -> FileResponse:
    """An item id, never a URL: this route must not become an open proxy."""
    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    outcome = fill_cover(storage, config, item, user_id=user_id)
    if not isinstance(outcome, Path):
        raise HTTPException(status_code=404, detail=outcome.reason)

    with outcome.open("rb") as handle:
        media_type = cache.image_media_type(handle.read(cache.SNIFF_BYTES))
    if media_type is None:
        raise HTTPException(status_code=404, detail="the cached cover is unreadable")
    return FileResponse(outcome, media_type=media_type)
