"""The ``theme`` group: the per-user UI theme (mirrors the /api/themes routes)."""

from __future__ import annotations

import json

import click
from tabulate import tabulate

from src.cli._shared import abort_with, emit_view, require_storage
from src.storage.manager import UnknownUserError
from src.web.themes import (
    DEFAULT_THEME_ID,
    THEMES_DIR,
    discover_themes,
    installed_theme_ids,
)


@click.group()
def theme() -> None:
    """Show and set the UI theme the web interface paints."""


@theme.command("list")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format (json matches GET /api/themes).",
)
def theme_list(output_format: str) -> None:
    """List the themes installed on this instance."""
    themes = discover_themes(THEMES_DIR)

    if output_format == "json":
        click.echo(json.dumps([one.model_dump() for one in themes], indent=2))
        return

    rows = [[one.id, one.name, one.theme_type, one.description] for one in themes]
    click.echo(
        tabulate(rows, headers=["ID", "Name", "Type", "Description"], tablefmt="grid")
    )


@theme.command("show")
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
    help="Output format (json matches GET /api/users/{id}/theme).",
)
@click.pass_context
def theme_show(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Show the theme a user's interface paints."""
    storage = require_storage(ctx)
    stored = storage.ui_settings.get_theme(user_id)

    if output_format == "json":
        click.echo(json.dumps({"theme": stored}, indent=2))
        return
    click.echo(stored or f"{DEFAULT_THEME_ID} (default)")


@theme.command("set")
@click.argument("theme_id")
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
    help="Output format (json matches PUT /api/users/{id}/theme).",
)
@click.pass_context
def theme_set(
    ctx: click.Context, theme_id: str, user_id: int, output_format: str
) -> None:
    """Set the theme a user's interface paints.

    THEME_ID is an installed theme's id, as ``theme list`` reports it. A
    ``preferences reset`` leaves it alone: the theme is not a scoring
    preference.
    """
    storage = require_storage(ctx)
    installed = installed_theme_ids(THEMES_DIR)
    if theme_id not in installed:
        abort_with(f"Unknown theme '{theme_id}'. Installed: {', '.join(installed)}")

    try:
        storage.ui_settings.set_theme(user_id, theme_id)
    except UnknownUserError as error:
        abort_with(str(error))

    emit_view(
        output_format,
        lambda: {"theme": theme_id},
        f"Set theme to {theme_id} for user {user_id}",
    )
