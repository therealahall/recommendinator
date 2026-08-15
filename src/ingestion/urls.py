"""Shape checks for the base URL a network-backed source plugin is pointed at."""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: The port a scheme implies, so ``http://host`` and ``http://host:80`` read as
#: the same endpoint rather than as two.
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UnreadableUrl(ValueError):
    """A url whose host and port cannot be read.

    Distinct from a url addressing nobody: two unreadable urls are not each
    other's party, so a caller comparing them refuses instead of matching.
    """


class UrlOrigin(NamedTuple):
    """The party a url addresses."""

    scheme: str
    host: str
    port: int | None


def url_origin(value: str) -> UrlOrigin | None:
    """The party *value* addresses, ``None`` when it addresses nobody.

    The scheme's default port is folded away, so ``http://host`` and
    ``http://host:80`` name one party rather than two.

    Raises:
        UnreadableUrl: *value* cannot be parsed.
    """
    try:
        parts = urlsplit(value)
        hostname, port = parts.hostname, parts.port
    except ValueError as error:
        raise UnreadableUrl(f"{value!r} cannot be parsed as a url") from error
    if not hostname:
        return None
    return UrlOrigin(
        parts.scheme,
        hostname,
        None if port == _DEFAULT_PORTS.get(parts.scheme) else port,
    )


def source_url_error(value: str) -> str | None:
    """Return why *value* is unusable as a source base URL, or None.

    ``file://`` would read the server's own disk, and a ``user:pass@host``
    prefix hands those credentials to whatever host follows it. An unreadable
    url is refused rather than stored.
    """
    try:
        parts = urlsplit(value)
        username, password = parts.username, parts.password
        origin = url_origin(value)
    except ValueError:
        return f"'url' is not a valid URL: {value}"

    if parts.scheme not in _ALLOWED_SCHEMES:
        return "'url' must start with http:// or https://"
    if origin is None:
        return "'url' must name a host"
    if username or password:
        return "'url' must not embed a username or password"
    return None
