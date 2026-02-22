"""REST API endpoints."""

import json
import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.cli.config import get_feature_flags
from src.ingestion.sync import execute_multi_source_sync
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.utils.text import humanize_source_id
from src.web.enrichment_manager import get_enrichment_manager
from src.web.export import export_items_csv, export_items_json
from src.web.gog_auth import (
    GogAuthError,
    exchange_code_for_tokens,
    extract_code_from_input,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    update_config_with_token,
)
from src.web.state import (
    get_config,
    get_config_path,
    get_embedding_gen,
    get_engine,
    get_storage,
    reload_config,
)
from src.web.sync_manager import SyncJob, get_sync_manager
from src.web.sync_sources import (
    get_available_sync_sources,
    resolve_inputs,
    validate_source_config,
)

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"

router = APIRouter(prefix="/api", tags=["api"])


# Request/Response models
class CompletionRequest(BaseModel):
    """Request model for marking content as completed."""

    content_type: str = Field(
        ..., description="Content type (book, movie, tv_show, video_game)"
    )
    title: str = Field(..., max_length=500, description="Title of the content")
    author: str | None = Field(None, max_length=500, description="Author (for books)")
    rating: int | None = Field(None, ge=1, le=5, description="Rating (1-5)")
    review: str | None = Field(None, max_length=10000, description="Review text")


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
    plugin_display_name: str


class UserResponse(BaseModel):
    """Response model for user listing."""

    id: int
    username: str
    display_name: str | None


class ContentItemResponse(BaseModel):
    """Response model for content item listing."""

    id: str | None
    db_id: int | None = None  # Database ID for actions like ignore
    title: str
    author: str | None
    content_type: str
    status: str
    rating: int | None
    review: str | None
    source: str | None
    ignored: bool = False
    seasons_watched: list[int] | None = None
    total_seasons: int | None = None


class RecommendationResponse(BaseModel):
    """Response model for recommendations."""

    db_id: int | None = None  # Database ID for actions like ignore
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


class IgnoreItemRequest(BaseModel):
    """Request model for setting item ignored status."""

    ignored: bool = Field(..., description="Whether to ignore the item")


class ItemEditRequest(BaseModel):
    """Request model for editing a content item from the UI."""

    status: str = Field(..., description="Status value")
    rating: int | None = Field(None, ge=1, le=5)
    review: str | None = Field(None, max_length=10000)
    seasons_watched: list[int] | None = Field(None)


