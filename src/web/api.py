"""REST API endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.ingestion.plugin_base import SourceError
from src.models.content import ConsumptionStatus, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.web.state import get_config, get_embedding_gen, get_engine, get_storage
from src.web.sync_manager import SyncJob, get_sync_manager
from src.web.sync_sources import (
    get_available_sync_sources,
    get_sync_handler,
    transform_source_config,
    validate_source_config,
)

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

    source: str = Field(
        ...,
        description="Data source (e.g. goodreads, steam, sonarr, radarr, or 'all')",
    )


class SyncSourceResponse(BaseModel):
    """Response model for a sync source."""

    id: str
    display_name: str
    description: str


class UserResponse(BaseModel):
    """Response model for user listing."""

    id: int
    username: str
    display_name: str | None


class ContentItemResponse(BaseModel):
    """Response model for content item listing."""

    id: str | None
    title: str
    author: str | None
    content_type: str
    status: str
    rating: int | None
    review: str | None
    source: str | None


class RecommendationResponse(BaseModel):
    """Response model for recommendations."""

    title: str
    author: str | None
    score: float
    similarity_score: float
    preference_score: float
    reasoning: str
    llm_reasoning: str | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class FeaturesStatus(BaseModel):
    """Feature flags status."""

    ai_enabled: bool = False
    embeddings_enabled: bool = False
    llm_reasoning_enabled: bool = False


class StatusResponse(BaseModel):
    """Response model for system status."""

    status: str
    version: str
    components: dict[str, bool]
    features: FeaturesStatus = Field(default_factory=FeaturesStatus)


class UserPreferenceResponse(BaseModel):
    """Response model for user preferences."""

    scorer_weights: dict[str, float]
    series_in_order: bool
    variety_after_completion: bool
    custom_rules: list[str]
    content_length_preferences: dict[str, str] = Field(default_factory=dict)


class SyncJobResponse(BaseModel):
    """Response model for sync job status."""

    source: str
    status: str
    started_at: str | None
    completed_at: str | None
    items_processed: int
    total_items: int | None
    current_item: str | None
    error_message: str | None
    progress_percent: int | None
    error_count: int


class SyncStatusResponse(BaseModel):
    """Response model for overall sync status."""

    status: str
    job: SyncJobResponse | None = None


class UserPreferenceUpdateRequest(BaseModel):
    """Request model for updating user preferences (partial merge)."""

    scorer_weights: dict[str, float] | None = None
    series_in_order: bool | None = None
    variety_after_completion: bool | None = None
    custom_rules: list[str] | None = None
    content_length_preferences: dict[str, str] | None = None


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
    count: int = Query(5, ge=1, description="Number of recommendations"),
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
    config = get_config()
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    # Validate count against max_count from config
    rec_config = config.get("recommendations", {}) if config else {}
    max_count = rec_config.get("max_count", 20)
    if count > max_count:
        raise HTTPException(
            status_code=400,
            detail=f"Count exceeds maximum allowed ({max_count})",
        )

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
                    score_breakdown=rec.get("score_breakdown", {}),
                )
            )

        return response

    except Exception as e:
        logger.error(f"Error generating recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/users", response_model=list[UserResponse])
async def list_users() -> list[UserResponse]:
    """List all users.

    Returns:
        List of users.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    users = storage.get_all_users()
    return [
        UserResponse(
            id=user["id"],
            username=user["username"],
            display_name=user.get("display_name"),
        )
        for user in users
    ]


