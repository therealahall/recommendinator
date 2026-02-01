"""FastAPI application for web interface."""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.cli.config import (
    create_llm_components,
    create_recommendation_engine,
    create_storage_manager,
    load_config,
)
from src.web.state import app_state

logger = logging.getLogger(__name__)

# Module-level app instance for uvicorn import string support
_app: FastAPI | None = None


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create and configure FastAPI application.

    Args:
        config_path: Optional path to configuration file

    Returns:
        Configured FastAPI application
    """
    # Load configuration
    try:
        config = load_config(config_path)
    except FileNotFoundError as e:
        logger.error(f"Config file not found: {e}")
        raise

    # Initialize components
    try:
        storage = create_storage_manager(config)
        llm_client, embedding_gen, rec_gen = create_llm_components(config)
        engine = create_recommendation_engine(storage, embedding_gen, rec_gen, config)

        # Store in app state
        app_state["config"] = config
        app_state["storage"] = storage
        app_state["embedding_gen"] = embedding_gen
        app_state["rec_gen"] = rec_gen
        app_state["engine"] = engine
    except Exception as e:
        logger.error(f"Failed to initialize components: {e}")
        raise

    # Create FastAPI app
    app = FastAPI(
        title="Personal Recommendations API",
        description="API for personalized content recommendations",
        version="1.0.0",
    )

    # Configure CORS (internal network only)
    web_config = config.get("web", {})
    allowed_origins = web_config.get("allowed_origins", ["*"])

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API router (import here to avoid circular import)
    from src.web.api import router

    app.include_router(router)

    # Serve static files (for web UI)
    static_dir = Path("src/web/static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        """Serve the main web UI."""
        html_file = Path("src/web/templates/index.html")
        if html_file.exists():
            return HTMLResponse(content=html_file.read_text())
        return HTMLResponse(
            content="<h1>Personal Recommendations API</h1><p>API is running. Use /docs for API documentation.</p>"
        )

    return app


def get_app() -> FastAPI:
    """Get or create the FastAPI app instance.

    This function is used when running with uvicorn reload mode,
    which requires an import string. It will use the config path
    from the CONFIG_PATH environment variable, or let load_config()
    use its default logic (config/config.yaml -> config/example.yaml).

    Returns:
        FastAPI application instance
    """
    global _app
    # Always recreate when called (allows reload to work properly)
    # Get config path from environment, or None to let load_config() decide
    config_path_str = os.environ.get("CONFIG_PATH")
    config_path = Path(config_path_str) if config_path_str else None
    # Don't override with example.yaml - let load_config() handle defaults
    # (it correctly tries config/config.yaml first, then example.yaml)
    _app = create_app(config_path)
    return _app


# Create app instance for uvicorn import string support
app = get_app()
