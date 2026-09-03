from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Path as PathParam  # this module's ``Path`` is pathlib's
from pydantic import AfterValidator, BaseModel

from src.settings.metadata import default_of
from src.settings.service import effective_value
from src.storage.accounts import AccountNameError, normalize_account_name
from src.storage.manager import StorageManager
from src.storage.schema import UserDict


def _account_name_validator(required: bool) -> Callable[[str], str]:
    """No ``Field(max_length=...)`` beside it: Pydantic runs that before any
    ``AfterValidator``, so it measured the padding the trim removes and the
    web refused a name ``account set-name`` stored.
    """

    def normalize(value: str) -> str:
        try:
            return normalize_account_name(value, required=required)
        except AccountNameError as error:
            raise ValueError(str(error)) from error

    return normalize


AccountUsername = Annotated[str, AfterValidator(_account_name_validator(True))]

#: Trimmed but not required: "" is how a form clears it, and the handlers read
#: an empty display name as "fall back to the username".
AccountDisplayName = Annotated[str, AfterValidator(_account_name_validator(False))]

#: Every user id in a path. The query-param siblings all carry ``ge=1``, and a
#: non-positive id matches no row.
UserIdPath = Annotated[int, PathParam(ge=1, description="User ID")]


class PluginImportErrorResponse(BaseModel):
    module: str
    reason: str


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: str | None
    #: What ``account show`` calls "Password changed", so both interfaces
    #: report one account shape.
    password_updated_at: str | None = None


def as_user_response(storage: StorageManager, user: UserDict) -> UserResponse:
    """The stamp is fetched rather than read off *user*: no other reader of a
    ``users`` row wants a credential column beside it.
    """
    account = storage.accounts.describe(user["id"])
    return UserResponse(
        id=user["id"],
        username=user["username"],
        display_name=user.get("display_name"),
        password_updated_at=account["password_updated_at"] if account else None,
    )


class RecommendationsConfig(BaseModel):
    max_count: int = default_of("recommendations.max_count")
    default_count: int = default_of("recommendations.default_count")


def _get_recommendations_config(config: dict[str, Any] | None) -> RecommendationsConfig:
    return RecommendationsConfig(
        max_count=effective_value(config or {}, "recommendations.max_count"),
        default_count=effective_value(config or {}, "recommendations.default_count"),
    )
