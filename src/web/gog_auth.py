"""GOG OAuth authentication service for web UI.

Handles the OAuth flow for connecting GOG accounts:
1. Generate auth URL for user to visit
2. Exchange authorization code for tokens
3. Update config.yaml with refresh token
"""

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
import yaml

from src.ingestion.sources.gog import GOG_CLIENT_ID, GOG_CLIENT_SECRET

logger = logging.getLogger(__name__)

GOG_AUTH_URL = "https://auth.gog.com/auth"
GOG_TOKEN_URL = "https://auth.gog.com/token"
GOG_REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"


class GogAuthError(Exception):
    """Exception raised for GOG authentication errors."""

    pass


def get_gog_auth_url() -> str:
    """Generate the GOG OAuth authorization URL.

    Returns:
        URL for user to visit to authorize the app.
    """
    params = (
        f"client_id={GOG_CLIENT_ID}"
        f"&redirect_uri={GOG_REDIRECT_URI}"
        "&response_type=code"
        "&layout=client2"
    )
    return f"{GOG_AUTH_URL}?{params}"


def extract_code_from_input(user_input: str) -> str:
    """Extract authorization code from user input.

    User can paste either:
    - Just the code
    - The full redirect URL containing the code

    Args:
        user_input: Code or URL pasted by user.

    Returns:
        Extracted authorization code.

    Raises:
        GogAuthError: If code cannot be extracted.
    """
    user_input = user_input.strip()

    # Check if it's a URL
    if user_input.startswith("http"):
        parsed = urlparse(user_input)
        query_params = parse_qs(parsed.query)
        if "code" in query_params:
            return query_params["code"][0]
        raise GogAuthError(
            "URL does not contain a 'code' parameter. "
            "Make sure you copied the full redirect URL after logging in."
        )

    # Assume it's the raw code
    if len(user_input) < 20:
        raise GogAuthError(
            "Input appears too short to be a valid authorization code. "
            "Please copy the full code or URL."
        )

    return user_input


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Exchange authorization code for access and refresh tokens.

    Args:
        code: Authorization code from OAuth redirect.

    Returns:
        Token response dict with access_token, refresh_token, etc.

    Raises:
        GogAuthError: If token exchange fails.
    """
    params = {
        "client_id": GOG_CLIENT_ID,
        "client_secret": GOG_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": GOG_REDIRECT_URI,
    }

    try:
        response = requests.get(GOG_TOKEN_URL, params=params, timeout=30)

        if not response.ok:
            logger.error(
                "GOG token exchange failed with status %d", response.status_code
            )
            raise GogAuthError(
                "Token exchange failed. Please try again or check your authorization code."
            )

        data: dict[str, Any] = response.json()

        if "refresh_token" not in data:
            raise GogAuthError("Response missing refresh_token")

        return data

    except requests.RequestException as error:
        logger.error("GOG token exchange request failed", exc_info=True)
        raise GogAuthError("Failed to connect to GOG servers") from error


def update_config_with_token(config_path: Path, refresh_token: str) -> None:
    """Update config.yaml with GOG refresh token.

    Preserves existing config structure and comments where possible.

    Args:
        config_path: Path to config.yaml file.
        refresh_token: GOG refresh token to save.

    Raises:
        GogAuthError: If config update fails.
    """
    if not config_path.exists():
        logger.error("Config file not found at expected path")
        raise GogAuthError("Config file not found")

    try:
        content = config_path.read_text()

        # Try to update in-place using regex to preserve formatting
        # Look for refresh_token under gog section
        pattern = r"(gog:.*?refresh_token:\s*)[\"']?[^\"'\n]*[\"']?"
        replacement = rf'\1"{refresh_token}"'

        new_content, count = re.subn(pattern, replacement, content, flags=re.DOTALL)

        if count > 0:
            config_path.write_text(new_content)
            logger.info("Updated GOG refresh_token in config.yaml")
            return

        # If regex didn't work, use YAML library
        with open(config_path) as file:
            config = yaml.safe_load(file)

        if config is None:
            config = {}

        if "inputs" not in config:
            config["inputs"] = {}

        if "gog" not in config["inputs"]:
            config["inputs"]["gog"] = {}

        config["inputs"]["gog"]["refresh_token"] = refresh_token
        config["inputs"]["gog"]["enabled"] = True

        with open(config_path, "w") as file:
            yaml.dump(config, file, default_flow_style=False, sort_keys=False)

        logger.info("Updated GOG configuration in config.yaml")

    except Exception as error:
        logger.error("Failed to update config: %s", error, exc_info=True)
        raise GogAuthError("Failed to update config file") from error


def is_gog_enabled(config: dict[str, Any]) -> bool:
    """Check if GOG is enabled in config.

    Args:
        config: Application config dict.

    Returns:
        True if GOG is enabled (has enabled: true in inputs.gog).
    """
    inputs: dict[str, Any] = config.get("inputs", {})
    gog_config: dict[str, Any] = inputs.get("gog", {})
    return bool(gog_config.get("enabled", False))


def has_gog_token(config: dict[str, Any]) -> bool:
    """Check if GOG has a refresh token configured.

    Args:
        config: Application config dict.

    Returns:
        True if a non-empty refresh_token is configured.
    """
    inputs = config.get("inputs", {})
    gog_config = inputs.get("gog", {})
    token = gog_config.get("refresh_token", "")
    return bool(token and token.strip())
