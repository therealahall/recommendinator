from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.storage.manager import UnknownUserError
from src.web.api._shared import UserIdPath
from src.web.guards import RequiredStorage
from src.web.themes import (
    DEFAULT_THEME_ID,
    MAX_THEME_ID_LENGTH,
    ThemeResponse,
    installed_theme_ids,
    installed_themes,
)

router = APIRouter()

ThemeId = Annotated[str, Field(max_length=MAX_THEME_ID_LENGTH)]


class ThemePreferenceResponse(BaseModel):
    """The theme a user's interface paints, empty when they have picked none."""

    theme: str


class ThemePreferenceRequest(BaseModel):
    theme: ThemeId


@router.get("/themes", response_model=list[ThemeResponse])
def list_themes() -> list[ThemeResponse]:
    """List the UI themes this install ships and the ones in private/themes/."""
    return installed_themes()


@router.get("/themes/default")
def get_default_theme() -> dict[str, str]:
    return {"theme": DEFAULT_THEME_ID}


@router.get("/users/{user_id}/theme", response_model=ThemePreferenceResponse)
def get_user_theme(
    user_id: UserIdPath, storage: RequiredStorage
) -> ThemePreferenceResponse:
    """Get the theme this user's interface paints, empty when none is stored."""
    return ThemePreferenceResponse(theme=storage.ui_settings.get_theme(user_id))


@router.put("/users/{user_id}/theme", response_model=ThemePreferenceResponse)
def set_user_theme(
    user_id: UserIdPath, request: ThemePreferenceRequest, storage: RequiredStorage
) -> ThemePreferenceResponse:
    if request.theme not in installed_theme_ids():
        raise HTTPException(status_code=400, detail="Theme not installed.")
    try:
        storage.ui_settings.set_theme(user_id, request.theme)
    except UnknownUserError as error:
        raise HTTPException(status_code=404, detail="User not found.") from error
    return ThemePreferenceResponse(theme=request.theme)
