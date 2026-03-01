"""Chat and memory API endpoints."""

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.conversation.profile import ProfileGenerator
from src.models.content import ContentType
from src.web.state import get_conversation_engine, get_memory_manager, get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


# ============================================================================
# Request/Response Models
# ============================================================================


class ChatRequest(BaseModel):
    """Request model for chat message."""

    user_id: int = Field(default=1, description="User ID")
    message: str = Field(..., max_length=5000, description="User's message")
    content_type: str | None = Field(
        None,
        description="Optional content type filter (book, movie, tv_show, video_game)",
    )


class MessageResponse(BaseModel):
    """Response model for a conversation message."""

    id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    created_at: datetime


class MemoryResponse(BaseModel):
    """Response model for a core memory."""

    id: int
    memory_text: str
    memory_type: str
    confidence: float
    is_active: bool
    source: str
    created_at: datetime


class MemoryCreateRequest(BaseModel):
    """Request model for creating a memory."""

    user_id: int = Field(default=1, description="User ID")
    memory_text: str = Field(
        ..., max_length=2000, description="The memory/preference text"
    )


class MemoryUpdateRequest(BaseModel):
    """Request model for updating a memory."""

    memory_text: str | None = Field(
        None, max_length=2000, description="New memory text"
    )
    is_active: bool | None = Field(None, description="Whether memory is active")


class ProfileResponse(BaseModel):
    """Response model for preference profile."""

    user_id: int
    genre_affinities: dict[str, float]
    theme_preferences: list[str]
    anti_preferences: list[str]
    cross_media_patterns: list[str]
    generated_at: datetime | None = None


# ============================================================================
# Chat Endpoints
# ============================================================================


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Process a chat message and stream the response.

    Uses Server-Sent Events (SSE) for streaming responses.

    Returns chunks in SSE format:
    - data: {"type": "text", "content": "..."}
    - data: {"type": "tool_call", "tool": "...", "params": {...}}
    - data: {"type": "tool_result", "result": {...}}
    - data: {"type": "done"}
    """
    engine = get_conversation_engine()
    if not engine:
        raise HTTPException(
            status_code=503,
            detail="Chat is not available. LLM is not configured.",
        )

    # Parse content type if provided
    content_type = None
    if request.content_type:
        try:
            content_type = ContentType(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    def generate_sse() -> Iterator[str]:
        """Generate SSE events from conversation chunks."""
        try:
            for chunk in engine.process_message(
                user_id=request.user_id,
                message=request.message,
                content_type=content_type,
                stream=True,
            ):
                event: dict[str, Any]
                if chunk.chunk_type == "text":
                    event = {"type": "text", "content": chunk.content}
                elif chunk.chunk_type == "tool_call":
                    event = {
                        "type": "tool_call",
                        "tool": chunk.tool_name,
                        "params": chunk.tool_params,
                    }
                elif chunk.chunk_type == "tool_result":
                    result_data = None
                    if chunk.tool_result:
                        result_data = {
                            "success": chunk.tool_result.success,
                            "message": chunk.tool_result.message,
                            "data": chunk.tool_result.data,
                            "needs_clarification": chunk.tool_result.needs_clarification,
                        }
                    event = {
                        "type": "tool_result",
                        "tool": chunk.tool_name,
                        "result": result_data,
                    }
                elif chunk.chunk_type == "memory_extracted":
                    event = {
                        "type": "memory",
                        "memory": {
                            "text": chunk.memory.memory_text if chunk.memory else None,
                            "type": chunk.memory.memory_type if chunk.memory else None,
                        },
                    }
                elif chunk.chunk_type == "done":
                    event = {"type": "done"}
                else:
                    continue  # type: ignore[unreachable]

                yield f"data: {json.dumps(event)}\n\n"

        except Exception:
            logger.error("Chat streaming error", exc_info=True)
            error_event = {"type": "error", "message": "An internal error occurred"}
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/chat/reset")
async def reset_chat(user_id: int = Query(default=1)) -> dict:
    """Reset conversation history for a user.

    This clears the chat history but preserves core memories.
    """
    engine = get_conversation_engine()
    if not engine:
        raise HTTPException(
            status_code=503,
            detail="Chat is not available. LLM is not configured.",
        )

    deleted_count = engine.reset_conversation(user_id)
    return {
        "success": True,
        "message": f"Cleared {deleted_count} messages from conversation history",
        "deleted_count": deleted_count,
    }


@router.get("/chat/history")
async def get_chat_history(
    user_id: int = Query(default=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MessageResponse]:
    """Get recent conversation history for a user."""
    memory_manager = get_memory_manager()
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Storage not available")

    messages = memory_manager.get_conversation_history(user_id=user_id, limit=limit)

    return [
        MessageResponse(
            id=msg.id or 0,
            role=msg.role,
            content=msg.content,
            tool_calls=msg.tool_calls,
            created_at=msg.created_at or datetime.now(),
        )
        for msg in messages
    ]


# ============================================================================
# Memory Endpoints
# ============================================================================


@router.get("/memories")
async def get_memories(
    user_id: int = Query(default=1),
    include_inactive: bool = Query(default=False),
) -> list[MemoryResponse]:
    """Get user's core memories."""
    memory_manager = get_memory_manager()
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Storage not available")

    memories = memory_manager.get_core_memories(
        user_id=user_id,
        active_only=not include_inactive,
    )

    return [
        MemoryResponse(
            id=mem.id or 0,
            memory_text=mem.memory_text,
            memory_type=mem.memory_type,
            confidence=mem.confidence,
            is_active=mem.is_active,
            source=mem.source,
            created_at=mem.created_at or datetime.now(),
        )
        for mem in memories
    ]