class EnrichmentStartRequest(BaseModel):
    """Request model for starting enrichment."""

    content_type: str | None = Field(
        None, description="Content type filter (book, movie, tv_show, video_game)"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")
    retry_not_found: bool = Field(
        False, description="Re-process items previously marked as not_found"
    )


class EnrichmentResetRequest(BaseModel):
    """Request model for resetting enrichment status."""

    provider: str | None = Field(
        None,
        description="Reset items enriched by this provider (tmdb, openlibrary, rawg)",
    )
    content_type: str | None = Field(
        None, description="Reset items of this content type"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")


class GogExchangeRequest(BaseModel):
    """Request model for GOG token exchange."""

    code_or_url: str = Field(
        ...,
        max_length=2000,
        description="Authorization code or full redirect URL from GOG",
    )


class EnrichmentJobStatusResponse(BaseModel):
    """Response model for enrichment job status."""

    running: bool = False
    completed: bool = False
    cancelled: bool = False
    items_processed: int = 0
    items_enriched: int = 0
    items_failed: int = 0
    items_not_found: int = 0
    total_items: int = 0
    current_item: str = ""
    content_type: str | None = None
    errors: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    progress_percent: float = 0.0


class EnrichmentStatsResponse(BaseModel):
    """Response model for enrichment statistics."""

    enabled: bool = False
    total: int = 0
    enriched: int = 0
    pending: int = 0
    not_found: int = 0
    failed: int = 0
    by_provider: dict[str, int] = Field(default_factory=dict)
    by_quality: dict[str, int] = Field(default_factory=dict)


class ThemeResponse(BaseModel):
    """Response model for a theme."""

    id: str
    name: str
    description: str
    author: str
    version: str
    theme_type: str


def discover_themes(themes_dir: Path) -> list[ThemeResponse]:
    """Scan the themes directory for valid themes.

    Each theme must be a subdirectory containing a theme.json file
    with name, description, author, version, and type fields.

    Args:
        themes_dir: Path to the themes directory.

    Returns:
        List of theme metadata, sorted alphabetically by directory name.
    """
    themes: list[ThemeResponse] = []

    if not themes_dir.is_dir():
        return themes

    for entry in sorted(themes_dir.iterdir()):
        if not entry.is_dir():
            continue

        theme_file = entry / "theme.json"
        if not theme_file.is_file():
            continue

        try:
            raw = json.loads(theme_file.read_text(encoding="utf-8"))
            themes.append(
                ThemeResponse(
                    id=entry.name,
                    name=raw["name"],
                    description=raw["description"],
                    author=raw["author"],
                    version=raw["version"],
                    theme_type=raw["type"],
                )
            )
        except (json.JSONDecodeError, KeyError, OSError):
            logger.warning(f"Skipping invalid theme directory: {entry.name}")
            continue

    return themes


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

    try:
        content_type = ContentType.from_string(type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid content type: {type}"
        ) from None

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
                    db_id=item.db_id,
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

    except Exception as error:
        logger.error(f"Error generating recommendations: {error}")
        raise HTTPException(
            status_code=500, detail="Failed to generate recommendations"
        ) from error


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


def _item_to_response(item: "ContentItem") -> ContentItemResponse:
    """Convert a ContentItem to a ContentItemResponse.

    Extracts seasons_watched and total_seasons from TV show metadata.

    Args:
        item: ContentItem to convert.

    Returns:
        ContentItemResponse with all fields populated.
    """
    seasons_watched: list[int] | None = None
    total_seasons: int | None = None
    metadata = item.metadata or {}

    if get_enum_value(item.content_type) == "tv_show":
        seasons_watched = metadata.get("seasons_watched")
        seasons_raw = metadata.get("seasons")
        if seasons_raw is not None:
            try:
                total_seasons = int(seasons_raw)
            except (ValueError, TypeError):
                pass

    return ContentItemResponse(
        id=item.id,
        db_id=item.db_id,
        title=item.title,
        author=item.author,
        content_type=get_enum_value(item.content_type),
        status=get_enum_value(item.status),
        rating=item.rating,
        review=item.review,
        source=item.source,
        ignored=bool(item.ignored),
        seasons_watched=seasons_watched,
        total_seasons=total_seasons,
    )


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
    include_ignored: bool = Query(
        False,
        description="Whether to include ignored items (default: hide ignored)",
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
        include_ignored: Whether to include ignored items (default: False).

    Returns:
        List of content items.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    content_type = None
    if type is not None:
        try:
            content_type = ContentType.from_string(type)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid content type: {type}"
            ) from None

    consumption_status = None
    if status is not None:
        try:
            consumption_status = ConsumptionStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid status: {status}"
            ) from None

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
        include_ignored=include_ignored,
    )

    return [_item_to_response(item) for item in items]


