from __future__ import annotations

import json
from pathlib import Path

import click

from src.cli._shared import abort_with
from src.covers.service import fill_cover


@click.group()
def covers() -> None:
    """Manage cached cover art."""


@covers.command("show")
@click.argument("item_id", type=int)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID owning the item",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def covers_show(
    ctx: click.Context, item_id: int, user_id: int, output_format: str
) -> None:
    """Where this item's cover art is cached, fetching it once if it is not.

    Mirrors GET /api/covers/{item_id}, which serves the same file.
    """
    storage = ctx.obj["storage"]
    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        abort_with(f"Item {item_id} not found.")

    outcome = fill_cover(storage, ctx.obj["config"], item, user_id=user_id)
    cached = isinstance(outcome, Path)
    view = {
        "db_id": item.db_id,
        "title": item.title,
        "path": str(outcome) if isinstance(outcome, Path) else None,
        "reason": None if isinstance(outcome, Path) else outcome.reason,
    }

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
    elif cached:
        click.echo(str(view["path"]))
    else:
        click.echo(f"No cover art for '{item.title}': {view['reason']}")
