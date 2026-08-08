"""Bearer-token authentication for the ``/api`` surface.

Attached to the routers, not to handlers, so a route is authenticated by the
fact of being registered. ``create_app`` refuses to boot without a token, so
there is no open mode to fall into.
"""

from __future__ import annotations

from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.web.state import get_api_token

UNAUTHORIZED_DETAIL = "Invalid or missing API token."

# auto_error=False: the built-in refusal is a 403 with no challenge header,
# which tells a caller nothing about what to send.
_bearer = HTTPBearer(auto_error=False)


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Answer 401 unless the request carries the configured bearer token."""
    expected = get_api_token()
    presented = credentials.credentials if credentials is not None else ""
    # compare_digest, not ==, which returns at the first differing byte and so
    # times how much of a guess was right. Bytes because a header is arbitrary
    # text and compare_digest refuses a non-ASCII str.
    if expected is None or not compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail=UNAUTHORIZED_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )
