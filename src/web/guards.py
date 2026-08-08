"""Availability guards shared by the API routers.

An absent component is unavailability (503), not a server fault (500). Only
``conversation_engine`` is absent on a running server, when the LLM is
disabled; ``create_app`` populates the rest or raises, so their guards defend
that invariant rather than a state the app reaches today. Every guard lives
here rather than in one router because the same server state has to read the
same on every endpoint: one status code and one message per dependency.

Handlers ask for a component through the ``Required*`` aliases below rather
than by calling a guard: a guard that IS the parameter cannot be forgotten
while the handler still compiles, and which components an endpoint needs is
then readable off its signature. FastAPI caches a dependency for the life of a
request, so a handler and one of its own dependencies both asking for storage
acquire it once.

The component classes are imported for real rather than under ``TYPE_CHECKING``
because the ``Required*`` aliases are runtime assignments, not annotations:
``Annotated[StorageManager, ...]`` is evaluated when this module is imported.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, TypeVar

from fastapi import Depends, HTTPException

from src.conversation.engine import ConversationEngine
from src.conversation.memory import MemoryManager
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.state import (
    get_config,
    get_conversation_engine,
    get_engine,
    get_memory_manager,
    get_storage,
    locked_running_config,
)

T = TypeVar("T")

_CONFIG_UNAVAILABLE = "Config unavailable"


def _require(component: T | None, detail: str) -> T:
    if component is None:
        raise HTTPException(status_code=503, detail=detail)
    return component


def require_storage() -> StorageManager:
    """Return the storage manager, or 503 when it is not initialised."""
    return _require(get_storage(), "Storage unavailable")


def require_config() -> dict[str, Any]:
    """Return the running config, or 503 when it is not loaded."""
    return _require(get_config(), _CONFIG_UNAVAILABLE)


@contextmanager
def writable_config() -> Iterator[dict[str, Any]]:
    """Yield the running config to write into, with the config lock held.

    ``RequiredConfig`` cannot serve a writer. A dependency resolves and is
    released before the handler body runs, and ``reload_config`` rebinds
    ``app_state.config`` wholesale, so the dict the dependency handed over can
    be the one nobody reads any more by the time the write lands. Resolving the
    binding inside the lock is what makes a writer's read and its store one
    operation.

    A route using this still declares ``Depends(require_config)`` so config
    being down is answered 503 before the body — and before request validation
    — runs, exactly as it is on every reader.
    """
    with locked_running_config() as config:
        yield _require(config, _CONFIG_UNAVAILABLE)


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


RequiredStorage = Annotated[StorageManager, Depends(require_storage)]
RequiredConfig = Annotated[dict[str, Any], Depends(require_config)]
RequiredEngine = Annotated[RecommendationEngine, Depends(require_engine)]
RequiredMemoryManager = Annotated[MemoryManager, Depends(require_memory_manager)]
RequiredConversationEngine = Annotated[
    ConversationEngine, Depends(require_conversation_engine)
]