@router.post("/memories")
async def create_memory(request: MemoryCreateRequest) -> MemoryResponse:
    """Create a new user-stated memory."""
    memory_manager = get_memory_manager()
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Storage not available")

    memory = memory_manager.save_core_memory(
        user_id=request.user_id,
        memory_text=request.memory_text,
        memory_type="user_stated",
        source="manual",
        confidence=1.0,
    )

    return MemoryResponse(
        id=memory.id or 0,
        memory_text=memory.memory_text,
        memory_type=memory.memory_type,
        confidence=memory.confidence,
        is_active=memory.is_active,
        source=memory.source,
        created_at=memory.created_at or datetime.now(),
    )


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: int,
    request: MemoryUpdateRequest,
) -> dict:
    """Update a memory (edit text or toggle active status)."""
    memory_manager = get_memory_manager()
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Storage not available")

    # Update the memory
    success = memory_manager.update_core_memory(
        memory_id=memory_id,
        memory_text=request.memory_text,
        is_active=request.is_active,
    )

    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {"success": True, "message": "Memory updated"}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    """Delete a memory."""
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=503, detail="Storage not available")

    # Use storage directly since MemoryManager doesn't have delete
    success = storage.delete_core_memory(memory_id)

    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {"success": True, "message": "Memory deleted"}


# ============================================================================
# Profile Endpoints
# ============================================================================


@router.get("/profile")
async def get_profile(user_id: int = Query(default=1)) -> ProfileResponse:
    """Get user's preference profile summary."""
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=503, detail="Storage not available")

    # Try to get existing profile
    profile_data = storage.get_preference_profile(user_id)

    if profile_data:
        # Parse the stored JSON profile (nested under "profile" key)
        profile = profile_data.get("profile", {})
        return ProfileResponse(
            user_id=user_id,
            genre_affinities=profile.get("genre_affinities", {}),
            theme_preferences=profile.get("theme_preferences", []),
            anti_preferences=profile.get("anti_preferences", []),
            cross_media_patterns=profile.get("cross_media_patterns", []),
            generated_at=(
                datetime.fromisoformat(profile["generated_at"])
                if profile.get("generated_at")
                else None
            ),
        )

    # No profile exists, return empty profile
    return ProfileResponse(
        user_id=user_id,
        genre_affinities={},
        theme_preferences=[],
        anti_preferences=[],
        cross_media_patterns=[],
        generated_at=None,
    )


@router.post("/profile/regenerate")
async def regenerate_profile(user_id: int = Query(default=1)) -> ProfileResponse:
    """Force regeneration of the preference profile."""
    storage = get_storage()
    if not storage:
        raise HTTPException(status_code=503, detail="Storage not available")

    generator = ProfileGenerator(storage)
    profile = generator.regenerate_and_save(user_id)

    return ProfileResponse(
        user_id=profile.user_id,
        genre_affinities=profile.genre_affinities,
        theme_preferences=profile.theme_preferences,
        anti_preferences=profile.anti_preferences,
        cross_media_patterns=profile.cross_media_patterns,
        generated_at=profile.generated_at,
    )
