"""FastAPI application for web interface."""

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from math import isfinite
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.config.service import (
    create_recommendation_engine,
    create_storage_manager,
    load_config,
    resolve_bootstrap_web,
    resolve_config_path,
)
from src.settings.metadata import default_of
from src.storage.credential_migration import migrate_config_credentials
from src.storage.global_secrets import migrate_config_secrets
from src.storage.import_source_cleanup import drop_sources_replaced_by_upload
from src.storage.schema import get_default_user_id
from src.storage.settings_migration import migrate_config_settings
from src.utils import logging as log_config
from src.utils.text import exception_for_log
from src.web.api import APP_VERSION
from src.web.api import router as api_router
from src.web.auth import set_session_cookie
from src.web.auth_api import router as auth_router
from src.web.responses import SurrogateSafeJSONResponse
from src.web.scheduler import sync_scheduler
from src.web.state import app_state, get_config
from src.web.themes import (
    PRIVATE_THEMES_DIR,
    PRIVATE_THEMES_URL,
    STATIC_DIR,
    themed_shell,
)

logger = logging.getLogger(__name__)


def _quotable(value: float) -> float | str:
    return value if isfinite(value) else repr(value)


async def _validation_refusal_json_can_carry(
    _request: Request, exc: Exception
) -> JSONResponse:
    """Quote the rejected input back in a body ``json.dumps`` can write.

    Only the non-finite float needs quoting: ``json.dumps`` has no
    representation for it, while the response class encodes a lone surrogate.
    """
    # Starlette types every handler against bare Exception and dispatches this
    # one on the registered class alone.
    errors = cast(RequestValidationError, exc).errors()
    return SurrogateSafeJSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(errors, custom_encoder={float: _quotable})},
    )


async def _raised_refusal_json_can_carry(_request: Request, exc: Exception) -> Response:
    """Render the refusals an endpoint raises for itself.

    FastAPI renders ``HTTPException`` on a stock ``JSONResponse``, which
    ``default_response_class`` never reaches — so a detail quoting a rejected
    key back answered 500 when the key held a lone surrogate.
    """
    refusal = cast(HTTPException, exc)
    # A 204 or 304 may not carry a body, and FastAPI's handler honours that.
    if not is_body_allowed_for_status_code(refusal.status_code):
        return Response(status_code=refusal.status_code, headers=refusal.headers)
    return SurrogateSafeJSONResponse(
        status_code=refusal.status_code,
        content={"detail": refusal.detail},
        headers=refusal.headers,
    )


def _stored_theme_id() -> str:
    storage = app_state.storage
    return storage.ui_settings.get_theme(get_default_user_id()) if storage else ""


class PrivateThemeFiles(StaticFiles):
    async def check_config(self) -> None:
        """No check: an absent private/themes is a 404, and its mount is read-only."""


