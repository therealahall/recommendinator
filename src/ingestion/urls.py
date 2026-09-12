"""Shape checks for the URL a network-backed plugin is pointed at, and the
redirect walk every credentialed request to it makes."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, NamedTuple
from urllib.parse import urljoin, urlsplit

import requests

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


def _safe_host(netloc: str) -> str:
    """Server text names the host, and a hop can put the api key in front of it.

    ``.hostname``/``.port`` would be cleaner, but ``.port`` raises on a malformed
    port a hostile Location can supply, turning a refusal into a traceback.
    """
    _userinfo, _, host = netloc.rpartition("@")
    return host


def _loggable(value: str) -> str:
    """Server text, and a query string is itself a credential for some callers."""
    parts = urlsplit(value)
    return sanitize_for_log(f"{parts.scheme}://{_safe_host(parts.netloc)}{parts.path}")


def redirect_refusal(url: str, target: str, service: str) -> str:
    """For a URL the operator configured, so there is a setting to point at it."""
    origin = urlsplit(url)
    safe_origin = sanitize_for_log(f"{origin.scheme}://{_safe_host(origin.netloc)}")
    safe_target = _loggable(target)
    return (
        f"Refused a redirect from {_loggable(url)} to {safe_target}. It leaves the "
        f"configured origin {safe_origin}, and the api key only goes where the "
        f"source url points. If {service} really is at {safe_target}, set the "
        "source url to it (and verify_ssl to false if its certificate is not "
        "publicly trusted)."
    )


def fixed_endpoint_refusal(url: str, target: str, service: str) -> str:
    """For a service's own API URL, which no setting points elsewhere: naming one
    would send an operator hunting a setting that does not exist."""
    return (
        f"Refused a redirect from {_loggable(url)} to {_loggable(target)}: it "
        f"leaves the origin the {service} credentials are sent to."
    )


class RedirectRefused(Exception):
    """Raised by the walk below, for the caller to restate as its own error."""


def request_within_origin(
    send: Callable[..., requests.Response],
    url: str,
    service: str,
    refusal: Callable[[str, str, str], str],
    **sent: Any,
) -> requests.Response:
    """What *url* answers, following only a redirect that stays on its origin:
    ``requests`` replays an Authorization header — and an api key in the query
    string — onto whatever host a ``Location`` names.
    """
    current = url
    for _ in range(MAX_SAME_ORIGIN_REDIRECTS):
        response = send(current, timeout=REQUEST_TIMEOUT, allow_redirects=False, **sent)
        location = response.headers.get("Location")
        if response.status_code not in REDIRECT_STATUSES or not location:
            return response

        target = urljoin(current, location)
        if not same_origin(url, target):
            raise RedirectRefused(refusal(current, target, service))
        current = target

    raise RedirectRefused(
        f"{service} redirected {_loggable(url)} more than "
        f"{MAX_SAME_ORIGIN_REDIRECTS} times."
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
