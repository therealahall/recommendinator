import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.models.content import ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.utils.text import exception_for_log
from src.web.api._shared import _get_recommendations_config
from src.web.guards import RequiredEngine
from src.web.state import get_config, get_storage

logger = logging.getLogger(__name__)

router = APIRouter()


class RelatedItemResponse(BaseModel):
    db_id: int | None = None
    title: str
    author: str | None = None
    content_type: str
    cover_url: str | None = None


class RecommendationResponse(BaseModel):
    db_id: int | None = None  # Database ID for actions like ignore
    title: str
    author: str | None
    content_type: str
    cover_url: str | None = None
    series: str | None = None
    series_index: float | None = None
    score: float
    reasoning: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    scorer_weights: dict[str, float] = Field(default_factory=dict)
    # 0.0 when the preference is off, or the item's genre was not just finished.
    variety_penalty: float = Field(0.0, ge=0.0, le=1.0)
    contributing_items: list[RelatedItemResponse] = Field(default_factory=list)
    adaptations: list[RelatedItemResponse] = Field(default_factory=list)


@router.get("/recommendations", response_model=list[RecommendationResponse])
def get_recommendations(
    engine: RequiredEngine,
    type: str | None = Query(
        None,
        description=(
            "Content type (book, movie, tv_show, video_game). "
            "Omit to recommend all four together."
        ),
    ),
    count: int | None = Query(
        None,
        ge=1,
        description=(
            "Number of recommendations. Omit for the "
            "recommendations.default_count setting."
        ),
    ),
    user_id: int = Query(1, ge=1, description="User ID for personalized preferences"),
) -> list[RecommendationResponse]:
    storage = get_storage()
    config = get_config()

    counts = _get_recommendations_config(config)
    if count is None:
        count = min(counts.default_count, counts.max_count)
    elif count > counts.max_count:
        raise HTTPException(
            status_code=400,
            detail="Requested count exceeds the maximum allowed",
        )

    content_type: ContentType | None = None
    if type is not None:
        try:
            content_type = ContentType.from_string(type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    try:
        user_preference_config: UserPreferenceConfig | None = None
        if storage:
            user_preference_config = storage.get_user_preference_config(user_id)

        recommendations = engine.generate_recommendations(
            content_type=content_type,
            count=count,
            user_preference_config=user_preference_config,
        )

        return [
            RecommendationResponse.model_validate(rec.to_payload())
            for rec in recommendations
        ]

    except Exception as error:
        # The engine walks the library, so its errors quote item titles.
        logger.error("Error generating recommendations: %s", exception_for_log(error))
        raise HTTPException(
            status_code=500, detail="Failed to generate recommendations"
        ) from error
