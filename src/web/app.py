"""FastAPI application for web interface."""

import logging
import os
import sys
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
    resolve_config_path,
)
from src.conversation.engine import create_conversation_engine
from src.web.api import router as api_router
from src.web.chat_api import router as chat_router
from src.web.state import app_state

logger = logging.getLogger(__name__)


def configure_logging(config: dict) -> None:
    """Configure logging from application config.

    Args:
        config: Application configuration dictionary
    """
    logging_config = config.get("logging", {})
    log_level_str = logging_config.get("level", "INFO").upper()
    log_file = logging_config.get("file", "logs/recommendations.log")

    # Map string to logging level
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Create log directory if needed
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger with both file and console handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicates on reload
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # File handler with detailed format
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)
    file_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)

    # Console handler with simpler format (for Docker logs)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_format = logging.Formatter("%(levelname)s | %(name)s | %(message)s")
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)


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
    except FileNotFoundError as error:
        logger.error(f"Config file not found: {error}")
        raise

    # Configure logging from config
    configure_logging(config)
    logger.info("Logging configured from application config")

    # Initialize components
    try:
        storage = create_storage_manager(config)
        llm_client, embedding_gen, rec_gen = create_llm_components(config)
        engine = create_recommendation_engine(storage, embedding_gen, rec_gen, config)

        # Determine actual config path used
        try:
            actual_config_path = resolve_config_path(config_path)
        except FileNotFoundError:
            actual_config_path = config_path or Path("config/config.yaml")

        # Store in app state
        app_state["config"] = config
        app_state["config_path"] = str(actual_config_path.resolve())
        app_state["storage"] = storage
        app_state["embedding_gen"] = embedding_gen
        app_state["rec_gen"] = rec_gen
        app_state["engine"] = engine
        app_state["ollama_client"] = llm_client

        # Initialize conversation engine if LLM is available
        if llm_client:
            conversation_engine = create_conversation_engine(
                storage_manager=storage,
                ollama_client=llm_client,
                recommendation_engine=engine,
                conversation_config=config.get("conversation"),
            )
            app_state["conversation_engine"] = conversation_engine
            logger.info("Conversation engine initialized")
        else:
            app_state["conversation_engine"] = None
            logger.info("Conversation engine not available (LLM disabled)")
    except Exception as error:
        logger.error(f"Failed to initialize components: {error}")
        raise

    # Create FastAPI app
    app = FastAPI(
        title="Personal Recommendations API",
        description="API for personalized content recommendations",
        version="1.0.0",
    )

    # Configure CORS (default to localhost only)
    web_config = config.get("web", {})
    allowed_origins = web_config.get("allowed_origins", ["http://localhost:18473"])

    # Disable credentials when wildcard origin is used (browser requirement)
    allow_credentials = "*" not in allowed_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routers
    app.include_router(api_router)
    app.include_router(chat_router)

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
