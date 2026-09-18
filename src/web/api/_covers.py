from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from src.covers import cache
from src.covers.service import fill_cover
from src.web.guards import RequiredConfig, RequiredStorage

router = APIRouter()


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
