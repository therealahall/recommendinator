"""Availability guards shared by the API routers.

An absent component is unavailability (503), not a server fault (500). Only
``conversation_engine`` is absent on a running server, when the LLM is
disabled; ``create_app`` populates the rest or raises, so their guards defend
that invariant rather than a state the app reaches today. Every guard lives
here rather than in one router because the same server state has to read the
same on every endpoint: one status code and one message per dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from fastapi import HTTPException

from src.web.state import (
    get_config,
    get_conversation_engine,
    get_engine,
    get_memory_manager,
    get_storage,
)

if TYPE_CHECKING:
    from src.conversation.engine import ConversationEngine
    from src.conversation.memory import MemoryManager
    from src.recommendations.engine import RecommendationEngine
    from src.storage.manager import StorageManager

T = TypeVar("T")


def _require(component: T | None, detail: str) -> T:
    if component is None:
        raise HTTPException(status_code=503, detail=detail)
    return component


def require_storage() -> StorageManager:
    """Return the storage manager, or 503 when it is not initialised."""
    return _require(get_storage(), "Storage unavailable")


def require_config() -> dict[str, Any]:
    """Return the running config, or 503 when it is not loaded."""
    return _require(get_config(), "Config unavailable")


def require_engine() -> RecommendationEngine:
    """Return the recommendation engine, or 503 when it is not initialised."""
    return _require(get_engine(), "Recommendation engine unavailable")


def require_memory_manager() -> MemoryManager:
    """Return the conversation memory manager, or 503 when it is not initialised."""
    return _require(get_memory_manager(), "Memory manager unavailable")


def require_conversation_engine() -> ConversationEngine:
    """Return the chat engine, or 503 when the LLM is not configured."""
    return _require(
        get_conversation_engine(), "Chat is not available. LLM is not configured."
    )
