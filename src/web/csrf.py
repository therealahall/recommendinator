"""Refuse a state change another origin drove.

``SameSite=Strict`` computes the site from scheme and registrable domain and
ignores the port, so any other server on localhost — the Vite dev server
included — is same-site and gets the session cookie.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

CROSS_ORIGIN_DETAIL = "Cross-origin requests may not change anything here."

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: ``same-origin`` is the SPA itself; ``none`` is a typed address or a
#: bookmark, which no other page can cause. ``same-site`` is refused precisely
#: because it is the neighbouring port the cookie does not separate.
_OWN_ORIGIN = frozenset({"same-origin", "none"})


def refuse_cross_origin(request: Request) -> None:
    """Answer 403 unless the browser reports this state change as our own.

    A missing header reads as same-origin: only a browser sets it, and the
    health check is not one.

    Raises:
        HTTPException: 403, naming no route.
    """
    if request.method in _READ_ONLY_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in _OWN_ORIGIN:
        raise HTTPException(status_code=403, detail=CROSS_ORIGIN_DETAIL)
