from __future__ import annotations

import json
from collections.abc import Callable

import click
from tabulate import tabulate

from src.cli._shared import abort_with
from src.models.user_preferences import (
    PreferenceValidationError,
    UserPreferenceConfig,
)
from src.recommendations.scorers import SCORER_NAME_MAP
from src.storage.manager import StorageManager, UnknownUserError


@click.group()
def preferences() -> None:
    """Manage user preference settings."""


def _edit_preferences(
    ctx: click.Context,
    user_id: int,
    apply: Callable[[UserPreferenceConfig], None],
) -> UserPreferenceConfig:
    """Apply *apply* to a user's preferences under storage's write lock."""
    storage: StorageManager = ctx.obj["storage"]
    try:
        return storage.merge_user_preference_config(user_id, apply)
    except (PreferenceValidationError, UnknownUserError) as error:
        abort_with(str(error))


@preferences.command("get")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def preferences_get(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Show current user preferences."""
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    data = preference_config.to_dict()

    if output_format == "json":
        click.echo(json.dumps(data, indent=2))
    else:
        table_data = []
        for key, value in data.items():
            table_data.append([key, str(value)])
        click.echo(tabulate(table_data, headers=["Setting", "Value"], tablefmt="grid"))


@preferences.command("set-weight")
@click.argument("scorer_name")
@click.argument("weight", type=float)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_set_weight(
    ctx: click.Context, scorer_name: str, weight: float, user_id: int
) -> None:
    """Set a scorer weight for a user.

    SCORER_NAME is the scorer to adjust (e.g. genre_match, creator_match).
    WEIGHT is the new weight value (e.g. 2.5).
    """
    if scorer_name not in SCORER_NAME_MAP:
        valid_names = ", ".join(sorted(SCORER_NAME_MAP.keys()))
        click.echo(
            f"Error: Unknown scorer '{scorer_name}'. " f"Valid scorers: {valid_names}",
            err=True,
        )
        raise click.Abort()

    def set_weight(preference_config: UserPreferenceConfig) -> None:
        preference_config.scorer_weights[scorer_name] = weight

    _edit_preferences(ctx, user_id, set_weight)
    click.echo(f"Set {scorer_name} weight to {weight} for user {user_id}")


# Boolean preference toggles settable from the CLI, mirroring the web UI's
# Rules section.  Each name is also the UserPreferenceConfig attribute.
_TOGGLE_NAMES: tuple[str, ...] = ("series_in_order",)


@preferences.command("set-toggle")
@click.argument("toggle_name", type=click.Choice(_TOGGLE_NAMES))
@click.argument("value", type=click.Choice(["on", "off"], case_sensitive=False))
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_set_toggle(
    ctx: click.Context, toggle_name: str, value: str, user_id: int
) -> None:
    """Enable or disable a boolean preference toggle.

    TOGGLE_NAME is the setting to change (series_in_order).  VALUE is 'on' or 'off'.
    """
    enabled = value.lower() == "on"
    # Guard against the allowlist drifting away from the model's bool fields.
    if not isinstance(getattr(UserPreferenceConfig(), toggle_name, None), bool):
        raise click.ClickException(f"'{toggle_name}' is not a boolean preference")

    def set_toggle(preference_config: UserPreferenceConfig) -> None:
        setattr(preference_config, toggle_name, enabled)

    _edit_preferences(ctx, user_id, set_toggle)
    state = "on" if enabled else "off"
    click.echo(f"Set {toggle_name} {state} for user {user_id}")


@preferences.command("set-variety")
@click.argument(
    "penalty",
    type=click.FloatRange(0.0, UserPreferenceConfig.MAX_VARIETY_PENALTY),
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_set_variety(ctx: click.Context, penalty: float, user_id: int) -> None:
    """Set the variety-after-completion penalty for a user.

    PENALTY is the strength of the genre-fatigue penalty (0.0-5.0, the same
    scale as scorer weights). 0.0 turns it off, up to 5.0 which fully zeroes a
    just-finished genre.
    """

    def set_variety(preference_config: UserPreferenceConfig) -> None:
        preference_config.variety_penalty = penalty

    _edit_preferences(ctx, user_id, set_variety)
    click.echo(f"Set variety_penalty to {penalty} for user {user_id}")


@preferences.command("reset")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def preferences_reset(ctx: click.Context, user_id: int, yes: bool) -> None:
    """Reset user preferences to defaults."""
    storage = ctx.obj["storage"]

    if not yes:
        if not click.confirm(
            "Reset the scorer weights, length preferences, variety penalty "
            f"and series ordering for user {user_id} to defaults?"
        ):
            click.echo("Aborted.")
            return

    try:
        storage.save_user_preference_config(user_id, UserPreferenceConfig())
    except UnknownUserError as error:
        abort_with(str(error))
    click.echo(f"Reset preferences to defaults for user {user_id}")


@preferences.command("set-length")
@click.argument(
    "content_type",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
)
@click.argument(
    "length_preference",
    type=click.Choice(["any", "short", "medium", "long"], case_sensitive=False),
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def preferences_set_length(
    ctx: click.Context, content_type: str, length_preference: str, user_id: int
) -> None:
    """Set a length preference for a content type.

    CONTENT_TYPE is the type (book, movie, tv_show, video_game).
    LENGTH_PREFERENCE is the preferred length (any, short, medium, long).
    """

    def set_length(preference_config: UserPreferenceConfig) -> None:
        preference_config.content_length_preferences[content_type.lower()] = (
            length_preference.lower()
        )

    _edit_preferences(ctx, user_id, set_length)
    click.echo(
        f"Set {content_type} length preference to '{length_preference}' for user {user_id}"
    )
