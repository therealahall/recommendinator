"""Sign-in endpoints: the four routes a signed-out browser may reach.

Their own router, because the session dependency every other ``/api`` route
carries would make signing in impossible.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.storage.accounts import (
    MIN_PASSWORD_LENGTH,
    AccountAlreadyClaimedError,
    PasswordTooShortError,
)
from src.storage.manager import StorageManager
from src.storage.schema import get_default_user_id
from src.utils.dates import utc_now
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

# One account and no shared store, so an in-process counter is the whole
# control: enough to stop an online guesser, and it forgets on restart.
_MAX_FAILURES = 5
_LOCKOUT = timedelta(minutes=5)

# Per-username counting alone bounds nothing: an unknown username is hashed
# against a stand-in salt so a miss cannot be timed, so varying it every
# request buys unlimited scrypt work. Far above one operator's typing.
_MAX_TOTAL_FAILURES = 50

_SIGN_IN_REFUSED = "That username and password do not match an account."
_TOO_MANY_ATTEMPTS = "Too many failed sign-in attempts. Wait a few minutes and retry."
_ALREADY_CLAIMED = "This instance already has an account. Sign in instead."


class _LoginThrottle:
    """Failed sign-ins per username and in total, in this process only."""

    def __init__(self) -> None:
        self._failures: dict[str, tuple[int, datetime]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: datetime) -> dict[str, tuple[int, datetime]]:
        """Drop every username whose last failure has aged out of the window."""
        self._failures = {
            name: record
            for name, record in self._failures.items()
            if record[1] > now - _LOCKOUT
        }
        return self._failures

    def locked_out(self, username: str, *, count_total: bool) -> bool:
        """Whether *username*, or the instance, has spent its attempts.

        ``count_total=False`` for the claimed account: see :func:`sign_in`.
        """
        with self._lock:
            counts = {
                name: count for name, (count, _) in self._prune(utc_now()).items()
            }
            if counts.get(username, 0) >= _MAX_FAILURES:
                return True
            return count_total and sum(counts.values()) >= _MAX_TOTAL_FAILURES

    def record_failure(self, username: str) -> None:
        """Count one refusal, dropping every attempt that has aged out."""
        now = utc_now()
        with self._lock:
            live = self._prune(now)
            count, _ = live.get(username, (0, now))
            live[username] = (count + 1, now)

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
    """First-run account creation.

    No ``min_length`` on the password: a Pydantic 422 renders ``detail`` as a
    list, which the SPA cannot show, on the one screen nobody can skip.
    :mod:`src.storage.accounts` holds the floor, and this route renders its
    refusal as a 400.
    """

    username: AccountUsername
    display_name: AccountDisplayName = ""
    password: str = Field(..., max_length=1000)


class LoginRequest(BaseModel):
    """Credentials for an existing account."""

    username: AccountUsername
    password: str = Field(..., max_length=1000)


class SessionResponse(BaseModel):
    """What the SPA needs on boot to choose setup, login or the app itself."""

    claimed: bool
    authenticated: bool
    user: UserResponse | None = None
    #: So the setup form states the floor before the server has to refuse one.
    min_password_length: int = MIN_PASSWORD_LENGTH


def _is_the_claimed_username(storage: StorageManager, username: str) -> bool:
    """Whether *username* is the one this instance belongs to.

    The total ceiling exempts it: counted in, fifty throwaway usernames would
    shut the operator out every five minutes. A ``users`` read, no hashing.
    """
    account = storage.describe_account(get_default_user_id())
    return (
        account is not None and account["claimed"] and account["username"] == username
    )


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
        user=as_user_response(storage, user) if user is not None else None,
    )


@router.post("/setup", response_model=UserResponse)
def claim_instance(
    body: SetupRequest, response: Response, storage: RequiredStorage
) -> UserResponse:
    """Claim an unclaimed instance, and sign the claimant in.

    Raises:
        HTTPException: 409 once the account exists — the claim itself refuses a
            second one too, so a request that raced past the check writes
            nothing either — and 400 for a password under the floor.
    """
    if storage.account_is_claimed():
        raise HTTPException(status_code=409, detail=_ALREADY_CLAIMED)
    try:
        user = storage.claim_account(
            body.username, body.display_name or None, body.password
        )
    except PasswordTooShortError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AccountAlreadyClaimedError as error:
        raise HTTPException(status_code=409, detail=_ALREADY_CLAIMED) from error

    set_session_cookie(response, storage.create_session(user["id"]))
    logger.info("Account claimed; this instance is no longer open to setup")
    return as_user_response(storage, user)


@router.post("/login", response_model=UserResponse)
def sign_in(
    body: LoginRequest, response: Response, storage: RequiredStorage
) -> UserResponse:
    """Exchange a username and password for a session cookie.

    Raises:
        HTTPException: 429 once the attempts run out, 401 otherwise. Neither
            says which half was wrong.
    """
    own = _is_the_claimed_username(storage, body.username)
    if _throttle.locked_out(body.username, count_total=not own):
        raise HTTPException(status_code=429, detail=_TOO_MANY_ATTEMPTS)

    user = storage.verify_password(body.username, body.password)
    if user is None:
        _throttle.record_failure(body.username)
        raise HTTPException(status_code=401, detail=_SIGN_IN_REFUSED)

    _throttle.clear(body.username)
    set_session_cookie(response, storage.create_session(user["id"]))
    return as_user_response(storage, user)


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
