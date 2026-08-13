"""Sign-in endpoints: the four routes a signed-out browser may reach.

Their own router, because the session dependency every other ``/api`` route
carries would make signing in impossible.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.storage.accounts import MIN_PASSWORD_LENGTH, AccountAlreadyClaimedError
from src.storage.schema import UserDict
from src.utils.dates import utc_now
from src.web.api import UserResponse
from src.web.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    set_session_cookie,
    signed_in_user,
)
from src.web.guards import RequiredStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# One account and no shared store, so an in-process counter is the whole
# control: enough to stop an online guesser, and it forgets on restart.
_MAX_FAILURES = 5
_LOCKOUT = timedelta(minutes=5)

_SIGN_IN_REFUSED = "That username and password do not match an account."
_TOO_MANY_ATTEMPTS = "Too many failed sign-in attempts. Wait a few minutes and retry."
_ALREADY_CLAIMED = "This instance already has an account. Sign in instead."


class _LoginThrottle:
    """Consecutive failed sign-ins per username, in this process only."""

    def __init__(self) -> None:
        self._failures: dict[str, tuple[int, datetime]] = {}
        self._lock = threading.Lock()

    def locked_out(self, username: str) -> bool:
        """Whether *username* has spent its attempts inside the lockout window."""
        with self._lock:
            count, last = self._failures.get(username, (0, utc_now()))
            return count >= _MAX_FAILURES and last > utc_now() - _LOCKOUT

    def record_failure(self, username: str) -> None:
        """Count one refusal, dropping every attempt that has aged out."""
        now = utc_now()
        with self._lock:
            live = {
                name: record
                for name, record in self._failures.items()
                if record[1] > now - _LOCKOUT
            }
            count, _ = live.get(username, (0, now))
            live[username] = (count + 1, now)
            self._failures = live

    def clear(self, username: str) -> None:
        """Forget *username*'s failures, after a sign-in that worked."""
        with self._lock:
            self._failures.pop(username, None)

    def forget_everything(self) -> None:
        """Drop every counted failure."""
        with self._lock:
            self._failures.clear()


_throttle = _LoginThrottle()


def reset_login_throttle() -> None:
    """Drop every counted failure. For tests, which share one process."""
    _throttle.forget_everything()


class SetupRequest(BaseModel):
    """First-run account creation."""

    username: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field("", max_length=100)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=1000)


class LoginRequest(BaseModel):
    """Credentials for an existing account."""

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., max_length=1000)


class SessionResponse(BaseModel):
    """What the SPA needs on boot to choose setup, login or the app itself."""

    claimed: bool
    authenticated: bool
    user: UserResponse | None = None


def _as_user(user: UserDict) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/session", response_model=SessionResponse)
def read_session(request: Request, storage: RequiredStorage) -> SessionResponse:
    """Report whether this instance is claimed, and who is signed in.

    One call, because the three answers decide one thing between them: which
    screen the SPA opens on.
    """
    user = signed_in_user(request)
    return SessionResponse(
        claimed=storage.account_is_claimed(),
        authenticated=user is not None,
        user=_as_user(user) if user is not None else None,
    )


@router.post("/setup", response_model=UserResponse)
def claim_instance(
    body: SetupRequest, response: Response, storage: RequiredStorage
) -> UserResponse:
    """Claim an unclaimed instance, and sign the claimant in.

    Raises:
        HTTPException: 409 once the account exists. The claim itself refuses a
            second one too, so a request that raced past the check writes
            nothing either.
    """
    if storage.account_is_claimed():
        raise HTTPException(status_code=409, detail=_ALREADY_CLAIMED)
    try:
        user = storage.claim_account(
            body.username, body.display_name.strip() or None, body.password
        )
    except AccountAlreadyClaimedError as error:
        raise HTTPException(status_code=409, detail=_ALREADY_CLAIMED) from error

    set_session_cookie(response, storage.create_session(user["id"]))
    logger.info("Account claimed; this instance is no longer open to setup")
    return _as_user(user)


@router.post("/login", response_model=UserResponse)
def sign_in(
    body: LoginRequest, response: Response, storage: RequiredStorage
) -> UserResponse:
    """Exchange a username and password for a session cookie.

    Raises:
        HTTPException: 429 once the attempts run out, 401 otherwise. Neither
            says which half was wrong.
    """
    if _throttle.locked_out(body.username):
        raise HTTPException(status_code=429, detail=_TOO_MANY_ATTEMPTS)

    user = storage.verify_password(body.username, body.password)
    if user is None:
        _throttle.record_failure(body.username)
        raise HTTPException(status_code=401, detail=_SIGN_IN_REFUSED)

    _throttle.clear(body.username)
    set_session_cookie(response, storage.create_session(user["id"]))
    return _as_user(user)


@router.post("/logout", status_code=204)
def sign_out(request: Request, response: Response, storage: RequiredStorage) -> None:
    """End the session this request carries, in the database and the browser.

    Open to a signed-out caller: with the cookie already expired there is
    nothing to revoke, and a 401 would leave the browser holding it.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        storage.revoke_session(token)
    clear_session_cookie(response)
