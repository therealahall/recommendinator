"""The one place a cover URL is dialled. RAWG hands over ``background_image``
verbatim, so the guards live on the fetch rather than on any one caller.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass

import requests

from src.covers.cache import image_media_type
from src.ingestion.urls import (
    RedirectRefused,
    UrlOrigin,
    request_within_origin,
    url_origin,
)

MAX_BYTES = 5 * 1024 * 1024

#: The request timeout bounds one socket read, which a trickling host never breaches.
_DEADLINE_SECONDS = 30
_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: A 5xx, a timeout and a refused hop are the server having a bad day.
_PERMANENT_STATUSES = frozenset({403, 404, 410})


@dataclass(frozen=True)
class CoverUnavailable:
    reason: str
    #: A retry fails the same way, so the fill-only column can be cleared.
    permanent: bool


_TOOK_TOO_LONG = CoverUnavailable("the cover took too long to arrive", permanent=False)


def _off_origin_refusal(_url: str, _target: str, _service: str) -> str:
    """Neither URL, unlike the refusals in `urls.py`: a cover URL is whatever a
    metadata provider handed over, so there is no setting to repoint it at."""
    return "the cover redirected to another origin"


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
    # Once, rather than per hop: the walk below never leaves this origin.
    if not private_allowed and _is_private(origin.host):
        return CoverUnavailable(
            "the cover URL points at a private address", permanent=False
        )

    deadline = time.monotonic() + _DEADLINE_SECONDS
    try:
        with request_within_origin(
            requests.get,
            url,
            "the cover host",
            _off_origin_refusal,
            deadline=deadline,
            auth=auth,
            verify=verify,
            stream=True,
        ) as response:
            return _read_image(response, deadline)
    except RedirectRefused as refused:
        # The walk refuses a hop off the origin, a chain past the hop cap and one
        # that outlives the deadline, and the backfill lists this reason: one
        # wording for the three names the wrong cause for two of them.
        return CoverUnavailable(str(refused), permanent=False)
    except requests.RequestException:
        # Not the exception's words: they quote the URL and the headers.
        return CoverUnavailable("the cover host could not be reached", False)


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
