"""REST API endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.ingestion.sources.goodreads import GoodreadsPlugin
from src.ingestion.sources.steam import SteamAPIError, parse_steam_games
from src.models.content import ConsumptionStatus, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.web.state import get_config, get_embedding_gen, get_engine, get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# Request/Response models
class CompletionRequest(BaseModel):
    """Request model for marking content as completed."""

    content_type: str = Field(
        ..., description="Content type (book, movie, tv_show, video_game)"
    )
    title: str = Field(..., description="Title of the content")
    author: str | None = Field(None, description="Author (for books)")
    rating: int | None = Field(None, ge=1, le=5, description="Rating (1-5)")
    review: str | None = Field(None, description="Review text")


class UpdateRequest(BaseModel):
    """Request model for updating data."""

    source: str = Field(..., description="Data source (goodreads, steam, all)")


class RecommendationResponse(BaseModel):
    """Response model for recommendations."""

    title: str
    author: str | None
    score: float
    similarity_score: float
    preference_score: float
    reasoning: str
    llm_reasoning: str | None = None


class StatusResponse(BaseModel):
    """Response model for system status."""

    status: str
    version: str
    components: dict[str, bool]


class UserPreferenceResponse(BaseModel):
    """Response model for user preferences."""

    scorer_weights: dict[str, float]
    series_in_order: bool
    variety_after_completion: bool
    minimum_book_pages: int | None
    maximum_movie_runtime: int | None
    custom_rules: list[str]


class UserPreferenceUpdateRequest(BaseModel):
    """Request model for updating user preferences (partial merge)."""

    scorer_weights: dict[str, float] | None = None
    series_in_order: bool | None = None
    variety_after_completion: bool | None = None
    minimum_book_pages: int | None = None
    maximum_movie_runtime: int | None = None
    custom_rules: list[str] | None = None


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
    count: int = Query(5, ge=1, le=20, description="Number of recommendations"),
    use_llm: bool = Query(True, description="Use LLM for enhanced reasoning"),
    user_id: int = Query(1, ge=1, description="User ID for personalized preferences"),
) -> list[RecommendationResponse]:
    """Get personalized recommendations.

    Args:
        type: Content type
        count: Number of recommendations
        use_llm: Whether to use LLM enhancement
        user_id: User ID for loading per-user preferences

    Returns:
        List of recommendations
    """
    engine = get_engine()
    storage = get_storage()
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    # Map string to ContentType enum
    type_map = {
        "book": ContentType.BOOK,
        "movie": ContentType.MOVIE,
        "tv_show": ContentType.TV_SHOW,
        "video_game": ContentType.VIDEO_GAME,
    }

    if type.lower() not in type_map:
        raise HTTPException(status_code=400, detail=f"Invalid content type: {type}")

    content_type = type_map[type.lower()]

    try:
        # Load user preferences if storage is available
        user_preference_config: UserPreferenceConfig | None = None
        if storage:
            user_preference_config = storage.get_user_preference_config(user_id)

        recommendations = engine.generate_recommendations(
            content_type=content_type,
            count=count,
            use_llm=use_llm,
            user_preference_config=user_preference_config,
        )

        if not recommendations:
            return []

        # Format response
        response = []
        for rec in recommendations:
            item = rec["item"]
            response.append(
                RecommendationResponse(
                    title=item.title,
                    author=item.author,
                    score=rec["score"],
                    similarity_score=rec["similarity_score"],
                    preference_score=rec["preference_score"],
                    reasoning=rec["reasoning"],
                    llm_reasoning=rec.get("llm_reasoning"),
                )
            )

        return response

    except Exception as e:
        logger.error(f"Error generating recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
async def get_user_preferences(user_id: int) -> UserPreferenceResponse:
    """Get user preference configuration.

    Args:
        user_id: User ID.

    Returns:
        User preference configuration.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    preference_config = storage.get_user_preference_config(user_id)
    return UserPreferenceResponse(**preference_config.to_dict())


