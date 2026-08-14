"""The ``preferences`` group and its ``custom-rules`` subgroup."""

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
from src.recommendations.preference_interpreter import PatternBasedInterpreter
from src.recommendations.scorers import SCORER_NAME_MAP
from src.storage.manager import StorageManager, UnknownUserError
from src.utils.text import sanitize_rule_text


@click.group()
def preferences() -> None:
    """Manage user preference settings."""


def _edit_preferences(
    ctx: click.Context,
    user_id: int,
    apply: Callable[[UserPreferenceConfig], None],
) -> UserPreferenceConfig:
    """Apply *apply* to a user's preferences under storage's write lock.

    An unlocked read-mutate-save loses whatever a concurrent write stored, and
    the write is an UPDATE keyed on the id, so an unknown user used to print
    success and persist nothing.
    """
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
        # Table output
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

    TOGGLE_NAME is the setting to change (series_in_order).  VALUE is 'on' or
    'off'.
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
    scale as scorer weights). 0.0 turns it off; higher values more strongly
    demote genres you have recently finished so recommendations vary instead of
    marching through the next entry in a just-completed series, up to 5.0 which
    fully zeroes a just-finished genre. The penalty decays as you finish more.
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
@click.pass_context
def preferences_reset(ctx: click.Context, user_id: int) -> None:
    """Reset user preferences to defaults."""
    storage = ctx.obj["storage"]
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


@preferences.group("custom-rules")
def custom_rules() -> None:
    """Manage custom preference rules."""


@custom_rules.command("list")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def custom_rules_list(ctx: click.Context, user_id: int) -> None:
    """List all custom rules for a user."""
    storage = ctx.obj["storage"]
    preference_config = storage.get_user_preference_config(user_id)
    rules = preference_config.custom_rules

    if not rules:
        click.echo(f"No custom rules set for user {user_id}")
        return

    click.echo(f"Custom rules for user {user_id}:")
    for index, rule in enumerate(rules):
        # A rule reaches storage as it was typed, and ``click.echo`` encodes
        # strictly: a lone surrogate out of argv raises here rather than print.
        click.echo(f"  {index}: {sanitize_rule_text(rule)}")


@custom_rules.command("add")
@click.argument("rule_text")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def custom_rules_add(ctx: click.Context, rule_text: str, user_id: int) -> None:
    """Add a custom preference rule.

    RULE_TEXT is the natural language rule (e.g., "avoid horror", "prefer sci-fi").

    Bounded as PUT /api/users/{id}/preferences is: a list this appends past
    the bound is one the Preferences page can then no longer save back.
    """
    if len(rule_text) > UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH:
        click.echo(
            "Error: A rule may be at most "
            f"{UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH} characters.",
            err=True,
        )
        raise click.Abort()

    def add_rule(preference_config: UserPreferenceConfig) -> None:
        # Under the lock, so the list it counts is the list it appends to.
        if len(preference_config.custom_rules) >= UserPreferenceConfig.MAX_CUSTOM_RULES:
            abort_with(
                "At most "
                f"{UserPreferenceConfig.MAX_CUSTOM_RULES} rules are kept. "
                "Remove one first."
            )
        preference_config.custom_rules.append(rule_text)

    saved = _edit_preferences(ctx, user_id, add_rule)
    click.echo(f"Added rule: '{sanitize_rule_text(rule_text)}' for user {user_id}")
    click.echo(f"Total rules: {len(saved.custom_rules)}")


@custom_rules.command("remove")
@click.argument("index", type=int)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID",
)
@click.pass_context
def custom_rules_remove(ctx: click.Context, index: int, user_id: int) -> None:
    """Remove a custom rule by index.

    INDEX is the rule number (use 'list' to see indices).
    """
    removed = ""

    def remove_rule(preference_config: UserPreferenceConfig) -> None:
        nonlocal removed
        if index < 0 or index >= len(preference_config.custom_rules):
            abort_with(
                f"Invalid index {index}. "
                f"Valid range: 0-{len(preference_config.custom_rules) - 1}"
            )
        removed = preference_config.custom_rules.pop(index)

    saved = _edit_preferences(ctx, user_id, remove_rule)
    click.echo(f"Removed rule: '{sanitize_rule_text(removed)}'")
    click.echo(f"Remaining rules: {len(saved.custom_rules)}")


@custom_rules.command("clear")
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
def custom_rules_clear(ctx: click.Context, user_id: int, yes: bool) -> None:
    """Clear all custom rules for a user."""
    storage = ctx.obj["storage"]
    count = len(storage.get_user_preference_config(user_id).custom_rules)

    if count and not yes:
        if not click.confirm(f"Clear {count} custom rule(s) for user {user_id}?"):
            click.echo("Aborted.")
            return

    cleared_count = 0

    def clear_rules(preference_config: UserPreferenceConfig) -> None:
        nonlocal cleared_count
        cleared_count = len(preference_config.custom_rules)
        preference_config.custom_rules = []

    # No early return on an empty list: the write is what proves the user
    # exists, and skipping it reported success for an id no user carries. The
    # count above only sizes the prompt; this one is read under the lock.
    _edit_preferences(ctx, user_id, clear_rules)
    if cleared_count:
        click.echo(f"Cleared {cleared_count} custom rule(s) for user {user_id}")
    else:
        click.echo(f"No custom rules to clear for user {user_id}")


@custom_rules.command("interpret")
@click.argument("rule_text")
@click.pass_context
def custom_rules_interpret(ctx: click.Context, rule_text: str) -> None:
    """Interpret a custom rule and show the parsed result.

    RULE_TEXT is the natural language rule to interpret.

    This command shows how the system would interpret a rule without saving it.
    """
    result = PatternBasedInterpreter().interpret(rule_text)

    # The interpreter sanitized it on the way in, so this is the text the
    # parse below was actually run against.
    click.echo(f"\nRule: '{result.original_rule}'")
    click.echo(f"Confidence: {result.confidence.value}")
    click.echo(f"Notes: {result.interpretation_notes}")
    click.echo("")

    if result.genre_boosts:
        click.echo("Genre boosts:")
        for genre, boost in result.genre_boosts.items():
            click.echo(f"  +{boost:.1f} {genre}")

    if result.genre_penalties:
        click.echo("Genre penalties:")
        for genre, penalty in result.genre_penalties.items():
            click.echo(f"  -{penalty:.1f} {genre}")

    if result.content_type_filters:
        click.echo(f"Content type filters: {', '.join(result.content_type_filters)}")

    if result.content_type_exclusions:
        click.echo(
            f"Content type exclusions: {', '.join(result.content_type_exclusions)}"
        )

    if result.length_preferences:
        click.echo("Length preferences:")
        for content_type, length in result.length_preferences.items():
            click.echo(f"  {content_type}: {length}")

    if result.is_empty():
        click.echo("(No preferences extracted from this rule)")
