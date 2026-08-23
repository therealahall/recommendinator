"""The ``settings`` group.

Mirrors the /api/settings endpoints. All business logic lives in
``src.settings.service`` (shared with the web API) so the two interfaces stay
in lockstep; the commands below only parse input and render output.
"""

from __future__ import annotations

import json
import os
from typing import Any

import click
from tabulate import tabulate

from src.cli._shared import (
    SECRET_VALUE_ENV,
    ValueCoercionError,
    abort_with,
    coerce_value,
    emit_view,
    read_json_payload,
    require_storage,
)
from src.settings.metadata import get_entry
from src.settings.service import (
    SettingsValidationError,
    apply_settings,
    build_settings_view,
    clear_secret,
    reset_setting,
    set_secret,
    setting_view,
)

#: How this group words a value its setting type cannot represent.
_VALUE_TYPE_ERRORS = {
    "bool": "expected true or false",
    "int": "expected an integer",
    "float": "expected a number",
}


def _format_value(value: Any) -> str:
    """Render a setting value for human output."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _format_setting_value(view: dict[str, Any]) -> str:
    """Render a setting's value for human output; never reveal a secret."""
    if view["sensitive"]:
        return "********" if view["has_secret"] else "(not set)"
    return _format_value(view["value"])


def _setting_flags(view: dict[str, Any]) -> str:
    """Render the ``overridden``/``restart``/``advanced`` markers for a setting."""
    flags = []
    if view.get("db_overridden"):
        flags.append("overridden")
    if view["restart_required"]:
        flags.append("restart")
    if view["advanced"]:
        flags.append("advanced")
    return ", ".join(flags)


@click.group()
def settings() -> None:
    """Manage global application settings (mirrors /api/settings)."""


@settings.command("list")
@click.option("--section", "section_name", default=None, help="Limit to one section.")
@click.option(
    "--advanced",
    is_flag=True,
    help="Include advanced (infra/security) settings in the human listing.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (json matches GET /api/settings).",
)
@click.pass_context
def settings_list(
    ctx: click.Context, section_name: str | None, advanced: bool, output_format: str
) -> None:
    """List every global setting grouped by section.

    Secrets show presence only (never their value). Advanced infra/security
    settings are hidden from the human listing unless --advanced is given or a
    specific --section is requested. --format json always emits the complete
    view.
    """
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    view = build_settings_view(config, storage)

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    sections = view["sections"]
    if section_name is not None:
        sections = [s for s in sections if s["section"] == section_name]
        if not sections:
            abort_with(f"Unknown section: {section_name}")

    show_advanced = advanced or section_name is not None
    for section in sections:
        rows = [
            [
                setting["label"],
                setting["key"],
                _format_setting_value(setting),
                _setting_flags(setting),
            ]
            for setting in section["settings"]
            if show_advanced or not setting["advanced"]
        ]
        if not rows:
            continue
        click.echo(f"\n{section['section']}")
        click.echo(
            tabulate(
                rows,
                headers=["Setting", "Key", "Value", "Flags"],
                tablefmt="grid",
            )
        )


@settings.command("get")
@click.argument("key")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (json shows secrets as presence only).",
)
@click.pass_context
def settings_get(ctx: click.Context, key: str, output_format: str) -> None:
    """Show one setting's metadata and current value.

    KEY is the dotted registry leaf (e.g. recommendations.default_count). A
    sensitive KEY shows only whether a secret is stored, never its value.
    """
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    entry = get_entry(key)
    if entry is None:
        abort_with(f"Unknown setting: {key}")
    view = setting_view(entry, config, storage)

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    rows = [
        ["Setting", view["label"]],
        ["Key", view["key"]],
        ["Section", view["section"]],
        ["Type", view["type"]],
        ["Value", _format_setting_value(view)],
        ["Flags", _setting_flags(view) or "-"],
        ["Help", view["help"]],
    ]
    click.echo(tabulate(rows, tablefmt="grid"))


