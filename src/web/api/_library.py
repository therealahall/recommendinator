import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_RELEASE_YEAR,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    MAX_TITLE_LENGTH,
    MIN_RELEASE_YEAR,
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
    ExternalId,
    get_enum_value,
)
from src.storage.manager import (
    UNSET,
    VALID_SORT_OPTIONS,
    UncorrectableFieldError,
    Unset,
)
from src.utils.export import export_items_csv, export_items_json
from src.utils.item_serialization import (
    completion_to_dict,
    ignore_result_to_dict,
    item_to_dict,
)
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.utils.text import exception_for_log, is_blank
from src.web.guards import RequiredStorage
from src.web.responses import SurrogateSafeResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _blank_rejector(field: str) -> Callable[[str], str]:
    """The lower bound refuses ``""``; spaces are the same claim, unsayable in
    a schema."""

    def reject(value: str) -> str:
        if is_blank(value):
            raise ValueError(f"{field} cannot be blank")
        return value

    return reject


#: Blank is not a review: stored, it stops a later import filling the field.
CompletionReviewText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_REVIEW_LENGTH),
    AfterValidator(_blank_rejector("review")),
]

#: Blank is not a title either: it is the item's only name in the library.
CompletionTitle = Annotated[
    str,
    Field(min_length=1, max_length=MAX_TITLE_LENGTH),
    AfterValidator(_blank_rejector("title")),
]

#: Stripped, so a pasted trailing space is not another name to the veto. Its
#: bounds are ``edit_item``'s to refuse: a constraint answers an unreadable 422.
CorrectedCreator = Annotated[str, StringConstraints(strip_whitespace=True)]


class CompletionRequest(BaseModel):
    content_type: str = Field(
        ..., description="Content type (book, movie, tv_show, video_game)"
    )
    title: CompletionTitle = Field(..., description="Title of the content")
    author: str | None = Field(
        None,
        max_length=MAX_CREATOR_LENGTH,
        description="Creator: author, director, creator or developer",
    )
    rating: int | None = Field(None, ge=1, le=5, description="Rating (1-5)")
    review: CompletionReviewText | None = Field(None, description="Review text")


class CompletionResponse(BaseModel):
    message: str
    id: int


class ContentItemResponse(BaseModel):
    # Which source contributed which id, one entry per source that named it.
    external_ids: list[ExternalId] = Field(default_factory=list)
    db_id: int | None = None  # Database ID for actions like ignore
    title: str
    author: str | None
    content_type: str
    status: str
    rating: int | None
    review: str | None
    source: str | None
    date_completed: str | None = None
    ignored: bool = False
    seasons_watched: list[int] | None = None
    total_seasons: int | None = None
    release_year: int | None = None
    series: str | None = None
    series_index: float | None = None
    enriched: bool = False
    manually_enriched: bool = False
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    description: str | None = None


class IgnoreItemRequest(BaseModel):
    ignored: bool = Field(..., description="Whether to ignore the item")


class IgnoreItemResponse(BaseModel):
    db_id: int
    title: str
    ignored: bool
    message: str


class ItemEditRequest(BaseModel):
    """Every field distinguishes omitted from supplied: an absent one leaves the
    stored value alone, and a null clears ``rating`` or ``review``. A null
    ``status`` is refused instead.
    """

    status: str | None = Field(None, description="Status value")
    rating: int | None = Field(None, ge=1, le=5)
    review: str | None = None
    seasons_watched: list[Annotated[int, Field(ge=1, le=MAX_SEASONS)]] | None = Field(
        None, max_length=MAX_SEASONS
    )
    genres: list[Annotated[str, Field(max_length=MAX_GENRE_TAG_LENGTH)]] | None = Field(
        None, max_length=MAX_GENRES, description="Manual genres (overwrite)"
    )
    tags: list[Annotated[str, Field(max_length=MAX_GENRE_TAG_LENGTH)]] | None = Field(
        None, max_length=MAX_TAGS, description="Manual tags (overwrite)"
    )
    description: str | None = Field(
        None, max_length=MAX_DESCRIPTION_LENGTH, description="Manual description"
    )
    release_year: int | str | None = Field(None, description="Corrected year")
    creator: CorrectedCreator | None = None

    @property
    def corrected_year(self) -> int | None:
        """``None`` for text no ``int`` takes, which ``edit_item`` refuses."""
        if self.release_year is None:
            return None
        text = str(self.release_year).strip()
        # Python refuses ``int`` on a decimal string over 4300 digits, and that
        # ValueError would escape as a 500 rather than the refusal sentence.
        if len(text) > len(str(MAX_RELEASE_YEAR)) or not text.isdecimal():
            return None
        return int(text)


def _item_to_response(item: "ContentItem") -> ContentItemResponse:
    return ContentItemResponse.model_validate(item_to_dict(item))


