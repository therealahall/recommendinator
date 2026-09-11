from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.recommendations.profile import profile_payload, regenerated_payload
from src.web.guards import RequiredStorage

router = APIRouter()


class GenreAffinityResponse(BaseModel):
    genre: str
    score: float | None = None
    anti: bool


class AuthorAffinityResponse(BaseModel):
    author: str
    score: float


class ProfileResponse(BaseModel):
    user_id: int
    genre_affinities: list[GenreAffinityResponse]
    author_affinities: list[AuthorAffinityResponse]
    theme_preferences: list[str]
    cross_media_patterns: list[str]
    has_content: bool
    generated_at: datetime | None = None


@router.get("/profile")
def get_profile(
    storage: RequiredStorage, user_id: int = Query(default=1, ge=1)
) -> ProfileResponse:
    return ProfileResponse.model_validate(
        profile_payload(user_id, storage.profiles.get(user_id))
    )


@router.post("/profile/regenerate")
def regenerate_profile(
    storage: RequiredStorage, user_id: int = Query(default=1, ge=1)
) -> ProfileResponse:
    return ProfileResponse.model_validate(regenerated_payload(storage, user_id))