@router.put("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
async def update_user_preferences(
    user_id: int, request: UserPreferenceUpdateRequest
) -> UserPreferenceResponse:
    """Update user preference configuration (partial merge).

    Only fields present in the request body are updated; omitted fields
    retain their current values.

    Args:
        user_id: User ID.
        request: Partial preference update.

    Returns:
        Updated user preference configuration.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    # Load existing config
    existing = storage.get_user_preference_config(user_id)

    # Merge only provided fields
    if request.scorer_weights is not None:
        existing.scorer_weights.update(request.scorer_weights)
    if request.series_in_order is not None:
        existing.series_in_order = request.series_in_order
    if request.variety_after_completion is not None:
        existing.variety_after_completion = request.variety_after_completion
    if request.minimum_book_pages is not None:
        existing.minimum_book_pages = request.minimum_book_pages
    if request.maximum_movie_runtime is not None:
        existing.maximum_movie_runtime = request.maximum_movie_runtime
    if request.custom_rules is not None:
        existing.custom_rules = request.custom_rules

    storage.save_user_preference_config(user_id, existing)

    return UserPreferenceResponse(**existing.to_dict())


@router.post("/complete")
async def mark_complete(request: CompletionRequest) -> dict[str, Any]:
    """Mark content as completed.

    Args:
        request: Completion request

    Returns:
        Success message
    """
    from src.models.content import ContentItem

    storage = get_storage()
    embedding_gen = get_embedding_gen()

    if not storage or not embedding_gen:
        raise HTTPException(status_code=500, detail="Components not initialized")

    # Map string to ContentType enum
    type_map = {
        "book": ContentType.BOOK,
        "movie": ContentType.MOVIE,
        "tv_show": ContentType.TV_SHOW,
        "video_game": ContentType.VIDEO_GAME,
    }

    if request.content_type.lower() not in type_map:
        raise HTTPException(
            status_code=400, detail=f"Invalid content type: {request.content_type}"
        )

    content_type = type_map[request.content_type.lower()]

    # Validate rating
    if request.rating is not None and (request.rating < 1 or request.rating > 5):
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    # Create content item
    item = ContentItem(
        id=None,
        title=request.title,
        author=request.author if content_type == ContentType.BOOK else None,
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        rating=request.rating,
        review=request.review,
    )

    try:
        # Generate embedding and save
        embedding = embedding_gen.generate_content_embedding(item)
        db_id = storage.save_content_item(item, embedding)

        return {"message": f"Marked '{request.title}' as completed", "id": db_id}

    except Exception as e:
        logger.error(f"Error marking content as completed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/update")
async def update_data(request: UpdateRequest) -> dict[str, Any]:
    """Update data from input files.

    Args:
        request: Update request

    Returns:
        Success message with count
    """
    storage = get_storage()
    embedding_gen = get_embedding_gen()
    config = get_config()

    if not storage or not embedding_gen or not config:
        raise HTTPException(status_code=500, detail="Components not initialized")

    try:
        count = 0

        if request.source == "goodreads" or request.source == "all":
            inputs_config = config.get("inputs", {})
            goodreads_config = inputs_config.get("goodreads", {})

            if not goodreads_config.get("enabled", False):
                if request.source == "goodreads":
                    return {"message": "Goodreads source is disabled", "count": 0}
            else:
                goodreads_path = goodreads_config.get(
                    "path", "inputs/goodreads_library_export.csv"
                )
                goodreads_plugin = GoodreadsPlugin()
                plugin_config = {"csv_path": str(goodreads_path)}
                validation_errors = goodreads_plugin.validate_config(plugin_config)

                if validation_errors:
                    raise HTTPException(
                        status_code=400, detail="; ".join(validation_errors)
                    )

                for item in goodreads_plugin.fetch(plugin_config):
                    try:
                        embedding = embedding_gen.generate_content_embedding(item)
                        storage.save_content_item(item, embedding)
                        count += 1
                    except Exception as e:
                        logger.warning(f"Failed to process {item.title}: {e}")

        if request.source == "steam" or request.source == "all":
            inputs_config = config.get("inputs", {})
            steam_config = inputs_config.get("steam", {})

            if not steam_config.get("enabled", False):
                if request.source == "steam":
                    return {"message": "Steam source is disabled", "count": 0}
            else:
                api_key = steam_config.get("api_key", "").strip()
                if not api_key:
                    raise HTTPException(
                        status_code=400,
                        detail="Steam API key is required. Get one from https://steamcommunity.com/dev/apikey",
                    )

                steam_id = steam_config.get("steam_id", "").strip()
                vanity_url = steam_config.get("vanity_url", "").strip()
                min_playtime = steam_config.get("min_playtime_minutes", 0)

                if not steam_id and not vanity_url:
                    raise HTTPException(
                        status_code=400,
                        detail="Either steam_id or vanity_url must be provided in config",
                    )

                try:
                    for item in parse_steam_games(
                        api_key=api_key,
                        steam_id=steam_id if steam_id else None,
                        vanity_url=vanity_url if vanity_url else None,
                        min_playtime_minutes=min_playtime,
                    ):
                        try:
                            embedding = embedding_gen.generate_content_embedding(item)
                            storage.save_content_item(item, embedding)
                            count += 1
                        except Exception as e:
                            logger.warning(f"Failed to process {item.title}: {e}")
                except SteamAPIError as e:
                    raise HTTPException(
                        status_code=500, detail=f"Steam API error: {e}"
                    ) from e
                except Exception as e:
                    logger.error(f"Error processing Steam data: {e}")
                    raise HTTPException(status_code=500, detail=str(e)) from e

        return {"message": f"Updated {count} items", "count": count}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating data: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get system status.

    Returns:
        System status information
    """
    engine = get_engine()
    storage = get_storage()
    embedding_gen = get_embedding_gen()

    components = {
        "engine": engine is not None,
        "storage": storage is not None,
        "embedding_generator": embedding_gen is not None,
    }

    all_ready = all(components.values())

    return StatusResponse(
        status="ready" if all_ready else "initializing",
        version="1.0.0",
        components=components,
    )