@settings.command("set")
@click.argument("key")
@click.argument("value")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (json emits the refreshed settings view).",
)
@click.pass_context
def settings_set(ctx: click.Context, key: str, value: str, output_format: str) -> None:
    """Set a non-sensitive setting.

    KEY is the dotted registry leaf; VALUE is parsed to its type (booleans
    accept true/false, lists are comma-separated, numbers and strings are
    parsed as written). Use ``settings set-secret`` for sensitive keys.
    """
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    entry = get_entry(key)
    if entry is None:
        abort_with(f"Unknown setting: {key}")
    if entry.sensitive:
        abort_with(f"{key} is a secret; use 'settings set-secret {key}'.")

    try:
        parsed = coerce_value(entry.type, value)
    except ValueCoercionError as error:
        abort_with(_VALUE_TYPE_ERRORS[error.value_type])
    try:
        apply_settings(config, storage, {key: parsed})
    except SettingsValidationError as error:
        abort_with(error.reason)

    # The stored leaf, not the running one: a restart_required key is written
    # without being live-applied, so the effective value is still the old one.
    emit_view(
        output_format,
        lambda: build_settings_view(config, storage),
        f"Set {key} = {_format_value(storage.settings.get(key))}.",
    )
    # The restart hint is advice for a human; the JSON view carries the same
    # fact structurally as `restart_required` on the entry.
    if entry.restart_required and output_format != "json":
        click.echo("This change takes effect after a restart.")


@settings.command("apply")
@click.option(
    "--from-json",
    "from_json",
    required=True,
    help=(
        "Path to a JSON file containing an updates dict, or '-' to read from "
        "stdin. Mirrors PUT /api/settings — applies every key atomically."
    ),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (json emits the refreshed settings view).",
)
@click.pass_context
def settings_apply(ctx: click.Context, from_json: str, output_format: str) -> None:
    """Apply a JSON object of {dotted.key: value} atomically (bulk update).

    Mirrors the web ``PUT /api/settings`` endpoint: every update is validated
    up front through a single ``apply_settings`` call, so one bad key leaves
    nothing written (all-or-nothing). Sensitive keys are rejected — store them
    with ``settings set-secret``. Non-restart settings take effect immediately;
    restart-required settings apply on the next boot.
    """
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    updates = read_json_payload(from_json)
    try:
        apply_settings(config, storage, updates)
    except SettingsValidationError as error:
        abort_with(f"{error.key}: {error.reason}")
    emit_view(
        output_format,
        lambda: build_settings_view(config, storage),
        f"Applied {len(updates)} setting(s).",
    )


@settings.command("reset")
@click.argument("key")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (json emits the refreshed settings view).",
)
@click.pass_context
def settings_reset(ctx: click.Context, key: str, output_format: str) -> None:
    """Reset a setting to its default by dropping the database override."""
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    # Mirror the web DELETE /api/settings/{key} 404 wording for an unknown key.
    if get_entry(key) is None:
        abort_with("Unknown setting.")
    try:
        reset_setting(config, storage, key)
    except SettingsValidationError as error:
        abort_with(error.reason)
    emit_view(
        output_format,
        lambda: build_settings_view(config, storage),
        f"Reset {key} to its default.",
    )


@settings.command("set-secret")
@click.argument("key")
@click.pass_context
def settings_set_secret(ctx: click.Context, key: str) -> None:
    """Store a sensitive setting's value in the encrypted secret store.

    Reads from the ``RECOMMENDINATOR_SECRET_VALUE`` environment variable for
    non-interactive use (env vars are not exposed in shell history or in the
    process list to other users); otherwise prompts with hidden input. Rejects
    non-sensitive keys.
    """
    storage = require_storage(ctx)
    entry = get_entry(key)
    if entry is None or not entry.sensitive:
        abort_with(f"{key} is not a configurable secret.")

    value = os.environ.get(SECRET_VALUE_ENV)
    if value is None:
        value = click.prompt(
            f"New value for {key}",
            hide_input=True,
            confirmation_prompt=False,
        )

    set_secret(storage, key, value)
    click.echo(f"Stored secret for {key}.")


@settings.command("clear-secret")
@click.argument("key")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def settings_clear_secret(ctx: click.Context, key: str, yes: bool) -> None:
    """Delete a sensitive setting's stored secret."""
    storage = require_storage(ctx)
    entry = get_entry(key)
    if entry is None or not entry.sensitive:
        abort_with(f"{key} is not a configurable secret.")

    if not yes and not click.confirm(
        f"Clear the secret for {key}? The stored value is deleted for good."
    ):
        click.echo("Aborted.")
        return

    if clear_secret(storage, key):
        click.echo(f"Cleared secret for {key}.")
    else:
        click.echo(f"No secret was set for {key}.")
