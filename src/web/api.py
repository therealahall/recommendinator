"""REST API endpoints."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.ingestion.sources.goodreads import parse_goodreads_csv
from src.ingestion.sources.steam import SteamAPIError, parse_steam_games
from src.models.content import ConsumptionStatus, ContentType
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


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
    count: int = Query(5, ge=1, le=20, description="Number of recommendations"),
    use_llm: bool = Query(True, description="Use LLM for enhanced reasoning"),
):
    """Get personalized recommendations.

    Args:
        type: Content type
        count: Number of recommendations
        use_llm: Whether to use LLM enhancement

    Returns:
        List of recommendations
    """
    engine = get_engine()
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
        recommendations = engine.generate_recommendations(
            content_type=content_type, count=count, use_llm=use_llm
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


@router.post("/complete")
async def mark_complete(request: CompletionRequest):
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
async def update_data(request: UpdateRequest):
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
                goodreads_path = Path(
                    goodreads_config.get("path", "inputs/goodreads_library_export.csv")
                )

                if not goodreads_path.exists():
                    raise HTTPException(
                        status_code=404, detail=f"File not found: {goodreads_path}"
                    )

                for item in parse_goodreads_csv(goodreads_path):
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
                    raise HTTPException(status_code=500, detail=f"Steam API error: {e}") from e
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
async def get_status():
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
