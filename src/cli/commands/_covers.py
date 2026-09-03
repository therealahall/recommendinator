from __future__ import annotations

import json
import time
from pathlib import Path

import click

from src.cli._shared import abort_with
from src.covers.service import fill_cover, start_backfill
from src.storage.cover_jobs import CoverBackfillRecord


def _state(record: CoverBackfillRecord) -> str:
    if record.running:
        return "running"
    if record.cancelled:
        return "cancelled"
    return "completed" if record.completed else "stopped on an error"


def _echo_backfill(record: CoverBackfillRecord) -> None:
    click.echo(f"Cover backfill: {_state(record)}")
    click.echo(f"  Progress: {record.processed}/{record.total}")
    click.echo(f"  Covers cached: {record.cached}")
    click.echo(f"  Covers cleared as unreachable: {record.cleared}")
    click.echo(f"  Covers that failed for now: {record.failed}")
    if record.without_cover:
        click.echo(f"  Items with no cover art to fetch: {record.without_cover}")
        click.echo(
            "    A provider settled these before it was asked for art. Run"
            " 'enrichment reset' then 'enrichment start' to ask again."
        )
    for error in record.errors:
        click.echo(f"    - {error}")


@click.group()
def covers() -> None:
    """Manage cached cover art."""


@covers.command("backfill")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID whose library to walk",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def covers_backfill(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Cache every library cover that is not on disk yet.

    Mirrors POST /api/covers/backfill, and answers with the same keys. The claim
    is shared with the web action, so neither can start a second walk.
    """
    storage = ctx.obj["storage"]
    if start_backfill(storage, ctx.obj["config"], user_id=user_id) is None:
        abort_with("A cover backfill is already running.")

    record = storage.cover_jobs.read()
    try:
        while record.running:
            click.echo(
                f"  {record.processed}/{record.total} - {record.current_item[:40]}",
                nl=False,
                err=True,
            )
            click.echo("\r", nl=False, err=True)
            time.sleep(1)
            record = storage.cover_jobs.read()
    except KeyboardInterrupt:
        # The walk is a daemon thread, so it dies with this process and leaves
        # the claim held until it goes stale, refusing both Start doors for
        # five minutes. Released here the way `enrichment start` releases its own.
        record = storage.cover_jobs.read()
        record.cancelled = True
        storage.cover_jobs.finish(record)
        click.echo("\nCover backfill stopped.", err=True)
        return

    if output_format == "json":
        click.echo(json.dumps(record.payload(), indent=2))
    else:
        _echo_backfill(record)

    if not record.completed and not record.cancelled:
        ctx.exit(1)


@covers.command("stop")
@click.pass_context
def covers_stop(ctx: click.Context) -> None:
    """Stop the running cover backfill, whatever started it.

    Mirrors POST /api/covers/backfill/stop.
    """
    if not ctx.obj["storage"].cover_jobs.request_stop():
        abort_with("No cover backfill is running.")
    click.echo("Stop requested. The walk ends after the item it is on.")


@covers.command("status")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def covers_status(ctx: click.Context, output_format: str) -> None:
    """Show the live cover backfill (mirrors GET /api/covers/backfill/status).

    Reads the walk whatever started it — the web UI or another terminal — and
    returns without starting or waiting for one.
    """
    record = ctx.obj["storage"].cover_jobs.read()

    if output_format == "json":
        click.echo(json.dumps(record.payload(), indent=2))
    elif record.started_at is None:
        click.echo("No cover backfill has run.")
    else:
        _echo_backfill(record)


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