@router.get("/items", response_model=list[ContentItemResponse])
async def list_items(
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
) -> list[ContentItemResponse]:
    """List content items with optional filters.

    Args:
        type: Optional content type filter.
        status: Optional consumption status filter.
        user_id: User ID to filter by.
        limit: Maximum number of results per page.
        offset: Number of items to skip (for pagination).
        sort_by: Sort order (default: title, which ignores leading articles).

    Returns:
        List of content items.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    type_map = {
        "book": ContentType.BOOK,
        "movie": ContentType.MOVIE,
        "tv_show": ContentType.TV_SHOW,
        "video_game": ContentType.VIDEO_GAME,
    }

    status_map = {
        "unread": ConsumptionStatus.UNREAD,
        "currently_consuming": ConsumptionStatus.CURRENTLY_CONSUMING,
        "completed": ConsumptionStatus.COMPLETED,
    }

    content_type = None
    if type is not None:
        if type.lower() not in type_map:
            raise HTTPException(status_code=400, detail=f"Invalid content type: {type}")
        content_type = type_map[type.lower()]

    consumption_status = None
    if status is not None:
        if status.lower() not in status_map:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        consumption_status = status_map[status.lower()]

    # Validate sort_by parameter
    valid_sort_options = {"title", "updated_at", "rating", "created_at"}
    if sort_by.lower() not in valid_sort_options:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by: {sort_by}. Valid options: {', '.join(sorted(valid_sort_options))}",
        )

    items = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        status=consumption_status,
        limit=limit,
        offset=offset,
        sort_by=sort_by.lower(),
    )

    return [
        ContentItemResponse(
            id=item.id,
            title=item.title,
            author=item.author,
            content_type=(
                item.content_type
                if isinstance(item.content_type, str)
                else item.content_type.value
            ),
            status=item.status if isinstance(item.status, str) else item.status.value,
            rating=item.rating,
            review=item.review,
            source=item.source,
        )
        for item in items
    ]


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
    if request.custom_rules is not None:
        existing.custom_rules = request.custom_rules
    if request.content_length_preferences is not None:
        existing.content_length_preferences.update(request.content_length_preferences)

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
    config = get_config()

    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    # Check if embeddings are enabled
    features_config = config.get("features", {}) if config else {}
    ai_enabled = features_config.get("ai_enabled", False)
    embeddings_enabled = features_config.get("embeddings_enabled", False)
    use_embeddings = ai_enabled and embeddings_enabled

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
        # Only generate embedding if AI features are enabled
        embedding = None
        if use_embeddings and embedding_gen:
            embedding = embedding_gen.generate_content_embedding(item)
        db_id = storage.save_content_item(item, embedding)

        return {"message": f"Marked '{request.title}' as completed", "id": db_id}

    except Exception as e:
        logger.error(f"Error marking content as completed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/update")
async def update_data(request: UpdateRequest) -> dict[str, Any]:
    """Start a background sync job for the specified data source.

    The sync runs in the background. Use GET /sync/status to monitor progress.
    Only one sync job can run at a time.

    Args:
        request: Update request specifying the source to sync.

    Returns:
        Message indicating sync was started or error if already running.
    """
    storage = get_storage()
    embedding_gen = get_embedding_gen()
    config = get_config()

    if not storage or not config:
        raise HTTPException(status_code=500, detail="Components not initialized")

    sync_manager = get_sync_manager()

    # Check if a sync is already running
    if sync_manager.is_running():
        status = sync_manager.get_status()
        job = status.get("job", {})
        raise HTTPException(
            status_code=409,
            detail=f"Sync already in progress for {job.get('source', 'unknown')}. "
            "Please wait for it to complete.",
        )

    # Validate source configuration before starting
    source = request.source
    inputs_config = config.get("inputs", {})

    # Resolve which sources to sync (dynamic from config)
    if source == "all":
        available = get_available_sync_sources(config)
        sources_to_sync = [s.id for s in available]
    else:
        # Single source - check it exists and is enabled
        source_config = inputs_config.get(source, {})
        if not isinstance(source_config, dict) or not source_config.get(
            "enabled", False
        ):
            return {
                "message": f"{source} source is disabled or not configured",
                "count": 0,
            }
        validation_errors = validate_source_config(source, inputs_config)
        if validation_errors:
            raise HTTPException(status_code=400, detail="; ".join(validation_errors))
        sources_to_sync = [source]

    if not sources_to_sync:
        return {"message": "No sources enabled or configured for sync", "count": 0}

    # Create the sync function that will run in background
    def run_sync(job: SyncJob) -> int:
        return _execute_sync(
            job=job,
            sources=sources_to_sync,
            config=config,
            storage=storage,
            embedding_gen=embedding_gen,
            sync_manager=sync_manager,
        )

    # Start background sync
    source_label = source if source != "all" else ", ".join(sources_to_sync)
    success, message = sync_manager.start_sync(source_label, run_sync)

    if not success:
        raise HTTPException(status_code=409, detail=message)

    logger.info(f"[SYNC] Started background sync for: {source_label}")
    return {
        "message": f"Sync started for {source_label}. Use GET /api/sync/status to monitor progress.",
        "sources": sources_to_sync,
    }


def _execute_sync(
    job: SyncJob,
    sources: list[str],
    config: dict[str, Any],
    storage: Any,
    embedding_gen: Any,
    sync_manager: Any,
) -> int:
    """Execute sync for specified sources (runs in background thread).

    Args:
        job: The sync job for progress tracking.
        sources: List of source names to sync.
        config: Application configuration.
        storage: Storage manager instance.
        embedding_gen: Embedding generator (may be None).
        sync_manager: Sync manager for progress updates.

    Returns:
        Total count of items processed.
    """
    features_config = config.get("features", {})
    ai_enabled = features_config.get("ai_enabled", False)
    embeddings_enabled = features_config.get("embeddings_enabled", False)
    use_embeddings = ai_enabled and embeddings_enabled

    inputs_config = config.get("inputs", {})
    total_count = 0

    for source_id in sources:
        logger.info(f"[SYNC] === Starting sync for source: {source_id} ===")

        plugin = get_sync_handler(source_id)
        if plugin is None:
            logger.warning(f"[SYNC] No handler for source {source_id}, skipping")
            continue

        source_config = inputs_config.get(source_id, {})
        plugin_config = transform_source_config(source_id, source_config)

        item_label = _get_item_label_for_source(source_id)
        total_count += _sync_plugin_source(
            job=job,
            source_name=plugin.display_name,
            item_label=item_label,
            plugin=plugin,
            plugin_config=plugin_config,
            storage=storage,
            embedding_gen=embedding_gen,
            use_embeddings=use_embeddings,
            sync_manager=sync_manager,
        )

    logger.info(f"[SYNC] === Completed. Total items processed: {total_count} ===")
    return total_count



def _get_item_label_for_source(source_id: str) -> str:
    """Get plural item label for a source (e.g. 'books', 'games')."""
    labels = {
        "goodreads": "books",
        "steam": "games",
        "sonarr": "series",
        "radarr": "movies",
        "csv_import": "items",
        "json_import": "items",
        "markdown_import": "items",
    }
    return labels.get(source_id, "items")


def _sync_plugin_source(
    job: SyncJob,
    source_name: str,
    item_label: str,
    plugin: Any,
    plugin_config: dict[str, Any],
    storage: Any,
    embedding_gen: Any,
    use_embeddings: bool,
    sync_manager: Any,
) -> int:
    """Sync data from a plugin source (Sonarr, Radarr, etc.)."""
    logger.info(f"[SYNC] {source_name}: Fetching {item_label}...")
    sync_manager.update_progress(
        total_items=None, items_processed=0, current_item=f"Fetching {item_label}..."
    )

    def progress_callback(
        items_processed: int, total_items: int | None, current_item: str | None
    ) -> None:
        sync_manager.update_progress(
            items_processed=items_processed,
            total_items=total_items,
            current_item=current_item,
        )
        if (
            total_items
            and items_processed > 0
            and (
                items_processed <= 5
                or items_processed % 25 == 0
                or items_processed == total_items
            )
        ):
            pct = int(items_processed * 100 / total_items) if total_items else 0
            logger.info(
                f"[SYNC] {source_name}: Loaded {items_processed}/{total_items} "
                f"{item_label} ({pct}%)"
            )

    try:
        items = list(plugin.fetch(plugin_config, progress_callback=progress_callback))
    except SourceError as error:
        error_msg = str(error)
        logger.error(f"[SYNC] {source_name}: {error_msg}")
        sync_manager.add_error(error_msg)
        raise

    total_items = len(items)
    sync_manager.update_progress(total_items=total_items, items_processed=0)
    logger.info(f"[SYNC] {source_name}: Found {total_items} {item_label}, saving...")

    count = 0
    for index, item in enumerate(items):
        try:
            sync_manager.update_progress(
                items_processed=index,
                current_item=item.title,
            )

            embedding = None
            if use_embeddings and embedding_gen:
                embedding = embedding_gen.generate_content_embedding(item)

            storage.save_content_item(item, embedding)
            count += 1

            if count % 25 == 0 or count == total_items:
                pct = int(count * 100 / total_items) if total_items > 0 else 0
                logger.info(
                    f"[SYNC] {source_name}: Saved {count}/{total_items} "
                    f"{item_label} ({pct}%)"
                )

        except Exception as error:
            error_msg = f"Failed to process '{item.title}': {error}"
            logger.warning(f"[SYNC] {source_name}: {error_msg}")
            sync_manager.add_error(error_msg)

    sync_manager.update_progress(items_processed=count)
    logger.info(
        f"[SYNC] {source_name}: Completed. {count}/{total_items} {item_label} saved."
    )
    return count


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Get system status.

    Returns:
        System status information
    """
    engine = get_engine()
    storage = get_storage()
    embedding_gen = get_embedding_gen()
    config = get_config()

    components = {
        "engine": engine is not None,
        "storage": storage is not None,
        "embedding_generator": embedding_gen is not None,
    }

    all_ready = all(components.values())

    # Read feature flags from config
    features_config = config.get("features", {}) if config else {}
    features = FeaturesStatus(
        ai_enabled=features_config.get("ai_enabled", False),
        embeddings_enabled=features_config.get("embeddings_enabled", False),
        llm_reasoning_enabled=features_config.get("llm_reasoning_enabled", False),
    )

    return StatusResponse(
        status="ready" if all_ready else "initializing",
        version="1.0.0",
        components=components,
        features=features,
    )


@router.get("/sync/sources", response_model=list[SyncSourceResponse])
async def get_sync_sources() -> list[SyncSourceResponse]:
    """Get list of available sync sources from config.

    Returns sources defined in config.inputs with enabled: true.
    No fallback to example config - uses the loaded config only.
    """
    config = get_config()
    if not config:
        return []

    sources = get_available_sync_sources(config)
    return [
        SyncSourceResponse(
            id=source.id,
            display_name=source.display_name,
            description=source.description,
        )
        for source in sources
    ]


@router.get("/sync/status", response_model=SyncStatusResponse)
async def get_sync_status() -> SyncStatusResponse:
    """Get current sync job status.

    Returns:
        Current sync status including job progress if running.
    """
    sync_manager = get_sync_manager()
    status_dict = sync_manager.get_status()

    job_data = status_dict.get("job")
    job_response = None
    if job_data:
        job_response = SyncJobResponse(**job_data)

    return SyncStatusResponse(
        status=status_dict["status"],
        job=job_response,
    )