_app: FastAPI | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle — config file watcher and sync scheduler."""
    if app_state.config_path:
        await app_state.config_watcher.start(Path(app_state.config_path))
    else:
        logger.warning(
            "Config watcher not started: no config_path in app_state. "
            "Hot-reload is disabled."
        )
    await sync_scheduler.start()
    yield
    await sync_scheduler.stop()
    await app_state.config_watcher.stop()


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create and configure FastAPI application.

    Args:
        config_path: Optional path to configuration file

    Returns:
        Configured FastAPI application
    """
    try:
        config = load_config(config_path)
    except FileNotFoundError as error:
        logger.error("Config file not found: %s", exception_for_log(error))
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

        # Configure logging from the (now DB-overlaid) config. Reached through
        # the module so the root conftest's patch of the one definition holds
        # for every caller; stdout because that is what `docker logs` shows.
        log_config.configure_logging(
            config,
            console_stream=sys.stdout,
            console_tracebacks=True,
            # A server's console is its log viewer, so it takes what
            # ``logging.level`` names rather than a floor of its own.
            console_floor=logging.NOTSET,
        )
        # Left to the caller that runs a server: neither library is on the
        # CLI's import path, so quieting them in the shared configurer would
        # describe a dependency the CLI does not have.
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("watchfiles").setLevel(logging.WARNING)
        logger.info("Logging configured from application config")

        # get_config, not the dict: a hot-reload swaps in a fresh one, and the
        # engine must score against whichever is current at the time.
        engine = create_recommendation_engine(
            storage, config, config_provider=get_config
        )

        # Determine actual config path used
        try:
            actual_config_path = resolve_config_path(config_path)
        except FileNotFoundError:
            actual_config_path = config_path or Path("config/example.yaml")

        # Loud, but not fatal: refusing to start is what left the owner of an
        # instance locked out of it. The window closes the moment someone
        # completes setup, and only the first visitor can.
        if not storage.accounts.is_claimed():
            logger.warning(
                "No account on this instance yet — the first visitor to the web "
                "UI claims it. Open it and create yours before anyone else can."
            )

        # Nothing else deletes a lapsed session, so without this the table
        # grows for the life of the database.
        purged = storage.accounts.purge_expired_sessions()
        if purged:
            logger.info("Purged %d expired session(s)", purged)

        # Migrate sensitive config credentials to encrypted DB storage
        migrate_config_credentials(config, storage)

        # Relocate global provider secrets (api keys) into encrypted storage,
        # stripping them from the in-memory plaintext config.
        migrate_config_secrets(config, storage)

        drop_sources_replaced_by_upload(storage)

        # Store in app state
        app_state.config = config
        app_state.config_path = str(actual_config_path.resolve())
        app_state.storage = storage
        app_state.engine = engine
    except Exception as error:
        logger.error("Failed to initialize components: %s", exception_for_log(error))
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
        # Here rather than per endpoint: a stored lone surrogate reaches every
        # body echoing that row, and the rows already written — the case with
        # no door left to correct it by — no write-path check can reach.
        default_response_class=SurrogateSafeJSONResponse,
    )

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        # No allow_credentials: under SameSite=Strict the browser never
        # attaches the session cookie cross-origin, so a configured origin
        # reaches the ungated surface — the SPA shell and /static — and
        # nothing behind the gate. Turning it on would promise otherwise.
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        # A preflight naming a header that is absent fails before routing.
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

    # Middleware, not the dependency's own ``Response``: FastAPI merges those
    # headers only where it serialises a return value, so every route handing
    # back a ``Response`` — the export, the 204s — missed the re-issue.
    class RollingSessionCookieMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self, request: Request, call_next: RequestResponseEndpoint
        ) -> Response:
            response = await call_next(request)
            token = getattr(request.state, "session_token", None)
            if token:
                set_session_cookie(response, token)
            return response

    app.add_middleware(RollingSessionCookieMiddleware)

    # FastAPI supplies both of these, and both of its own render on a stock
    # JSONResponse that default_response_class never reaches. Keyed on
    # Starlette's HTTPException so the MRO walk catches FastAPI's subclass and
    # a 404 raised inside StaticFiles alike.
    app.add_exception_handler(
        RequestValidationError, _validation_refusal_json_can_carry
    )
    app.add_exception_handler(HTTPException, _raised_refusal_json_can_carry)

    # The session dependency rides on the routers themselves (see
    # src/web/auth.py). Nothing under /api is exempt but the four /api/auth
    # routes — including /api/status, whose component report is a free
    # fingerprint.
    app.include_router(api_router)
    app.include_router(auth_router)

    # Ahead of /static, which matches this prefix too. Resolved per request, so
    # a theme dropped in later needs no restart.
    app.mount(
        PRIVATE_THEMES_URL,
        PrivateThemeFiles(directory=PRIVATE_THEMES_DIR, check_dir=False),
        name="private-themes",
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    dist_index = STATIC_DIR / "dist" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        """Serve the Vue SPA, with the stored theme already linked in it.

        Vite content-hashes its filenames, so no manual cache-busting is needed.
        """
        if dist_index.exists():
            # Vite writes UTF-8 and the response declares it; reading it in the
            # locale's encoding answered 500 on the first accented byte.
            document = dist_index.read_text(encoding="utf-8")
            return HTMLResponse(content=themed_shell(document, _stored_theme_id()))
        # docs_url/redoc_url/openapi_url are unset unless debug_mode, so on a
        # default install advertising /docs pointed at a 404.
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
