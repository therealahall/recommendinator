"""FastAPI application for web interface."""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.cli.config import (
    load_config,
    create_storage_manager,
    create_llm_components,
    create_recommendation_engine,
)
from src.web.state import app_state

logger = logging.getLogger(__name__)

# Module-level app instance for uvicorn import string support
_app: Optional[FastAPI] = None


def create_app(config_path: Optional[Path] = None) -> FastAPI:
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
    async def root():
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
    from the CONFIG_PATH environment variable, or default to
    config/example.yaml if not set.
    
    Returns:
        FastAPI application instance
    """
    global _app
    # Always recreate when called (allows reload to work properly)
    # Get config path from environment or use default
    config_path_str = os.environ.get("CONFIG_PATH")
    config_path = Path(config_path_str) if config_path_str else None
    if config_path is None:
        default_config = Path("config/example.yaml")
        if default_config.exists():
            config_path = default_config
    _app = create_app(config_path)
    return _app


# Create app instance for uvicorn import string support
app = get_app()
