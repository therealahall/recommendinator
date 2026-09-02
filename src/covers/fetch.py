"""The one place a cover URL is dialled. RAWG hands over ``background_image``
verbatim, so the guards live on the fetch rather than on any one caller.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from src.covers.cache import image_media_type
from src.ingestion.urls import UrlOrigin, url_origin

MAX_BYTES = 5 * 1024 * 1024

_TIMEOUT = 10
#: The timeout above bounds one socket read, which a trickling host never breaches.
_DEADLINE_SECONDS = 30
_MAX_HOPS = 3
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: A 5xx, a timeout and a refused hop are the server having a bad day.
_PERMANENT_STATUSES = frozenset({403, 404, 410})


@dataclass(frozen=True)
class CoverUnavailable:
    reason: str
    #: A retry fails the same way, so the fill-only column can be cleared.
    permanent: bool


_TOOK_TOO_LONG = CoverUnavailable("the cover took too long to arrive", permanent=False)


def fetch_cover(
    url: str,
    *,
    auth: tuple[str, str] | None = None,
    verify: bool = True,
    private_allowed: bool = False,
) -> bytes | CoverUnavailable:
    """Fetch *url* as image bytes, or say why it is not one."""
    origin = url_origin(url)
    if not isinstance(origin, UrlOrigin) or origin.scheme not in _ALLOWED_SCHEMES:
        return CoverUnavailable("the cover URL names no http host", permanent=True)
    if not private_allowed and _is_private(origin.host):
        return CoverUnavailable(
            "the cover URL points at a private address", permanent=False
        )

    deadline = time.monotonic() + _DEADLINE_SECONDS
    for _ in range(_MAX_HOPS):
        if time.monotonic() > deadline:
            return _TOOK_TOO_LONG
        try:
            with requests.get(
                url,
                auth=auth,
                verify=verify,
                timeout=_TIMEOUT,
                stream=True,
                allow_redirects=False,
            ) as response:
                location = response.headers.get("Location")
                if response.status_code in _REDIRECT_STATUSES and location:
                    target = urljoin(url, location)
                    if url_origin(target) != origin:
                        return CoverUnavailable(
                            "the cover redirected to another origin", permanent=False
                        )
                    url = target
                    continue
                return _read_image(response, deadline)
        except requests.RequestException:
            # Not the exception's words: they quote the URL and the headers.
            return CoverUnavailable("the cover host could not be reached", False)

    return CoverUnavailable("the cover redirected too many times", permanent=False)


def _read_image(
    response: requests.Response, deadline: float
) -> bytes | CoverUnavailable:
    if response.status_code in _PERMANENT_STATUSES:
        return CoverUnavailable(
            f"the cover host answered {response.status_code}", permanent=True
        )
    if not response.ok:
        return CoverUnavailable(
            f"the cover host answered {response.status_code}", permanent=False
        )

    data = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if time.monotonic() > deadline:
            return _TOOK_TOO_LONG
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            return CoverUnavailable("the cover is too large", permanent=False)

    if image_media_type(bytes(data)) is None:
        # An HTML error page answers 200 as readily as an image does.
        return CoverUnavailable("the cover is not an image", permanent=True)
    return bytes(data)


def _is_private(host: str) -> bool:
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = _resolve(host)
    return any(not address.is_global for address in addresses)


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        # A name that will not resolve cannot be connected to either.
        return []
    resolved = []
    for info in infos:
        try:
            resolved.append(ipaddress.ip_address(str(info[4][0]).partition("%")[0]))
        except ValueError:
            continue
    return resolved
