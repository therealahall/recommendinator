"""Shape checks for the base URL a network-backed source plugin is pointed at."""

from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def source_url_error(value: str) -> str | None:
    """Return why *value* is unusable as a source base URL, or None.

    ``file://`` would read the server's own disk, and a ``user:pass@host``
    prefix hands those credentials to whatever host follows it.
    """
    try:
        parts = urlsplit(value)
        username, password, hostname = parts.username, parts.password, parts.hostname
    except ValueError:
        return f"'url' is not a valid URL: {value}"

    if parts.scheme not in _ALLOWED_SCHEMES:
        return "'url' must start with http:// or https://"
    if not hostname:
        return "'url' must name a host"
    if username or password:
        return "'url' must not embed a username or password"
    return None
