from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.storage.accounts import PASSWORD_TOO_SHORT, PasswordTooShortError
from src.storage.manager import UnknownUserError
from src.storage.schema import UserDict
from src.web.api._shared import (
    AccountDisplayName,
    AccountUsername,
    UserIdPath,
    UserResponse,
    as_user_response,
)
from src.web.auth import SESSION_COOKIE, CurrentUser
from src.web.guards import RequiredStorage

router = APIRouter()

_WRONG_CURRENT_PASSWORD = "Your current password is not correct."


def _refuse_another_account(user_id: int, user: UserDict) -> None:
    """A 404 would say which ids exist, and this is the shape a Users page needs
    anyway: an admin distinction is a change here, not a new route.
    """
    if user_id != user["id"]:
        raise HTTPException(
            status_code=403, detail="You may only change your own account."
        )


class UserUpdateRequest(BaseModel):
    username: AccountUsername
    display_name: AccountDisplayName = ""


class PasswordChangeRequest(BaseModel):
    """The session alone must not be enough, or a borrowed unlocked browser is a
    permanent takeover.
    """

    current_password: str = Field(..., max_length=1000)
    # No ``min_length``: the floor is enforced where the password is written
    # and reported as a 400 — see :class:`SetupRequest`.
    new_password: str = Field(..., max_length=1000)


@router.get("/users", response_model=list[UserResponse])
def list_users(storage: RequiredStorage) -> list[UserResponse]:
    return [as_user_response(storage, user) for user in storage.get_all_users()]


@router.patch("/users/{user_id}", response_model=UserResponse)
def rename_account(
    user_id: UserIdPath,
    request: UserUpdateRequest,
    storage: RequiredStorage,
    user: CurrentUser,
) -> UserResponse:
    _refuse_another_account(user_id, user)
    try:
        renamed = storage.update_user_identity(
            user_id, request.username, request.display_name or None
        )
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="User not found.") from None
    return as_user_response(storage, renamed)


@router.put("/users/{user_id}/password", status_code=204)
def change_password(
    user_id: UserIdPath,
    request: PasswordChangeRequest,
    http_request: Request,
    storage: RequiredStorage,
    user: CurrentUser,
) -> None:
    """Replace an account's password, signing every other browser out."""
    _refuse_another_account(user_id, user)
    if (
        storage.accounts.verify_password(user["username"], request.current_password)
        is None
    ):
        raise HTTPException(status_code=401, detail=_WRONG_CURRENT_PASSWORD)

    try:
        storage.accounts.set_password(user_id, request.new_password)
    except PasswordTooShortError as error:
        raise HTTPException(status_code=400, detail=PASSWORD_TOO_SHORT) from error
    # The token is a live session by the time this runs — the dependency that
    # produced ``user`` looked it up — so the caller keeps the browser they
    # changed the password in, and every other one is signed out.
    token = http_request.cookies[SESSION_COOKIE]
    storage.accounts.revoke_other_sessions(user_id, token)