@router.get("/items/export")
async def export_items(
    type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
    format: str = Query("csv", description="Export format: csv or json"),
    user_id: int = Query(1, ge=1, description="User ID"),
) -> Response:
    """Export library items as CSV or JSON file download.

    Args:
        type: Content type to export
        format: Export format (csv or json)
        user_id: User ID for filtering items

    Returns:
        File download response
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    try:
        content_type = ContentType.from_string(type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid content type: {type}"
        ) from None

    export_format = format.lower()
    if export_format not in {"csv", "json"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format: {format}. Must be csv or json",
        )

    items = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
    )

    content_type_value = get_enum_value(content_type)
    filename = f"{content_type_value}s.{export_format}"

    if export_format == "csv":
        content = export_items_csv(items, content_type)
        media_type = "text/csv"
    else:
        content = export_items_json(items, content_type)
        media_type = "application/json"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/items/{db_id}/ignore")
async def set_item_ignored(
    db_id: int,
    request: IgnoreItemRequest,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> dict[str, Any]:
    """Set the ignored status of a content item.

    Ignored items are excluded from recommendations.

    Args:
        db_id: Database ID of the item
        request: Request with ignored status
        user_id: User ID for authorization

    Returns:
        Updated item info
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    # Verify item exists and belongs to user
    item = storage.get_content_item(db_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    success = storage.set_item_ignored(db_id, request.ignored, user_id=user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update item")

    return {
        "db_id": db_id,
        "title": item.title,
        "ignored": request.ignored,
        "message": f"Item '{item.title}' {'ignored' if request.ignored else 'unignored'}",
    }


@router.get("/items/{db_id}", response_model=ContentItemResponse)
async def get_single_item(
    db_id: int,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> ContentItemResponse:
    """Get a single content item by database ID.

    Args:
        db_id: Database ID of the item.
        user_id: User ID for authorization.

    Returns:
        Content item details.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    item = storage.get_content_item(db_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return _item_to_response(item)


@router.patch("/items/{db_id}", response_model=ContentItemResponse)
async def edit_item(
    db_id: int,
    request: ItemEditRequest,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> ContentItemResponse:
    """Edit a content item from the UI.

    Allows unrestricted editing of status, rating, review, and
    seasons_watched (TV shows). Unlike sync, status can go backward
    and rating/review can be changed or cleared.

    Args:
        db_id: Database ID of the item.
        request: Edit request with new values.
        user_id: User ID for authorization.

    Returns:
        Updated content item.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    # Validate status
    valid_statuses = {"unread", "currently_consuming", "completed"}
    if request.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {request.status}. "
            f"Valid options: {', '.join(sorted(valid_statuses))}",
        )

    success = storage.update_item_from_ui(
        db_id=db_id,
        status=request.status,
        rating=request.rating,
        review=request.review,
        seasons_watched=request.seasons_watched,
        user_id=user_id,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")

    # Fetch and return the updated item
    updated_item = storage.get_content_item(db_id, user_id=user_id)
    if not updated_item:
        raise HTTPException(status_code=404, detail="Item not found after update")

    return _item_to_response(updated_item)


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
    storage = get_storage()
    embedding_gen = get_embedding_gen()
    config = get_config()

    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    # Check if embeddings are enabled
    use_embeddings = get_feature_flags(config)["use_embeddings"]

    try:
        content_type = ContentType.from_string(request.content_type)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid content type: {request.content_type}"
        ) from None

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
        db_id = storage.save_content_item(item, embedding=embedding)

        return {"message": f"Marked '{request.title}' as completed", "id": db_id}

    except Exception as error:
        logger.error(f"Error marking content as completed: {error}")
        raise HTTPException(
            status_code=500, detail="Failed to mark content as completed"
        ) from error


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
        resolved = resolve_inputs(config)
    else:
        # Single source - check it exists and is enabled
        source_entry = inputs_config.get(source, {})
        if not isinstance(source_entry, dict) or not source_entry.get("enabled", False):
            return {
                "message": f"{source} source is disabled or not configured",
                "count": 0,
            }
        validation_errors = validate_source_config(source, config)
        if validation_errors:
            raise HTTPException(status_code=400, detail="; ".join(validation_errors))
        resolved = [
            entry for entry in resolve_inputs(config) if entry.source_id == source
        ]

    if not resolved:
        return {"message": "No sources enabled or configured for sync", "count": 0}

    sources_to_sync = [entry.source_id for entry in resolved]

    use_embeddings = get_feature_flags(config)["use_embeddings"]

    # Check if auto-enrichment is enabled
    enrichment_config = config.get("enrichment", {})
    auto_enrich = enrichment_config.get("enabled", False) and enrichment_config.get(
        "auto_enrich_on_sync", False
    )

    source_pairs = [(entry.plugin, entry.config) for entry in resolved]

    # Create the sync function that will run in background
    def run_sync(job: SyncJob) -> int:
        def progress_callback(
            items_processed: int,
            total_items: int | None,
            current_item: str | None,
            current_source: str | None,
        ) -> None:
            sync_manager.update_progress(
                items_processed=items_processed,
                total_items=total_items,
                current_item=current_item,
                current_source=current_source,
            )

        results = execute_multi_source_sync(
            sources=source_pairs,
            storage_manager=storage,
            embedding_generator=embedding_gen,
            use_embeddings=use_embeddings,
            progress_callback=progress_callback,
            error_callback=sync_manager.add_error,
            mark_for_enrichment=auto_enrich,
        )
        return sum(result.items_synced for result in results)

    # Determine content type for enrichment based on synced source config
    # If syncing a single source with a configured content_type, use that type
    # Otherwise (multiple sources or "all"), enrich all types
    enrichment_content_type: ContentType | None = None
    if len(resolved) == 1:
        content_type_str = resolved[0].config.get("content_type")
        if content_type_str:
            try:
                enrichment_content_type = ContentType(content_type_str)
            except ValueError:
                logger.warning(
                    f"Invalid content_type '{content_type_str}' for source "
                    f"{resolved[0].source_id}, enriching all types"
                )

    # Create completion callback for auto-enrichment
    def on_sync_complete() -> None:
        if auto_enrich:
            enrichment_manager = get_enrichment_manager()
            started, message = enrichment_manager.start_enrichment(
                storage_manager=storage,
                config=config,
                content_type=enrichment_content_type,
            )
            if started:
                logger.info(f"[ENRICHMENT] Auto-started after sync: {message}")
            else:
                logger.info(f"[ENRICHMENT] Auto-start skipped: {message}")

    # Start background sync
    source_label = humanize_source_id(source) if source != "all" else "All Sources"
    success, message = sync_manager.start_sync(
        source_label, run_sync, on_complete=on_sync_complete
    )

    if not success:
        raise HTTPException(status_code=409, detail=message)

    logger.info(f"[SYNC] Started background sync for: {source_label}")
    return {
        "message": f"Sync started for {source_label}. Use GET /api/sync/status to monitor progress.",
        "sources": sources_to_sync,
    }


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

    # Read feature flags from config
    flags = get_feature_flags(config)
    features = FeaturesStatus(
        ai_enabled=flags["ai_enabled"],
        embeddings_enabled=flags["embeddings_enabled"],
        llm_reasoning_enabled=flags["llm_reasoning_enabled"],
    )

    # Only require embedding_generator when AI features are enabled
    components = {
        "engine": engine is not None,
        "storage": storage is not None,
        "embedding_generator": (
            embedding_gen is not None if flags["ai_enabled"] else True
        ),
    }

    all_ready = all(components.values())

    return StatusResponse(
        status="ready" if all_ready else "initializing",
        version=APP_VERSION,
        components=components,
        features=features,
    )


@router.post("/config/reload")
async def reload_config_endpoint() -> dict[str, Any]:
    """Reload configuration from disk.

    Useful for picking up config changes without restarting the server.

    Returns:
        Success status.
    """
    success = reload_config()
    if success:
        return {"success": True, "message": "Configuration reloaded successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reload configuration")


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
            plugin_display_name=source.plugin_display_name,
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


# ---------------------------------------------------------------------------
# Enrichment endpoints
# ---------------------------------------------------------------------------


@router.post("/enrichment/start")
async def start_enrichment(
    request: EnrichmentStartRequest,
) -> dict[str, Any]:
    """Start background metadata enrichment.

    Enriches content items with genres, tags, and descriptions from
    external APIs (TMDB, OpenLibrary, RAWG).

    Args:
        request: Enrichment start request with optional filters

    Returns:
        Message indicating enrichment was started or error
    """
    storage = get_storage()
    config = get_config()

    if not storage or not config:
        raise HTTPException(status_code=500, detail="Components not initialized")

    # Check if enrichment is enabled
    enrichment_config = config.get("enrichment", {})
    if not enrichment_config.get("enabled", False):
        raise HTTPException(
            status_code=400,
            detail="Enrichment is disabled. Set enrichment.enabled: true in config",
        )

    # Map content type if provided
    content_type = None
    if request.content_type:
        try:
            content_type = ContentType.from_string(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid content type: {request.content_type}",
            ) from None

    enrichment_manager = get_enrichment_manager()
    success, message = enrichment_manager.start_enrichment(
        storage_manager=storage,
        config=config,
        content_type=content_type,
        user_id=request.user_id,
        include_not_found=request.retry_not_found,
    )

    if not success:
        raise HTTPException(status_code=409, detail=message)

    return {"message": message, "status": "started"}


@router.post("/enrichment/stop")
async def stop_enrichment() -> dict[str, Any]:
    """Stop the current enrichment job.

    Returns:
        Message indicating enrichment was stopped
    """
    enrichment_manager = get_enrichment_manager()
    success, message = enrichment_manager.stop_enrichment()

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"message": message, "status": "stopping"}


@router.get("/enrichment/status", response_model=EnrichmentJobStatusResponse | None)
async def get_enrichment_status() -> EnrichmentJobStatusResponse | None:
    """Get current enrichment job status.

    Returns:
        Current enrichment job status or null if no job exists
    """
    enrichment_manager = get_enrichment_manager()
    status = enrichment_manager.get_status()

    if status is None:
        return EnrichmentJobStatusResponse()

    return EnrichmentJobStatusResponse(
        running=status.running,
        completed=status.completed,
        cancelled=status.cancelled,
        items_processed=status.items_processed,
        items_enriched=status.items_enriched,
        items_failed=status.items_failed,
        items_not_found=status.items_not_found,
        total_items=status.total_items,
        current_item=status.current_item,
        content_type=status.content_type,
        errors=status.errors,
        elapsed_seconds=status.elapsed_seconds,
        progress_percent=status.progress_percent,
    )


@router.get("/enrichment/stats", response_model=EnrichmentStatsResponse)
async def get_enrichment_stats(
    user_id: int = Query(1, ge=1, description="User ID for filtering stats"),
) -> EnrichmentStatsResponse:
    """Get enrichment statistics.

    Args:
        user_id: User ID for filtering stats

    Returns:
        Enrichment statistics
    """
    config = get_config() or {}
    enrichment_config = config.get("enrichment", {})
    enrichment_enabled = enrichment_config.get("enabled", False)

    storage = get_storage()

    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    stats = storage.get_enrichment_stats(user_id=user_id)

    return EnrichmentStatsResponse(
        enabled=enrichment_enabled,
        total=cast(int, stats.get("total", 0)),
        enriched=cast(int, stats.get("enriched", 0)),
        pending=cast(int, stats.get("pending", 0)),
        not_found=cast(int, stats.get("not_found", 0)),
        failed=cast(int, stats.get("failed", 0)),
        by_provider=cast(dict[str, int], stats.get("by_provider", {})),
        by_quality=cast(dict[str, int], stats.get("by_quality", {})),
    )


@router.post("/enrichment/reset")
async def reset_enrichment(
    request: EnrichmentResetRequest,
) -> dict[str, Any]:
    """Reset enrichment status for re-processing.

    Marks items as needing enrichment again, allowing them to be
    re-processed by the enrichment providers.

    Args:
        request: Reset request with optional filters

    Returns:
        Count of items reset
    """
    storage = get_storage()

    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    # Map content type if provided
    content_type = None
    if request.content_type:
        try:
            content_type = ContentType.from_string(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid content type: {request.content_type}",
            ) from None

    count = storage.reset_enrichment_status(
        provider=request.provider,
        content_type=content_type,
        user_id=request.user_id,
    )

    return {"message": f"Reset enrichment status for {count} item(s)", "count": count}


# ---------------------------------------------------------------------------
# Theme endpoints
# ---------------------------------------------------------------------------

THEMES_DIR = Path(__file__).resolve().parent / "static" / "themes"


@router.get("/themes", response_model=list[ThemeResponse])
async def list_themes() -> list[ThemeResponse]:
    """List all available UI themes.

    Scans the themes directory for subdirectories containing theme.json.

    Returns:
        List of theme metadata sorted alphabetically.
    """
    return discover_themes(THEMES_DIR)


@router.get("/themes/default")
async def get_default_theme() -> dict[str, str]:
    """Get the server-configured default theme.

    Reads the web.theme setting from config. Falls back to "nord"
    if not configured. The frontend uses this when no localStorage
    preference is set.

    Returns:
        Dictionary with the default theme ID.
    """
    config = get_config()
    web_config = config.get("web", {}) if config else {}
    default_theme = web_config.get("theme", "nord")
    return {"theme": default_theme}


# ---------------------------------------------------------------------------
# GOG OAuth endpoints
# ---------------------------------------------------------------------------


@router.get("/gog/status")
async def get_gog_status() -> dict[str, Any]:
    """Get GOG integration status.

    Returns:
        Status of GOG integration (enabled, connected, auth_url).
    """
    config = get_config()
    if not config:
        raise HTTPException(status_code=500, detail="Config not initialized")

    enabled = is_gog_enabled(config)
    connected = has_gog_token(config)

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": get_gog_auth_url() if enabled else None,
    }


