from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src import __version__ as APP_VERSION
from src.utils.dependencies import PackageDrift, dependency_drift
from src.web.api._shared import RecommendationsConfig, _get_recommendations_config
from src.web.state import get_config, get_engine, get_storage, reload_config

router = APIRouter()


class StatusResponse(BaseModel):
    status: str
    version: str
    components: dict[str, bool]
    recommendations_config: RecommendationsConfig = Field(
        default_factory=RecommendationsConfig
    )
    dependency_drift: list[PackageDrift] = Field(default_factory=list)


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    engine = get_engine()
    storage = get_storage()
    config = get_config()

    components = {
        "engine": engine is not None,
        "storage": storage is not None,
    }

    all_ready = all(components.values())

    return StatusResponse(
        status="ready" if all_ready else "initializing",
        version=APP_VERSION,
        components=components,
        recommendations_config=_get_recommendations_config(config),
        dependency_drift=list(dependency_drift()),
    )


@router.post("/config/reload")
def reload_config_endpoint() -> dict[str, Any]:
    success = reload_config()
    if success:
        return {"success": True, "message": "Configuration reloaded successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reload configuration")
