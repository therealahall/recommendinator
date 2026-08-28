"""Sign-in endpoints: the four routes a signed-out browser may reach. Their own
router, because the session dependency every other ``/api`` route carries would
make signing in impossible.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.storage.accounts import (
    MIN_PASSWORD_LENGTH,
    PASSWORD_TOO_SHORT,
    AccountAlreadyClaimedError,
    PasswordTooShortError,
)
from src.web.api import (
    AccountDisplayName,
    AccountUsername,
    UserResponse,
    as_user_response,
)
from src.web.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    set_session_cookie,
    signed_in_user,
)
from src.web.csrf import refuse_cross_origin
from src.web.guards import RequiredStorage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth", tags=["auth"], dependencies=[Depends(refuse_cross_origin)]
)

_SIGN_IN_REFUSED = "That username and password do not match an account."
_ALREADY_CLAIMED = "This instance already has an account. Sign in instead."


class SetupRequest(BaseModel):
    """No ``min_length`` on the password: a Pydantic 422 renders ``detail`` as a
    list, which the SPA cannot show, on the one screen nobody can skip.
    """

    username: AccountUsername
    display_name: AccountDisplayName = ""
    password: str = Field(..., max_length=1000)


class LoginRequest(BaseModel):
    username: AccountUsername
    password: str = Field(..., max_length=1000)


class SessionResponse(BaseModel):
    """What the SPA needs on boot to choose setup, login or the app itself."""

    claimed: bool
    authenticated: bool
    user: UserResponse | None = None
    #: So the setup form states the floor before the server has to refuse one.
    min_password_length: int = MIN_PASSWORD_LENGTH


@router.get("/session", response_model=SessionResponse)
def read_session(request: Request, storage: RequiredStorage) -> SessionResponse:
    user = signed_in_user(request)
    return SessionResponse(
        claimed=storage.accounts.is_claimed(),
        authenticated=user is not None,
        user=as_user_response(storage, user) if user is not None else None,
    )


@router.post("/setup", response_model=UserResponse)
def claim_instance(
    body: SetupRequest, response: Response, storage: RequiredStorage
) -> UserResponse:
    if storage.accounts.is_claimed():
        raise HTTPException(status_code=409, detail=_ALREADY_CLAIMED)
    try:
        user = storage.accounts.claim(
            body.username, body.display_name or None, body.password
        )
    except PasswordTooShortError as error:
        raise HTTPException(status_code=400, detail=PASSWORD_TOO_SHORT) from error
    except AccountAlreadyClaimedError as error:
        raise HTTPException(status_code=409, detail=_ALREADY_CLAIMED) from error

    set_session_cookie(response, storage.accounts.create_session(user["id"]))
    logger.info("Account claimed; this instance is no longer open to setup")
    return as_user_response(storage, user)


@router.post("/login", response_model=UserResponse)
def sign_in(
    body: LoginRequest, response: Response, storage: RequiredStorage
) -> UserResponse:
    user = storage.accounts.verify_password(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail=_SIGN_IN_REFUSED)

    set_session_cookie(response, storage.accounts.create_session(user["id"]))
    return as_user_response(storage, user)


@router.post("/logout", status_code=204)
def sign_out(request: Request, response: Response, storage: RequiredStorage) -> None:
    """Open to a signed-out caller: with the cookie already expired there is
    nothing to revoke, and a 401 would leave the browser holding it.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        storage.accounts.revoke_session(token)
    clear_session_cookie(response)
