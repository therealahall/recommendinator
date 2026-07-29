"""FastAPI application for web interface."""

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.cli.config import (
    create_llm_components,
    create_recommendation_engine,
    create_storage_manager,
    load_config,
    resolve_bootstrap_web,
    resolve_config_path,
)
from src.conversation.engine import create_conversation_engine
from src.conversation.memory import MemoryManager
from src.settings.metadata import default_of
from src.storage.credential_migration import migrate_config_credentials
from src.storage.global_secrets import migrate_config_secrets
from src.storage.settings_migration import migrate_config_settings
from src.storage.source_migration import (
    migrate_source_config_plugins,
    migrate_source_labels,
)
from src.web.api import APP_VERSION
from src.web.api import router as api_router
from src.web.chat_api import router as chat_router
from src.web.state import app_state
from src.web.upload_limit import (
    MAX_REQUEST_BODY_BYTES,
    RequestBodySizeLimitMiddleware,
)

logger = logging.getLogger(__name__)

# Every log file must live under this directory. ``logging.file`` is settable
# over the network Settings API, so a resolved path escaping this base is
# refused before a FileHandler ever opens it (see ``_safe_log_path``).
_LOG_BASE_DIR = Path("logs")

# The authoritative name -> level map, minus NOTSET. ``logging.NOTSET`` is a
# real name in that mapping but not a usable threshold: the root logger has no
# parent to inherit from, so level 0 enables every record — a DEBUG firehose
# written to disk from a value that reads like "off".
_LOG_LEVELS = {
    name: level
    for name, level in logging.getLevelNamesMapping().items()
    if level != logging.NOTSET
}


def _safe_log_path(log_file: str) -> Path:
    """Resolve *log_file*, refusing any path that escapes the ``logs/`` directory.

    ``logging.file`` is a network-settable string. The registry ``pattern`` now
    rejects traversal and absolute paths at the Settings API, but this backstop
    is still load-bearing, for three inputs the pattern never sees:
    ``config.yaml`` is unvalidated; rows persisted before the pattern gained its
    ``..`` lookahead still overlay onto config at boot without re-validation; and
    a symlink planted under ``logs/`` satisfies any pattern. Any path resolving
    outside ``logs/`` falls back to the registry default's file name inside
    ``logs/``, so logging never writes to an arbitrary location (fail safe).

    Args:
        log_file: Configured log file path (relative or absolute).

    Returns:
        The resolved, contained path, or the registry default's file name under
        ``logs/`` when the configured path escapes ``logs/``.
    """
    base = _LOG_BASE_DIR.resolve()
    resolved = Path(log_file).resolve()
    # ``base`` itself is excluded deliberately: `file: logs` names the directory,
    # which FileHandler cannot open (IsADirectoryError), not a log file.
    if base in resolved.parents:
        return resolved
    logger.warning(
        "Log file %r resolves outside the logs/ directory; using the default.",
        log_file,
    )
    # Built from ``base`` rather than resolving the default, so the fail-safe
    # branch cannot itself escape — via an absolute registry default or a
    # symlinked default file.
    return base / Path(default_of("logging.file")).name


def configure_logging(config: dict) -> None:
    """Configure logging from application config.

    Args:
        config: Application configuration dictionary
    """
    # Type-guarded like every other leaf read straight from YAML (see
    # resolve_bootstrap_web and the CORS block): config.yaml is unvalidated, and
    # both of these land inside create_app's try, so an unguarded `logging: 3`
    # or `level: 3` aborts boot with "Failed to initialize components" instead of
    # degrading. A bare `logging:` header parses to None, not {}, so the section
    # itself needs the guard too — .get would raise on None.
    raw_section = config.get("logging")
    logging_config = raw_section if isinstance(raw_section, dict) else {}

    raw_level = logging_config.get("level", default_of("logging.level"))
    if isinstance(raw_level, str) and raw_level.upper() in _LOG_LEVELS:
        log_level_str = raw_level.upper()
    else:
        log_level_str = default_of("logging.level")
        logger.warning(
            "Ignoring unusable logging.level %r in config.yaml; using %s instead. "
            "It must be one of: %s.",
            raw_level,
            log_level_str,
            ", ".join(sorted(_LOG_LEVELS)),
        )

    raw_file = logging_config.get("file", default_of("logging.file"))
    if isinstance(raw_file, str):
        log_file = raw_file
    else:
        log_file = default_of("logging.file")
        logger.warning(
            "Ignoring unusable logging.file %r in config.yaml; using %s instead. "
            "It must be a string.",
            raw_file,
            log_file,
        )

    log_level = _LOG_LEVELS[log_level_str]

    # Contain the (network-settable) log path under logs/ before opening it.
    log_path = _safe_log_path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger with both file and console handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicates on reload
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # File handler with detailed format
    file_handler = logging.FileHandler(log_path)
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