@router.post("/gog/exchange")
async def exchange_gog_token(request: GogExchangeRequest) -> dict[str, Any]:
    """Exchange GOG authorization code for tokens.

    Accepts either the raw authorization code or the full redirect URL.
    Attempts to update config.yaml, or returns the token for manual setup.

    Args:
        request: Request with code or URL.

    Returns:
        Success message with optional refresh_token if config couldn't be updated.
    """
    config = get_config()
    config_path = get_config_path()

    if not config:
        raise HTTPException(status_code=500, detail="Config not initialized")

    if not is_gog_enabled(config):
        raise HTTPException(
            status_code=400,
            detail="GOG is not enabled. Set inputs.gog.enabled: true in config.yaml first.",
        )

    try:
        # Extract code from input (handles both URL and raw code)
        code = extract_code_from_input(request.code_or_url)

        # Exchange code for tokens
        tokens = exchange_code_for_tokens(code)
        refresh_token = tokens["refresh_token"]

        # Try to update config file with refresh token
        config_updated = False
        if config_path:
            try:
                update_config_with_token(Path(config_path), refresh_token)
                config_updated = True
                logger.info("Successfully connected GOG account and updated config")
            except GogAuthError as config_error:
                logger.warning(f"Could not update config: {config_error}")

        if config_updated:
            return {
                "success": True,
                "message": "GOG account connected successfully! You can now sync your GOG library.",
            }
        else:
            # Return token for manual setup
            return {
                "success": True,
                "manual_setup": True,
                "refresh_token": refresh_token,
                "message": "Token obtained! Add it to your config.yaml manually.",
            }

    except GogAuthError as error:
        logger.warning(f"GOG auth error: {error}")
        raise HTTPException(
            status_code=400, detail="GOG authentication failed"
        ) from error
    except Exception as error:
        logger.error(f"Unexpected error during GOG token exchange: {error}")
        raise HTTPException(
            status_code=500, detail="Unexpected error during GOG authentication"
        ) from error
