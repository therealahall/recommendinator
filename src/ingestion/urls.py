"""Shape checks for the base URL a network-backed source plugin is pointed at."""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple
from urllib.parse import urlsplit

from src.utils.text import sanitize_for_log

_ALLOWED_SCHEMES = frozenset({"http", "https"})

REQUEST_TIMEOUT = 30

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

MAX_SAME_ORIGIN_REDIRECTS = 5

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _non_default_port(scheme: str, port: int | None) -> int | None:
    return None if port == _DEFAULT_PORTS.get(scheme) else port


class NoOrigin(Enum):
    ADDRESSES_NOBODY = "addresses_nobody"
    UNREADABLE = "unreadable"


class CredentialHost(NamedTuple):
    host: str
    port: int | None


class UrlOrigin(NamedTuple):
    scheme: str
    host: str
    port: int | None

    @property
    def credential_host(self) -> CredentialHost:
        return CredentialHost(self.host, self.port)


def url_origin(value: str) -> UrlOrigin | NoOrigin:
    """The party *value* addresses, or why it addresses none."""
    try:
        parts = urlsplit(value)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return NoOrigin.UNREADABLE
    if not hostname:
        return NoOrigin.ADDRESSES_NOBODY
    return UrlOrigin(parts.scheme, hostname, _non_default_port(parts.scheme, port))


def same_origin(url: str, target: str) -> bool:
    origin = url_origin(url)
    return isinstance(origin, UrlOrigin) and url_origin(target) == origin


def redirect_refusal(url: str, target: str, service: str) -> str:
    origin = urlsplit(url)
    safe_target = sanitize_for_log(target)
    return (
        f"Refused a redirect from {url} to {safe_target}. It leaves the configured "
        f"origin {origin.scheme}://{origin.netloc}, and the api key only goes where "
        f"the source url points. If {service} really is at {safe_target}, set the "
        "source url to it (and verify_ssl to false if its certificate is not "
        "publicly trusted)."
    )


def source_url_error(value: str) -> str | None:
    """``file://`` would read the server's own disk, and a ``user:pass@host``
    prefix hands those credentials to whatever host follows it.
    """
    origin = url_origin(value)
    if origin is NoOrigin.UNREADABLE:
        return f"'url' is not a valid URL: {value}"

    parts = urlsplit(value)
    if parts.scheme not in _ALLOWED_SCHEMES:
        return "'url' must start with http:// or https://"
    if origin is NoOrigin.ADDRESSES_NOBODY:
        return "'url' must name a host"
    if parts.username or parts.password:
        return "'url' must not embed a username or password"
    return None
