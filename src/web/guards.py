"""An absent component is unavailability (503), not a server fault (500)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, TypeVar

from fastapi import Depends, HTTPException

from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.state import (
    get_config,
    get_engine,
    get_storage,
    locked_running_config,
)

T = TypeVar("T")

_CONFIG_UNAVAILABLE = "Config unavailable"

#: Exported because authentication answers with it too, and one server state
#: has to read the same way on every route.
STORAGE_UNAVAILABLE = "Storage unavailable"


def _require(component: T | None, detail: str) -> T:
    if component is None:
        raise HTTPException(status_code=503, detail=detail)
    return component


def require_storage() -> StorageManager:
    return _require(get_storage(), STORAGE_UNAVAILABLE)


def require_config() -> dict[str, Any]:
    return _require(get_config(), _CONFIG_UNAVAILABLE)


@contextmanager
def writable_config() -> Iterator[dict[str, Any]]:
    """``RequiredConfig`` cannot serve a writer."""
    with locked_running_config() as config:
        yield _require(config, _CONFIG_UNAVAILABLE)


def require_engine() -> RecommendationEngine:
    return _require(get_engine(), "Recommendation engine unavailable")


RequiredStorage = Annotated[StorageManager, Depends(require_storage)]
RequiredConfig = Annotated[dict[str, Any], Depends(require_config)]
RequiredEngine = Annotated[RecommendationEngine, Depends(require_engine)]
