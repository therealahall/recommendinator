"""Web server entry point for personal recommendations."""

import argparse
import logging
import socket
from pathlib import Path

import uvicorn

from src.web.app import create_app

logger = logging.getLogger(__name__)


def get_local_ip_addresses() -> list[str]:
    """Get list of local IP addresses (excluding Docker networks and IPv6).

    Returns:
        List of IP address strings
    """
    addresses = []
    try:
        # Get hostname
        hostname = socket.gethostname()
        # Get all IP addresses
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
    """Main entry point for web server."""
    parser = argparse.ArgumentParser(
        description="Start Personal Recommendations web server"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration file (default: config/example.yaml)",
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

    args = parser.parse_args()

    # Load config to get default host/port
    config_path = args.config
    if config_path is None:
        # Try to find config file
        default_config = Path("config/example.yaml")
        if default_config.exists():
            config_path = default_config
        else:
            logger.warning("No config file found, using defaults")

    # Load config first to get web settings
    from src.cli.config import load_config

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        config = {}

    web_config = config.get("web", {})
    host = args.host or web_config.get("host", "0.0.0.0")
    port = args.port or web_config.get("port", 8000)

    # Create app
    app = create_app(config_path)

    # Log accessible addresses
    logger.info("Starting Personal Recommendations web server...")
    logger.info("Server will be accessible at:")
    logger.info(f"  - http://localhost:{port}")
    if host == "0.0.0.0":
        local_ips = get_local_ip_addresses()
        for ip in local_ips:
            logger.info(f"  - http://{ip}:{port}")
    else:
        logger.info(f"  - http://{host}:{port}")
    logger.info(
        f"API documentation: http://{host if host != '0.0.0.0' else 'localhost'}:{port}/docs"
    )

    # Start server
    # Enable auto-reload in debug mode (watches for file changes)
    debug_mode = web_config.get("debug", False)

    # When reload is enabled, uvicorn requires an import string instead of app object
    if debug_mode:
        # Set config path in environment for the imported app to use
        import os

        if config_path:
            os.environ["CONFIG_PATH"] = str(config_path.resolve())
        # Use import string format for reload support
        uvicorn.run(
            "src.web.app:app",  # Import string format
            host=host,
            port=port,
            log_level="info",
            reload=True,
        )
    else:
        # When reload is disabled, we can pass the app object directly
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            reload=False,
        )


if __name__ == "__main__":
    main()
