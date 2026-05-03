"""REST API endpoints."""

import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from src import __version__ as APP_VERSION
from src.cli.config import get_feature_flags
from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.sync import execute_multi_source_sync
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.storage.manager import VALID_SORT_OPTIONS, StorageManager
from src.utils.item_serialization import item_to_dict
from src.utils.text import humanize_source_id
from src.web.enrichment_manager import get_enrichment_manager
from src.web.epic_auth import (
    EpicAuthError,
    get_epic_auth_url,
    has_epic_token,
    is_epic_enabled,
    save_epic_token,
)
from src.web.epic_auth import exchange_code_for_tokens as exchange_epic_tokens
from src.web.epic_auth import extract_code_from_input as extract_epic_code
from src.web.export import export_items_csv, export_items_json
from src.web.gog_auth import (
    GogAuthError,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from src.web.gog_auth import exchange_code_for_tokens as exchange_gog_tokens
from src.web.gog_auth import extract_code_from_input as extract_gog_code
from src.web.state import (
    get_config,
    get_embedding_gen,
    get_engine,
    get_storage,
    reload_config,
)
from src.web.sync_manager import SyncJob, get_sync_manager
from src.web.sync_sources import (
    SourceConfigError,
    build_config_view,
    build_schema_view,
    clear_source_secret_value,
    create_source,
    delete_source,
    get_available_sync_sources,
    list_available_plugins,
    migrate_source,
    resolve_inputs,
    resolve_source_plugin,
    set_source_enabled_state,
    set_source_secret_value,
    update_source_config_values,
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
    enabled: bool


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
    date_completed: str | None = None
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


class RecommendationsConfig(BaseModel):
    """Recommendations configuration exposed to the frontend.

    Defaults mirror config/example.yaml recommendations section.
    """

    max_count: int = 20
    default_count: int = 5


class StatusResponse(BaseModel):
    """Response model for system status."""

    status: str
    version: str
    components: dict[str, bool]
    features: FeaturesStatus = Field(default_factory=FeaturesStatus)
    recommendations_config: RecommendationsConfig = Field(
        default_factory=RecommendationsConfig
    )


class UserPreferenceResponse(BaseModel):
    """Response model for user preferences."""

    scorer_weights: dict[str, float]
    series_in_order: bool
    variety_after_completion: bool
    custom_rules: list[str]
    content_length_preferences: dict[str, str] = Field(default_factory=dict)
    theme: str = ""


class SyncJobResponse(BaseModel):
    """Response model for sync job status."""

    source: str
    status: str
    started_at: str | None
    completed_at: str | None
    items_processed: int
    total_items: int | None
    current_item: str | None
    current_source: str | None
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
    theme: str | None = None


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


class EpicExchangeRequest(BaseModel):
    """Request model for Epic Games token exchange."""

    code_or_json: str = Field(
        ...,
        max_length=4000,
        description="Authorization code or JSON response from Epic Games",
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


class SourceFieldSchema(BaseModel):
    """One field in a source plugin's config schema."""

    name: str
    field_type: str
    required: bool
    default: Any = None
    description: str = ""
    sensitive: bool = False


class SourceSchemaResponse(BaseModel):
    """Plugin config schema for a single source (drives autogen UI/CLI)."""

    source_id: str
    plugin: str
    plugin_display_name: str
    fields: list[SourceFieldSchema]


class SourceConfigResponse(BaseModel):
    """Current config values for a source. Sensitive fields are never returned."""

    source_id: str
    plugin: str
    plugin_display_name: str
    enabled: bool
    migrated: bool
    migrated_at: str | None
    field_values: dict[str, Any]
    secret_status: dict[str, bool]


class SourceConfigUpdateRequest(BaseModel):
    """Bulk update of non-sensitive fields for a migrated source."""

    values: dict[str, Any]


class SourceSecretUpdateRequest(BaseModel):
    """Set or rotate a single sensitive field."""

    value: str


class SourceEnabledUpdateRequest(BaseModel):
    """Toggle the enabled flag for a migrated source."""

    enabled: bool


class SourceMigrationResponse(BaseModel):
    """Result of migrating a YAML source entry into the database."""

    source_id: str
    migrated_at: str
    fields_migrated: list[str]
    secrets_migrated: list[str]


class PluginInfoResponse(BaseModel):
    """One installed source plugin's metadata for the Add-Source picker."""

    name: str
    display_name: str
    description: str
    content_types: list[str]
    requires_api_key: bool
    requires_network: bool
    fields: list[SourceFieldSchema]


class SourceCreateRequest(BaseModel):
    """Body for ``POST /api/sync/sources`` — create a new DB-backed source."""

    id: str = Field(..., max_length=64)
    plugin: str = Field(..., max_length=128)
    values: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


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
            logger.warning("Skipping invalid theme directory: %s", entry.name)
            continue

    return themes


def _get_recommendations_config(config: dict[str, Any] | None) -> RecommendationsConfig:
    """Extract recommendations config from the loaded config dict.

    Falls back to model defaults when the config or section is absent.
    """
    rec_section = config.get("recommendations", {}) if config else {}
    return RecommendationsConfig(
        **{
            k: rec_section[k]
            for k in ("max_count", "default_count")
            if k in rec_section
        }
    )


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

    # Validate count against config-driven max_count (may be tighter than hard limit)
    max_count = _get_recommendations_config(config).max_count
    if count > max_count:
        raise HTTPException(
            status_code=400,
            detail="Requested count exceeds the maximum allowed",
        )

    try:
        content_type = ContentType.from_string(type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
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
        logger.error("Error generating recommendations: %s", error)
        raise HTTPException(
            status_code=500, detail="Failed to generate recommendations"
        ) from error


@router.get("/recommendations/stream")
async def stream_recommendations(
    type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
    count: int = Query(5, ge=1, description="Number of recommendations"),
    user_id: int = Query(1, ge=1, description="User ID for personalized preferences"),
) -> StreamingResponse:
    """Stream recommendations with progressive LLM blurb generation.

    Returns Server-Sent Events in two phases:

    - Phase 1 (immediate): ``{"type": "recommendations", "items": [...]}``
      — pipeline results without LLM reasoning.
    - Phase 2 (progressive): ``{"type": "blurb", "index": N, "llm_reasoning": "..."}``
      per item as each LLM call completes.
    - Final: ``{"type": "done"}``

    Args:
        type: Content type
        count: Number of recommendations
        user_id: User ID for loading per-user preferences

    Returns:
        SSE streaming response
    """
    engine = get_engine()
    storage = get_storage()
    config = get_config()

    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    max_count = _get_recommendations_config(config).max_count
    if count > max_count:
        raise HTTPException(
            status_code=400,
            detail="Requested count exceeds the maximum allowed",
        )

    try:
        content_type = ContentType.from_string(type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
        ) from None

    def generate_sse() -> Iterator[str]:
        """Generate SSE events: recommendations first, then blurbs."""
        try:
            user_preference_config = None
            if storage:
                user_preference_config = storage.get_user_preference_config(user_id)

            # Generate recommendations without LLM reasoning
            recommendations = engine.generate_recommendations(
                content_type=content_type,
                count=count,
                use_llm=False,
                user_preference_config=user_preference_config,
            )

            if not recommendations:
                yield f"data: {json.dumps({'type': 'recommendations', 'items': []})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            # Phase 1: send recommendations immediately (no LLM reasoning)
            items_payload: list[dict[str, Any]] = []
            for rec in recommendations:
                item = rec["item"]
                items_payload.append(
                    {
                        "db_id": item.db_id,
                        "title": item.title,
                        "author": item.author,
                        "score": rec["score"],
                        "similarity_score": rec["similarity_score"],
                        "preference_score": rec["preference_score"],
                        "reasoning": rec["reasoning"],
                        "llm_reasoning": None,
                        "score_breakdown": rec.get("score_breakdown", {}),
                    }
                )
            event: dict[str, Any] = {
                "type": "recommendations",
                "items": items_payload,
            }
            yield f"data: {json.dumps(event)}\n\n"

            # Phase 2: generate blurbs per item, stream as they complete
            consumed_items = engine.storage.get_completed_items(
                content_type=None, min_rating=None
            )

            items_with_index: list[tuple[int, ContentItem, list[ContentItem]]] = []
            for idx, rec in enumerate(recommendations):
                refs = list(rec.get("contributing_items") or [])
                items_with_index.append((idx, rec["item"], refs))

            with ThreadPoolExecutor(
                max_workers=min(len(items_with_index), 4)
            ) as executor:
                future_to_index = {
                    executor.submit(
                        engine.generate_blurb_for_item,
                        content_type,
                        item,
                        consumed_items,
                        refs or None,
                    ): idx
                    for idx, item, refs in items_with_index
                }
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    try:
                        blurb = future.result()
                    except Exception as exc:
                        logger.warning(
                            "Streaming blurb failed for index %d: %s",
                            idx,
                            exc,
                        )
                        blurb = None
                    if blurb:
                        blurb_event = {
                            "type": "blurb",
                            "index": idx,
                            "llm_reasoning": blurb,
                        }
                        yield f"data: {json.dumps(blurb_event)}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception:
            logger.error("Streaming recommendation error", exc_info=True)
            error_event = {
                "type": "error",
                "message": "Failed to generate recommendations",
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


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
    """Convert a ContentItem to a ContentItemResponse via the shared dict."""
    return ContentItemResponse.model_validate(item_to_dict(item))


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

    # Validate sort_by parameter
    if sort_by.lower() not in VALID_SORT_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail="Invalid sort_by. Valid options: created_at, rating, title, updated_at",
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

    safe_title = item.title.replace("\n", " ").replace("\r", " ")
    return {
        "db_id": db_id,
        "title": safe_title,
        "ignored": request.ignored,
        "message": f"Item '{safe_title}' {'ignored' if request.ignored else 'unignored'}",
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
            detail="Invalid status. Valid options: completed, currently_consuming, unread",
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
    if request.theme is not None:
        existing.theme = request.theme

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
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
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

        safe_title = request.title.replace("\n", " ").replace("\r", " ")
        return {"message": f"Marked '{safe_title}' as completed", "id": db_id}

    except Exception as error:
        logger.error("Error marking content as completed: %s", error)
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
        resolved = resolve_inputs(config, storage=storage)
    else:
        # Single source - check it exists and is enabled
        source_entry = inputs_config.get(source, {})
        if not isinstance(source_entry, dict) or not source_entry.get("enabled", False):
            return {
                "message": f"{source} source is disabled or not configured",
                "count": 0,
            }
        validation_errors = validate_source_config(source, config, storage=storage)
        if validation_errors:
            logger.warning(
                "Sync config validation failed for %s: %s",
                source,
                "; ".join(validation_errors),
            )
            raise HTTPException(
                status_code=400,
                detail=f"Source '{source}' is not properly configured: "
                + "; ".join(validation_errors),
            )
        resolved = [
            entry
            for entry in resolve_inputs(config, storage=storage)
            if entry.source_id == source
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
            user_id=1,
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
                    "Invalid content_type '%s' for source %s, enriching all types",
                    content_type_str,
                    resolved[0].source_id,
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
                logger.info("[ENRICHMENT] Auto-started after sync: %s", message)
            else:
                logger.info("[ENRICHMENT] Auto-start skipped: %s", message)

    # Start background sync
    source_label = humanize_source_id(source) if source != "all" else "All Sources"
    success, message = sync_manager.start_sync(
        source_label, run_sync, on_complete=on_sync_complete
    )

    if not success:
        raise HTTPException(status_code=409, detail=message)

    logger.info("[SYNC] Started background sync for: %s", source_label)
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
        recommendations_config=_get_recommendations_config(config),
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

    storage = get_storage()
    sources = get_available_sync_sources(config, storage=storage)
    return [
        SyncSourceResponse(
            id=source.id,
            display_name=source.display_name,
            plugin_display_name=source.plugin_display_name,
            enabled=source.enabled,
        )
        for source in sources
    ]


@router.get("/plugins", response_model=list[PluginInfoResponse])
async def list_plugins() -> list[PluginInfoResponse]:
    """List every registered source plugin (for the Add-Source picker)."""
    return [PluginInfoResponse(**info) for info in list_available_plugins()]


@router.post(
    "/sync/sources",
    response_model=SourceConfigResponse,
    status_code=201,
)
async def create_source_endpoint(
    payload: SourceCreateRequest,
) -> SourceConfigResponse:
    """Create a new DB-backed source.

    Sensitive fields must be set via ``PUT /secret/{key}`` *after* this
    call returns; the create path rejects them in the body to keep the
    sensitive-write surface narrow.
    """
    storage = _require_storage()
    try:
        view = create_source(
            payload.id,
            payload.plugin,
            payload.values,
            storage,
            enabled=payload.enabled,
            config=get_config(),
        )
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**view)


@router.delete("/sync/sources/{source_id}", status_code=204)
async def delete_source_endpoint(source_id: str) -> Response:
    """Drop a DB-backed source and clear its credentials."""
    storage = _require_storage()
    try:
        delete_source(source_id, storage)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


# Per-source configuration endpoints. Business logic lives in
# ``src.web.sync_sources``; the endpoints below adapt those helpers to
# FastAPI / Pydantic so the CLI ``source`` group can share them.


_ERROR_KIND_TO_STATUS: dict[str, int] = {
    "not_found": 404,
    "not_migrated": 404,
    "invalid_field": 400,
    "not_sensitive": 400,
    "sensitive_in_config": 400,
    "conflict": 409,
    "invalid_id": 400,
    "unknown_plugin": 400,
}

# Fixed user-facing strings keyed by error kind so HTTP responses never
# echo back caller-controlled identifiers (path params would otherwise
# end up in JSON `detail` fields).
_ERROR_KIND_TO_DETAIL: dict[str, str] = {
    "not_found": "Field or source not found.",
    "not_migrated": "Source has not been migrated to the database.",
    "invalid_field": "Request references an unknown field.",
    "not_sensitive": "Field is not sensitive — use the config endpoint instead.",
    "sensitive_in_config": "Sensitive fields must be set via the secret endpoint.",
    "conflict": "A source with that id already exists.",
    "invalid_id": (
        "Source id must start with a lowercase letter and contain only "
        "lowercase letters, digits, and underscores."
    ),
    "unknown_plugin": "The requested plugin is not registered.",
}


def _sanitize_for_log(value: str) -> str:
    """Strip CR/LF/NUL from a string before logging.

    Path parameters are user-controlled. Without sanitization an attacker
    could inject newlines and forge structured log lines (CWE-117).
    """
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\0", "\\0")


def _require_plugin(source_id: str) -> SourcePlugin:
    plugin = resolve_source_plugin(source_id, get_config(), get_storage())
    if plugin is None:
        # Server-side log carries the identifier; the wire response stays generic.
        logger.info("Source lookup miss for source_id=%s", _sanitize_for_log(source_id))
        raise HTTPException(status_code=404, detail="Source not found.")
    return plugin


def _require_storage() -> StorageManager:
    storage = get_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail="Storage unavailable")
    return storage


def _config_error_to_http(error: SourceConfigError) -> HTTPException:
    # error.kind is controlled internally; error.message embeds caller-supplied
    # values so it stays out of the log to prevent log injection.
    logger.info("Source config error kind=%s", error.kind)
    return HTTPException(
        status_code=_ERROR_KIND_TO_STATUS.get(error.kind, 400),
        detail=_ERROR_KIND_TO_DETAIL.get(error.kind, "Invalid request."),
    )


@router.get("/sync/sources/{source_id}/schema", response_model=SourceSchemaResponse)
async def get_source_schema(source_id: str) -> SourceSchemaResponse:
    """Return the plugin config schema for a source (drives autogen forms)."""
    plugin = _require_plugin(source_id)
    return SourceSchemaResponse(**build_schema_view(source_id, plugin))


@router.get("/sync/sources/{source_id}/config", response_model=SourceConfigResponse)
async def get_source_config_endpoint(source_id: str) -> SourceConfigResponse:
    """Return current config values for a source. Sensitive fields are stripped."""
    plugin = _require_plugin(source_id)
    return SourceConfigResponse(
        **build_config_view(source_id, plugin, get_config(), get_storage())
    )


@router.post(
    "/sync/sources/{source_id}/migrate", response_model=SourceMigrationResponse
)
async def migrate_source_to_db(source_id: str) -> SourceMigrationResponse:
    """Copy a YAML source entry into the database (idempotent)."""
    plugin = _require_plugin(source_id)
    storage = _require_storage()
    return SourceMigrationResponse(
        **migrate_source(source_id, plugin, get_config(), storage)
    )


@router.put("/sync/sources/{source_id}/config", response_model=SourceConfigResponse)
async def update_source_config_endpoint(
    source_id: str, payload: SourceConfigUpdateRequest
) -> SourceConfigResponse:
    """Update non-sensitive fields on a migrated source."""
    plugin = _require_plugin(source_id)
    storage = _require_storage()
    try:
        update_source_config_values(source_id, plugin, storage, payload.values)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(
        **build_config_view(source_id, plugin, get_config(), storage)
    )


@router.put("/sync/sources/{source_id}/secret/{key}", status_code=204)
async def set_source_secret_endpoint(
    source_id: str, key: str, payload: SourceSecretUpdateRequest
) -> Response:
    """Encrypt and store a sensitive field for a source."""
    plugin = _require_plugin(source_id)
    storage = _require_storage()
    try:
        set_source_secret_value(source_id, plugin, storage, key, payload.value)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


@router.delete("/sync/sources/{source_id}/secret/{key}", status_code=204)
async def clear_source_secret_endpoint(source_id: str, key: str) -> Response:
    """Delete a sensitive field's stored value for a source."""
    plugin = _require_plugin(source_id)
    storage = _require_storage()
    try:
        clear_source_secret_value(source_id, plugin, storage, key)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


@router.put("/sync/sources/{source_id}/enabled", response_model=SourceConfigResponse)
async def set_source_enabled_endpoint(
    source_id: str, payload: SourceEnabledUpdateRequest
) -> SourceConfigResponse:
    """Toggle the enabled flag on a migrated source."""
    plugin = _require_plugin(source_id)
    storage = _require_storage()
    try:
        set_source_enabled_state(source_id, storage, payload.enabled)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(
        **build_config_view(source_id, plugin, get_config(), storage)
    )


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
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
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
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
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
    """Get the default theme for new users.

    Returns "nord" as the built-in default. Per-user theme preferences
    are stored via the user preferences API and take priority.

    Returns:
        Dictionary with the default theme ID.
    """
    return {"theme": "nord"}


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
    storage = get_storage()
    connected = has_gog_token(config, storage=storage)

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": get_gog_auth_url() if enabled else None,
    }


@router.post("/gog/exchange")
async def exchange_gog_token(request: GogExchangeRequest) -> dict[str, Any]:
    """Exchange GOG authorization code for tokens.

    Accepts either the raw authorization code or the full redirect URL.
    Saves the refresh token to the encrypted credential database.

    Args:
        request: Request with code or URL.

    Returns:
        Success message. The token is never included in the HTTP response.
    """
    config = get_config()
    storage = get_storage()

    if not config:
        raise HTTPException(status_code=500, detail="Config not initialized")

    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    if not is_gog_enabled(config):
        raise HTTPException(
            status_code=400,
            detail="GOG is not enabled. Set inputs.gog.enabled: true in config.yaml first.",
        )

    try:
        # Extract code from input (handles both URL and raw code)
        code = extract_gog_code(request.code_or_url)

        # Exchange code for tokens
        tokens = exchange_gog_tokens(code)
        refresh_token = tokens["refresh_token"]

        # Save token to encrypted database storage
        save_gog_token(storage, refresh_token)
        logger.info("Successfully connected GOG account")

        return {
            "success": True,
            "message": "GOG account connected successfully! You can now sync your GOG library.",
        }

    except GogAuthError as error:
        logger.warning("GOG auth error: %s", error)
        raise HTTPException(
            status_code=400, detail="GOG authentication failed"
        ) from error
    except Exception as error:
        logger.error("Unexpected error during GOG token exchange", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Unexpected error during GOG authentication"
        ) from error


@router.delete("/gog/token")
async def disconnect_gog(user_id: int = Query(1, ge=1)) -> dict[str, Any]:
    """Disconnect GOG by deleting the stored refresh token.

    Mirrors the CLI `auth disconnect --source gog` command.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    deleted = storage.delete_credential(user_id, "gog", "refresh_token")
    if not deleted:
        raise HTTPException(status_code=404, detail="No active GOG connection found")
    logger.info("Disconnected GOG account for user %s", user_id)
    return {"success": True, "message": "GOG disconnected."}


# ---------------------------------------------------------------------------
# Epic Games OAuth endpoints
# ---------------------------------------------------------------------------


@router.get("/epic/status")
async def get_epic_status() -> dict[str, Any]:
    """Get Epic Games integration status.

    Returns:
        Status of Epic Games integration (enabled, connected, auth_url).
    """
    config = get_config()
    if not config:
        raise HTTPException(status_code=500, detail="Config not initialized")

    enabled = is_epic_enabled(config)
    storage = get_storage()
    connected = has_epic_token(config, storage=storage)

    auth_url: str | None = None
    if enabled:
        try:
            auth_url = get_epic_auth_url()
        except Exception:
            logger.warning("Failed to generate Epic auth URL", exc_info=True)

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": auth_url,
    }


@router.post("/epic/exchange")
async def exchange_epic_token(request: EpicExchangeRequest) -> dict[str, Any]:
    """Exchange Epic Games authorization code for tokens.

    Accepts either the raw authorization code or JSON containing it.
    Saves the refresh token to the encrypted credential database.

    Args:
        request: Request with code or JSON.

    Returns:
        Success message. The token is never included in the HTTP response.
    """
    config = get_config()
    storage = get_storage()

    if not config:
        raise HTTPException(status_code=500, detail="Config not initialized")

    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    if not is_epic_enabled(config):
        raise HTTPException(
            status_code=400,
            detail="Epic Games is not enabled in the current configuration.",
        )

    try:
        # Extract code from input (handles both JSON and raw code)
        code = extract_epic_code(request.code_or_json)

        # Exchange code for tokens via EPCAPI
        tokens = exchange_epic_tokens(code)
        refresh_token = tokens["refresh_token"]

        # Save token to encrypted database storage
        save_epic_token(storage, refresh_token)
        logger.info("Successfully connected Epic Games account")

        return {
            "success": True,
            "message": "Epic Games account connected successfully! You can now sync your Epic library.",
        }

    except EpicAuthError as error:
        logger.warning("Epic Games auth error: %s", error)
        raise HTTPException(
            status_code=400, detail="Epic Games authentication failed"
        ) from error
    except Exception as error:
        logger.error("Unexpected error during Epic Games token exchange", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Unexpected error during Epic Games authentication",
        ) from error


@router.delete("/epic/token")
async def disconnect_epic(user_id: int = Query(1, ge=1)) -> dict[str, Any]:
    """Disconnect Epic Games by deleting the stored refresh token.

    Mirrors the CLI `auth disconnect --source epic` command.
    """
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    deleted = storage.delete_credential(user_id, "epic_games", "refresh_token")
    if not deleted:
        raise HTTPException(
            status_code=404, detail="No active Epic Games connection found"
        )
    logger.info("Disconnected Epic Games account for user %s", user_id)
    return {"success": True, "message": "Epic Games disconnected."}
