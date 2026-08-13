"""Session-cookie authentication for the ``/api`` surface.

Attached to the routers themselves rather than at ``include_router``, so a
route is authenticated by being registered — a bare mount included.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response

from src.storage.accounts import SESSION_LIFETIME
from src.storage.schema import UserDict
from src.web.guards import STORAGE_UNAVAILABLE
from src.web.state import get_storage

SESSION_COOKIE = "recommendinator_session"

#: Names neither the username nor the password, so a refusal cannot be read
#: back as "that account exists".
UNAUTHORIZED_DETAIL = "Not signed in."

# No ``secure``: this app serves no TLS, and the documented deployment is
# loopback or behind a reverse proxy, so a Secure cookie would never be sent at
# all. ``strict`` ignores the port, which ``src/web/csrf.py`` covers.
_COOKIE_ATTRIBUTES: dict[str, Any] = {
    "httponly": True,
    "samesite": "strict",
    "path": "/",
}


def set_session_cookie(response: Response, token: str) -> None:
    """Put *token* in the browser's session cookie."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        **_COOKIE_ATTRIBUTES,
    )


def clear_session_cookie(response: Response) -> None:
    """Drop the browser's session cookie.

    The same attributes as the set: a clear differing in path or SameSite
    leaves the original cookie in place, so signing out would appear to work.
    """
    response.delete_cookie(SESSION_COOKIE, **_COOKIE_ATTRIBUTES)


def signed_in_user(request: Request) -> UserDict | None:
    """Return the user *request*'s cookie names, or None for anyone else."""
    token = request.cookies.get(SESSION_COOKIE)
    storage = get_storage()
    if not token or storage is None:
        return None
    return storage.lookup_session(token)


def require_session(request: Request) -> UserDict:
    """Answer 401 unless the request carries a live session cookie.

    Storage is reached through ``src.web.state`` rather than declared as a
    dependency, so a cookieless request is refused before any component
    resolves: an anonymous caller learns nothing about what is up.
    """
    if request.cookies.get(SESSION_COOKIE) and get_storage() is None:
        raise HTTPException(status_code=503, detail=STORAGE_UNAVAILABLE)
    user = signed_in_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_DETAIL)
    # The lookup rolled the row's expiry forward; a ``Max-Age`` fixed at
    # sign-in would sign a daily user out on day 30 of a live session. The
    # middleware in ``app.py`` carries the new one to the browser.
    request.state.session_token = request.cookies[SESSION_COOKIE]
    return user


#: The signed-in user, for a handler that needs to know who is asking. The same
#: dependency the routers carry, so FastAPI resolves it once per request.
CurrentUser = Annotated[UserDict, Depends(require_session)]