_app: FastAPI | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle — start/stop config file watcher."""
    if app_state.config_path:
        await app_state.config_watcher.start(Path(app_state.config_path))
    else:
        logger.warning(
            "Config watcher not started: no config_path in app_state. "
            "Hot-reload is disabled."
        )
    yield
    await app_state.config_watcher.stop()


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
        logger.error("Config file not found: %s", error)
        raise

    # Resolved from raw YAML BEFORE the database overlay, so a legacy `web.debug`
    # row in the settings table cannot open /docs here while src/web/main.py
    # (which calls the same resolver) believes it is closed.
    # warn=False: src/web/main.py resolves the same config a moment earlier and
    # already logged anything unusable. Warning again here would print every
    # bind diagnostic twice per launch, reading like two separate faults.
    debug_mode = resolve_bootstrap_web(config, warn=False).debug

    # Initialize components
    try:
        # Storage must come first: the effective global settings (incl. logging
        # and CORS origins) are assembled from const/YAML/DB layers before
        # anything reads them.
        storage = create_storage_manager(config)

        # Assemble the effective global config (const default < YAML < DB) so
        # the database wins over YAML for the rest of the process.
        migrate_config_settings(config, storage)

        # Configure logging from the (now DB-overlaid) config
        configure_logging(config)
        logger.info("Logging configured from application config")

        llm_client, embedding_gen, rec_gen = create_llm_components(config)
        engine = create_recommendation_engine(storage, embedding_gen, rec_gen, config)

        # Determine actual config path used
        try:
            actual_config_path = resolve_config_path(config_path)
        except FileNotFoundError:
            actual_config_path = config_path or Path("config/example.yaml")

        # Migrate sensitive config credentials to encrypted DB storage
        migrate_config_credentials(config, storage)
        # Relabel stored goodreads source values and plugin names after the
        # plugin rename
        migrate_source_labels(storage)
        migrate_source_config_plugins(storage)

        # Relocate global provider secrets (api keys) into encrypted storage,
        # stripping them from the in-memory plaintext config.
        migrate_config_secrets(config, storage)

        # Store in app state
        app_state.config = config
        app_state.config_path = str(actual_config_path.resolve())
        app_state.storage = storage
        app_state.embedding_gen = embedding_gen
        app_state.engine = engine
        app_state.ollama_client = llm_client

        # Initialize conversation engine if LLM is available
        if llm_client:
            conversation_engine = create_conversation_engine(
                storage_manager=storage,
                ollama_client=llm_client,
                recommendation_engine=engine,
                conversation_config=config.get("conversation"),
            )
            app_state.conversation_engine = conversation_engine
            logger.info("Conversation engine initialized")
        else:
            app_state.conversation_engine = None
            logger.info("Conversation engine not available (LLM disabled)")

        # Cache a shared MemoryManager instance
        app_state.memory_manager = MemoryManager(storage)
    except Exception as error:
        logger.error("Failed to initialize components: %s", error)
        raise

    # Configure web settings. Guarded independently of migrate_config_settings:
    # a `web:` header with no children parses to None, and dict.get's default
    # only fires on an ABSENT key. The overlay does heal a non-dict section, but
    # relying on that is an undocumented cross-file ordering dependency, and this
    # read sits outside the try/except.
    raw_web_config = config.get("web")
    web_config = raw_web_config if isinstance(raw_web_config, dict) else {}

    # Create FastAPI app (debug_mode was resolved pre-overlay, above)
    app = FastAPI(
        title="Recommendinator API",
        description="API for personalized content recommendations",
        version=APP_VERSION,
        docs_url="/docs" if debug_mode else None,
        redoc_url="/redoc" if debug_mode else None,
        # Gated too: docs_url=None only removes the Swagger HTML page, leaving
        # the machine-readable schema — and the full route inventory — served at
        # /openapi.json. Swagger and ReDoc need it, so it tracks debug_mode.
        openapi_url="/openapi.json" if debug_mode else None,
        lifespan=lifespan,
    )

    # Bound the request body before Starlette's multipart parser can spool it
    # to disk. Added first so it ends up the innermost user middleware, which
    # leaves the CORS and security headers applied to its 413 as well.
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)

    # Configure CORS (default to localhost only).
    # Type-guarded because config.yaml is unvalidated: a blank `allowed_origins:`
    # yields None and `"*" not in None` raises outside the try/except, killing
    # boot with a bare traceback. Worse, a scalar string is passed straight to
    # Starlette, whose check is `origin in self.allow_origins` — a SUBSTRING test
    # on a string, so "https://app.example.co" would match a configured
    # "https://app.example.com". The DB path is already list-validated.
    raw_origins = web_config.get("allowed_origins")
    if isinstance(raw_origins, list) and all(
        isinstance(origin, str) for origin in raw_origins
    ):
        allowed_origins = raw_origins
    else:
        if "allowed_origins" in web_config:
            logger.warning(
                "Ignoring unusable web.allowed_origins %r; using the default. "
                "It must be a list of origin strings.",
                raw_origins,
            )
        allowed_origins = default_of("web.allowed_origins")

    # Disable credentials when wildcard origin is used (browser requirement)
    allow_credentials = "*" not in allowed_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["Content-Type", "Accept"],
    )

    # Security headers middleware
    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self, request: Request, call_next: RequestResponseEndpoint
        ) -> Response:
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "font-src 'self' data:; "
                "img-src 'self' data: https:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            return response

    app.add_middleware(SecurityHeadersMiddleware)

    # Include API routers
    app.include_router(api_router)
    app.include_router(chat_router)

    # Serve static files (for web UI)
    static_dir = Path("src/web/static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Serve the Vue SPA from Vite build output
    dist_index = Path("src/web/static/dist/index.html")

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        """Serve the main web UI.

        Serves the Vite-built SPA (dist/index.html) when present. Vite uses
        content-hashed filenames so no manual cache-busting is needed. When
        the SPA has not been built, returns a plain API-running message.
        """
        if dist_index.exists():
            return HTMLResponse(content=dist_index.read_text())
        # Only advertise /docs when it actually exists. docs_url/redoc_url/
        # openapi_url are all left unset unless debug_mode, so on a default
        # install this sentence pointed at a 404 — the sibling of the same fix
        # in src/web/main.py's startup banner.
        docs_hint = " Use /docs for API documentation." if debug_mode else ""
        return HTMLResponse(
            content=f"<h1>Recommendinator API</h1><p>API is running.{docs_hint}</p>"
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


def __getattr__(name: str) -> FastAPI:
    """Lazy module-level attribute for uvicorn import string support.

    Defers ``get_app()`` until ``app`` is actually accessed (e.g. by
    ``uvicorn src.web.app:app``) rather than running it at import time.
    This prevents test-collection imports from triggering production
    logging and full app initialisation as a side effect.
    """
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
