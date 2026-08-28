import argparse
import logging
import os
import socket
from pathlib import Path

import uvicorn

from src.config.service import load_config, resolve_bootstrap_web
from src.web.app import create_app

logger = logging.getLogger(__name__)

# Bind addresses that listen on every interface. The startup banner must report
# these as network-reachable rather than printing them literally, so an operator
# is never told an exposed instance is localhost-only.
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})


def get_local_ip_addresses() -> list[str]:
    addresses = []
    try:
        hostname = socket.gethostname()
        ip_list = socket.gethostbyname_ex(hostname)[2]
        # Filter out Docker networks and localhost
        for ip in ip_list:
            if not ip.startswith("172.") and not ip.startswith("127."):
                addresses.append(ip)
    except Exception:
        pass

    # Also try to get the default route IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in addresses:
            addresses.append(ip)
    except Exception:
        pass

    return addresses


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Recommendinator web server")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration file (default: config/config.yaml, falls back to example.yaml)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host to bind to (overrides config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind to (overrides config)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload (watches src/ and templates/); for development only",
    )

    args = parser.parse_args()

    config_path = args.config

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        config = {}

    # Shared with create_app so the two readers cannot disagree — see
    # resolve_bootstrap_web. CLI flags then override on top.
    bootstrap = resolve_bootstrap_web(config)
    host = args.host or bootstrap.host
    # `is not None` rather than `or`: --port 0 is a real request for an
    # ephemeral port, not a blank value to fall through.
    port = args.port if args.port is not None else bootstrap.port

    app = create_app(config_path)

    logger.info("Starting Recommendinator web server...")
    logger.info("Server will be accessible at:")
    if host in _WILDCARD_HOSTS:
        # Only a wildcard bind is reachable at localhost AND on the LAN.
        logger.info("  - http://localhost:%s", port)
        for ip in get_local_ip_addresses():
            logger.info("  - http://%s:%s", ip, port)
    else:
        # A socket bound to one specific address is NOT reachable at localhost,
        # so printing it unconditionally handed the operator a dead URL — above
        # the correct one.
        logger.info("  - http://%s:%s", host, port)
    # Only when debug is on: create_app leaves docs_url, redoc_url and
    # openapi_url unset otherwise, so advertising /docs on a default install
    # points the operator at a 404.
    if bootstrap.debug:
        logger.info(
            "API documentation: http://%s:%s/docs",
            "localhost" if host in _WILDCARD_HOSTS else host,
            port,
        )

    # Reload is enabled either by the explicit --reload flag (dev compose) or by web.debug
    # in config. uvicorn requires an import string when reload is enabled.
    reload_enabled = args.reload or bootstrap.debug

    if reload_enabled:
        if config_path:
            os.environ["CONFIG_PATH"] = str(config_path.resolve())
        # Resolve reload_dirs relative to the project root so --reload works
        # from any cwd (uvicorn would otherwise resolve them against $PWD).
        project_root = Path(__file__).resolve().parents[2]
        uvicorn.run(
            "src.web.app:app",
            host=host,
            port=port,
            log_level="info",
            reload=True,
            reload_dirs=[str(project_root / "src"), str(project_root / "templates")],
        )
    else:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            reload=False,
        )


if __name__ == "__main__":
    main()