@router.get("/items", response_model=list[ContentItemResponse])
def list_items(
    storage: RequiredStorage,
    type: str | None = Query(None, description="Content type filter"),
    status: str | None = Query(None, description="Status filter"),
    user_id: int = Query(1, ge=1, description="User ID"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results per page"),
    offset: int = Query(
        0, ge=0, description="Number of items to skip (for pagination)"
    ),
    sort_by: str = Query(
        "title",
        description="Sort order: title (ignores articles), updated_at, rating, created_at",
    ),
    include_ignored: bool = Query(
        False,
        description="Whether to include ignored items (default: hide ignored)",
    ),
    enrichment: EnrichmentFilter | None = Query(
        None,
        description="Filter by enrichment state: enriched or not_enriched",
    ),
    search: str | None = Query(
        None,
        max_length=MAX_SEARCH_LENGTH,
        description="Search term for title/creator/series",
    ),
    needs_rating: bool = Query(
        False,
        description="Only return completed items that have no rating yet",
    ),
) -> list[ContentItemResponse]:
    content_type = None
    if type is not None:
        try:
            content_type = ContentType.from_string(type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    consumption_status = None
    if status is not None:
        try:
            consumption_status = ConsumptionStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Valid options: unread, currently_consuming, completed",
            ) from None

    if sort_by.lower() not in VALID_SORT_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail="Invalid sort_by. Valid options: created_at, rating, title, updated_at",
        )

    # needs_rating means "completed AND unrated": completed status is implied
    # and takes precedence over any explicitly-passed status param.
    if needs_rating:
        consumption_status = ConsumptionStatus.COMPLETED

    items = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        status=consumption_status,
        unrated_only=needs_rating,
        limit=limit,
        offset=offset,
        sort_by=sort_by.lower(),
        include_ignored=include_ignored,
        enrichment=enrichment,
        search=search,
    )

    return [_item_to_response(item) for item in items]


@router.get("/items/export")
def export_items(
    storage: RequiredStorage,
    type: str | None = Query(
        None, description="Content type (book, movie, tv_show, video_game)"
    ),
    format: str = Query("csv", description="Export format: csv or json"),
    user_id: int = Query(1, ge=1, description="User ID"),
) -> Response:
    content_type: ContentType | None = None
    if type is not None:
        try:
            content_type = ContentType.from_string(type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    export_format = format.lower()
    if export_format not in {"csv", "json"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid format. Valid options: csv, json",
        )

    items = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        include_ignored=True,
    )

    stem = "library" if content_type is None else f"{get_enum_value(content_type)}s"
    filename = f"{stem}.{export_format}"

    if export_format == "csv":
        content = export_items_csv(items, content_type)
        media_type = "text/csv"
    else:
        content = export_items_json(items, content_type)
        media_type = "application/json"

    # The app's response class only covers a body FastAPI renders, and this
    # one arrives serialised, so it names the same encode itself.
    return SurrogateSafeResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/items/{db_id}/ignore", response_model=IgnoreItemResponse)
def set_item_ignored(
    db_id: int,
    request: IgnoreItemRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> IgnoreItemResponse:
    """Ignored items are excluded from recommendations."""
    item = storage.get_content_item(db_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    success = storage.set_item_ignored(db_id, request.ignored, user_id=user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update item")

    return IgnoreItemResponse.model_validate(
        ignore_result_to_dict(db_id, item.title, request.ignored)
    )


@router.get("/items/{db_id}", response_model=ContentItemResponse)
def get_single_item(
    db_id: int,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> ContentItemResponse:
    item = storage.get_content_item(db_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return _item_to_response(item)


def _edit_bound_crossed(request: ItemEditRequest) -> str | None:
    """The first bound an edit crosses, worded as the CLI words its own."""
    if request.review is not None:
        if not request.review.strip():
            return "Review cannot be blank. Send null to clear it."
        if len(request.review) > MAX_REVIEW_LENGTH:
            return f"Review must be at most {MAX_REVIEW_LENGTH} characters."
    if request.creator is not None:
        if not request.creator:
            return "Creator cannot be empty."
        if len(request.creator) > MAX_CREATOR_LENGTH:
            return f"Creator must be at most {MAX_CREATOR_LENGTH} characters."
    if request.release_year is not None:
        year = request.corrected_year
        if year is None or not MIN_RELEASE_YEAR <= year <= MAX_RELEASE_YEAR:
            return (
                "Release year must be a number between "
                f"{MIN_RELEASE_YEAR} and {MAX_RELEASE_YEAR}."
            )
    return None


@router.patch("/items/{db_id}", response_model=ContentItemResponse)
def edit_item(
    db_id: int,
    request: ItemEditRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> ContentItemResponse:
    supplied = request.model_fields_set
    status: str | Unset = UNSET
    if "status" in supplied:
        if request.status not in {"unread", "currently_consuming", "completed"}:
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Valid options: completed, currently_consuming, unread",
            )
        status = request.status

    crossed = _edit_bound_crossed(request)
    if crossed is not None:
        raise HTTPException(status_code=400, detail=crossed)

    try:
        success = storage.update_item_from_ui(
            db_id=db_id,
            status=status,
            rating=request.rating if "rating" in supplied else UNSET,
            review=request.review if "review" in supplied else UNSET,
            seasons_watched=request.seasons_watched,
            genres=request.genres,
            tags=request.tags,
            description=request.description,
            release_year=request.corrected_year,
            creator=request.creator,
            user_id=user_id,
        )
    except UncorrectableFieldError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")

    updated_item = storage.get_content_item(db_id, user_id=user_id)
    if not updated_item:
        raise HTTPException(status_code=404, detail="Item not found after update")

    return _item_to_response(updated_item)


@router.post("/complete", response_model=CompletionResponse)
def mark_complete(
    request: CompletionRequest, storage: RequiredStorage
) -> CompletionResponse:
    try:
        content_type = ContentType.from_string(request.content_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
        ) from None

    item = ContentItem(
        id=None,
        title=request.title,
        author=request.author,
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        rating=request.rating,
        review=request.review,
    )

    try:
        db_id = storage.complete_content_item(item)
    except Exception as error:
        # The failing write is this request's title, author and review.
        logger.error("Error marking content as completed: %s", exception_for_log(error))
        raise HTTPException(
            status_code=500, detail="Failed to mark content as completed"
        ) from error

    return CompletionResponse.model_validate(completion_to_dict(request.title, db_id))
