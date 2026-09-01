from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.recommendations.profile import ProfileGenerator, profile_payload
from src.web.guards import RequiredStorage

router = APIRouter()


class ProfileResponse(BaseModel):
    user_id: int
    genre_affinities: dict[str, float]
    theme_preferences: list[str]
    anti_preferences: list[str]
    cross_media_patterns: list[str]
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
    profile = ProfileGenerator(storage).regenerate_and_save(user_id)

    return ProfileResponse(
        user_id=profile.user_id,
        genre_affinities=profile.genre_affinities,
        theme_preferences=profile.theme_preferences,
        anti_preferences=profile.anti_preferences,
        cross_media_patterns=profile.cross_media_patterns,
        generated_at=profile.generated_at,
    )
