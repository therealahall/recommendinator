from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import Path as PathParam  # this module's ``Path`` is pathlib's
from pydantic import BaseModel, Field

from src.models.content import ContentType
from src.storage.manager import (
    MAX_DECLINE_OTHERS,
    SUGGESTION_PAGE_DEFAULT,
    SUGGESTION_PAGE_MAX,
    DeclinedPair,
    MergeError,
    MergeEvidence,
    MergeRecord,
)
from src.utils.duplicate_serialization import (
    decline_refusal_message,
    declined_pair_to_dict,
    merge_to_dict,
    suggestion_page_to_dict,
)
from src.web.guards import RequiredStorage

router = APIRouter()

ItemDbId = Annotated[int, Field(ge=1)]
ItemIdPath = Annotated[int, PathParam(ge=1)]


class DuplicateSideResponse(BaseModel):
    db_id: int
    title: str
    source: str | None
    creator: str | None
    release_year: int | None
    also_offered: str


class DuplicateSuggestionResponse(BaseModel):
    content_type: str
    evidence: str
    evidence_label: str
    evidence_detail: str
    survivor_id: int
    copies: list[DuplicateSideResponse]


class DuplicateSuggestionPageResponse(BaseModel):
    total: int
    skipped_note: str
    suggestions: list[DuplicateSuggestionResponse]


class MergeResponse(BaseModel):
    id: int
    survivor_id: int
    survivor_title: str
    absorbed_id: int
    absorbed_title: str
    evidence: str
    evidence_label: str
    evidence_detail: str | None
    merged_at: str


class DeclinedPairResponse(BaseModel):
    one_id: int
    one_title: str
    other_id: int
    other_title: str


class MergeRequest(BaseModel):
    survivor_id: ItemDbId
    absorbed_id: ItemDbId


class DeclineDuplicateRequest(BaseModel):
    """*one_id* is the copy set apart, *other_ids* the copies it is not; storage
    stores one pair per refusal, lowest id first, either order round."""

    one_id: ItemDbId
    other_ids: Annotated[
        list[ItemDbId], Field(min_length=1, max_length=MAX_DECLINE_OTHERS)
    ]


def _merge_to_response(record: MergeRecord) -> MergeResponse:
    return MergeResponse.model_validate(merge_to_dict(record))


def _declined_to_response(pair: DeclinedPair) -> DeclinedPairResponse:
    return DeclinedPairResponse.model_validate(declined_pair_to_dict(pair))


def _duplicate_type(type_name: str | None) -> ContentType | None:
    if type_name is None:
        return None
    try:
        return ContentType.from_string(type_name)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
        ) from None


def _refused_merge(error: MergeError) -> HTTPException:
    """409, not 404: it names the row or merge to deal with first."""
    return HTTPException(status_code=409, detail=str(error))


@router.get("/duplicates", response_model=DuplicateSuggestionPageResponse)
def list_duplicates(
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
    type: str | None = Query(None, description="Content type filter"),
    limit: int = Query(
        SUGGESTION_PAGE_DEFAULT,
        ge=1,
        le=SUGGESTION_PAGE_MAX,
        description="Maximum works to offer",
    ),
) -> DuplicateSuggestionPageResponse:
    page = storage.list_duplicate_suggestions(
        user_id=user_id, content_type=_duplicate_type(type), limit=limit
    )
    return DuplicateSuggestionPageResponse.model_validate(suggestion_page_to_dict(page))


@router.get("/duplicates/declined", response_model=list[DeclinedPairResponse])
def list_declined_duplicates(
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[DeclinedPairResponse]:
    """List the pairs refused for good, lowest id first."""
    return [
        _declined_to_response(pair)
        for pair in storage.list_declined_duplicates(user_id=user_id)
    ]


@router.post("/duplicates/declined", response_model=list[DeclinedPairResponse])
def decline_duplicate_pair(
    request: DeclineDuplicateRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[DeclinedPairResponse]:
    pairs = storage.decline_duplicate_suggestion(
        request.one_id, request.other_ids, user_id=user_id
    )
    if not pairs:
        raise HTTPException(
            status_code=404,
            detail=decline_refusal_message(request.one_id, request.other_ids),
        )
    return [_declined_to_response(pair) for pair in pairs]


@router.delete(
    "/duplicates/declined/{one_id}/{other_id}", response_model=DeclinedPairResponse
)
def undecline_duplicate_pair(
    one_id: ItemIdPath,
    other_id: ItemIdPath,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> DeclinedPairResponse:
    try:
        pair = storage.undecline_duplicate_suggestion(one_id, other_id, user_id=user_id)
    except MergeError as error:
        raise _refused_merge(error) from error
    if pair is None:
        raise HTTPException(
            status_code=404,
            detail=f"Items {one_id} and {other_id} are not a declined pair.",
        )
    return _declined_to_response(pair)


@router.get("/merges", response_model=list[MergeResponse])
def list_merges(
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[MergeResponse]:
    """List the merges in force, newest first — the order they undo in."""
    return [
        _merge_to_response(record)
        for record in storage.list_content_item_merges(user_id=user_id)
    ]


@router.post("/merges", response_model=MergeResponse)
def merge_items(
    request: MergeRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> MergeResponse:
    try:
        record = storage.merge_content_items(
            request.survivor_id,
            request.absorbed_id,
            MergeEvidence.MANUAL,
            user_id=user_id,
        )
    except MergeError as error:
        raise _refused_merge(error) from error
    return _merge_to_response(record)


@router.delete("/merges/{merge_id}", response_model=MergeResponse)
def unmerge_items(
    merge_id: ItemIdPath,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> MergeResponse:
    try:
        record = storage.unmerge_content_items(merge_id, user_id=user_id)
    except MergeError as error:
        raise _refused_merge(error) from error
    if record is None:
        raise HTTPException(status_code=404, detail=f"Merge {merge_id} not found.")
    return _merge_to_response(record)
