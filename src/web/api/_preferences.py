from collections.abc import Callable, Iterable
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import AfterValidator, BaseModel, Field

from src.models.content import ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.content_length import LengthPreference
from src.recommendations.scorers import SCORER_NAME_MAP
from src.storage.manager import UnknownUserError
from src.web.api._shared import UserIdPath
from src.web.guards import RequiredStorage

router = APIRouter()


def _member_validator(noun: str, allowed: Iterable[str]) -> Callable[[str], str]:
    """A name outside the set weights nothing and sits in ``users.settings`` for
    every later read to parse."""
    permitted = sorted(allowed)

    def reject(value: str) -> str:
        if value not in permitted:
            raise ValueError(f"unknown {noun}; expected one of {', '.join(permitted)}")
        return value

    return reject


ScorerName = Annotated[
    str, AfterValidator(_member_validator("scorer", SCORER_NAME_MAP))
]

ContentTypeName = Annotated[
    str,
    AfterValidator(
        _member_validator("content type", (member.value for member in ContentType))
    ),
]

LengthPreferenceName = Annotated[
    str,
    AfterValidator(
        _member_validator(
            "length preference", (member.value for member in LengthPreference)
        )
    ),
]

#: ``json.loads`` accepts the ``Infinity`` and ``NaN`` literals and
#: ``JSONResponse`` refuses to render them, so one stored non-finite weight
#: answers 500 on every later read of the preferences page.
PreferenceWeight = Annotated[float, Field(allow_inf_nan=False)]

CustomRuleText = Annotated[
    str, Field(max_length=UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH)
]


class UserPreferenceResponse(BaseModel):
    """``scorer_weights`` needs no bound of its own: ``from_dict`` drops the
    non-finite ones on read, and a second refusal here would answer a bare
    ``ValidationError`` rather than anything an operator can act on.
    """

    scorer_weights: dict[str, float]
    series_in_order: bool
    variety_penalty: float = Field(
        0.0, ge=0.0, le=UserPreferenceConfig.MAX_VARIETY_PENALTY
    )
    custom_rules: list[str]
    content_length_preferences: dict[str, str] = Field(default_factory=dict)


class UserPreferenceUpdateRequest(BaseModel):
    """The merge is additive, so the key set is what bounds the stored blob."""

    scorer_weights: dict[ScorerName, PreferenceWeight] | None = None
    series_in_order: bool | None = None
    variety_penalty: float | None = Field(
        None, ge=0.0, le=UserPreferenceConfig.MAX_VARIETY_PENALTY
    )
    custom_rules: list[CustomRuleText] | None = Field(
        None, max_length=UserPreferenceConfig.MAX_CUSTOM_RULES
    )
    content_length_preferences: dict[ContentTypeName, LengthPreferenceName] | None = (
        None
    )


@router.get("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
def get_user_preferences(
    user_id: UserIdPath, storage: RequiredStorage
) -> UserPreferenceResponse:
    preference_config = storage.get_user_preference_config(user_id)
    return UserPreferenceResponse(**preference_config.to_dict())


@router.put("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
def update_user_preferences(
    user_id: UserIdPath,
    request: UserPreferenceUpdateRequest,
    storage: RequiredStorage,
) -> UserPreferenceResponse:
    # Storage does the read, this merge and the write as one locked operation:
    # two of these requests otherwise both read the old ``users.settings`` blob
    # and the later write discards the earlier one.
    def merge_supplied_fields(existing: UserPreferenceConfig) -> None:
        if request.scorer_weights is not None:
            existing.scorer_weights.update(request.scorer_weights)
        if request.series_in_order is not None:
            existing.series_in_order = request.series_in_order
        if request.variety_penalty is not None:
            existing.variety_penalty = request.variety_penalty
        if request.custom_rules is not None:
            existing.custom_rules = request.custom_rules
        if request.content_length_preferences is not None:
            existing.content_length_preferences.update(
                request.content_length_preferences
            )

    # The write is an UPDATE keyed on the id, so it is the write that knows
    # whether the user exists. A pre-check here is a second answer to the same
    # question, and the two disagreeing would be a 500.
    try:
        updated = storage.merge_user_preference_config(user_id, merge_supplied_fields)
    except UnknownUserError as error:
        raise HTTPException(status_code=404, detail="User not found.") from error

    return UserPreferenceResponse(**updated.to_dict())


@router.delete("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
def reset_user_preferences(
    user_id: UserIdPath, storage: RequiredStorage
) -> UserPreferenceResponse:
    defaults = UserPreferenceConfig()
    try:
        storage.save_user_preference_config(user_id, defaults)
    except UnknownUserError as error:
        raise HTTPException(status_code=404, detail="User not found.") from error
    return UserPreferenceResponse(**defaults.to_dict())
