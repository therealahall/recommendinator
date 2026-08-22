"""The ``enrichment`` group."""

from __future__ import annotations

import json
import time

import click

from src.cli._shared import abort_with
from src.enrichment.manager import EnrichmentManager
from src.models.content import ContentType


@click.group()
def enrichment() -> None:
    """Manage metadata enrichment."""


@enrichment.command("start")
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Content type to enrich (default: all types)",
)
@click.option(
    "--retry-not-found",
    is_flag=True,
    help="Re-process items previously marked as not_found (matches web API).",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering items",
)
@click.pass_context
def enrichment_start(
    ctx: click.Context,
    content_type_str: str | None,
    retry_not_found: bool,
    user_id: int,
) -> None:
    """Start background metadata enrichment.

    Enriches content items with genres, tags, and descriptions from
    external APIs (TMDB, OpenLibrary, RAWG).
    """
    storage = ctx.obj["storage"]
    config = ctx.obj["config"]

    # Check if enrichment is enabled
    enrichment_config = config.get("enrichment", {})
    if not enrichment_config.get("enabled", False):
        click.echo(
            "Enrichment is disabled in config. "
            "Set enrichment.enabled: true in config.yaml",
            err=True,
        )
        raise click.Abort()

    # Map string to ContentType enum if provided
    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    manager = EnrichmentManager(storage, config)

    if not manager.start_enrichment(
        content_type=content_type,
        user_id=user_id,
        include_not_found=retry_not_found,
    ):
        click.echo("Enrichment job is already running.", err=True)
        raise click.Abort()

    type_desc = content_type_str if content_type_str else "all types"
    click.echo(f"Started enrichment for {type_desc}...", err=True)

    # Poll for completion
    try:
        while True:
            status = manager.get_status()
            if not status.running:
                break

            progress = status.progress_percent
            current = status.current_item or "..."
            click.echo(
                f"  Progress: {progress:.1f}% - Processing: {current[:40]}",
                nl=False,
                err=True,
            )
            click.echo("\r", nl=False, err=True)
            time.sleep(1)

        # Final status
        click.echo("", err=True)
        if status.cancelled:
            click.echo("Enrichment cancelled.")
        else:
            click.echo("Enrichment completed.")

        click.echo(f"  Items processed: {status.items_processed}")
        click.echo(f"  Items enriched: {status.items_enriched}")
        click.echo(f"  Items not found: {status.items_not_found}")
        click.echo(f"  Items failed: {status.items_failed}")
        click.echo(f"  Elapsed time: {status.elapsed_seconds:.1f}s")

        if status.errors:
            click.echo("  Errors:")
            for error in status.errors[:5]:
                click.echo(f"    - {error}")
            if len(status.errors) > 5:
                click.echo(f"    ... and {len(status.errors) - 5} more")

    except KeyboardInterrupt:
        click.echo("\nStopping enrichment...", err=True)
        manager.stop_enrichment()
        click.echo("Enrichment stopped.")


@enrichment.command("status")
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering stats",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def enrichment_status(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Show enrichment statistics."""
    config = ctx.obj["config"]
    storage = ctx.obj["storage"]

    raw_stats = storage.enrichment.stats(user_id=user_id)
    enrichment_enabled = config.get("enrichment", {}).get("enabled", False)
    # Shape matches web API EnrichmentStatsResponse
    stats = {"enabled": enrichment_enabled, **raw_stats}

    if output_format == "json":
        click.echo(json.dumps(stats, indent=2))
    else:
        enabled_label = "enabled" if stats["enabled"] else "disabled"
        click.echo(f"Enrichment Statistics ({enabled_label}):")
        click.echo(f"  Total items: {stats['total']}")
        click.echo(f"  Enriched: {stats['enriched']}")
        click.echo(f"  Pending: {stats['pending']}")
        click.echo(f"  Not found: {stats['not_found']}")
        click.echo(f"  Failed: {stats['failed']}")

        if stats["by_provider"]:
            click.echo("\nBy Provider:")
            for provider, count in stats["by_provider"].items():
                click.echo(f"  {provider}: {count}")

        if stats["by_quality"]:
            click.echo("\nBy Match Quality:")
            for quality, count in stats["by_quality"].items():
                click.echo(f"  {quality}: {count}")


@enrichment.command("reset")
@click.option(
    "--provider",
    type=click.Choice(["tmdb", "openlibrary", "rawg", "all"], case_sensitive=False),
    default="all",
    help="Reset items enriched by specific provider (default: all)",
)
@click.option(
    "--type",
    "content_type_str",
    type=click.Choice(["book", "movie", "tv_show", "video_game"], case_sensitive=False),
    default=None,
    help="Reset only items of this content type",
)
@click.option(
    "--id",
    "item_id",
    type=int,
    default=None,
    help="Restore this one item to automatic enrichment",
)
@click.option(
    "--user",
    "user_id",
    type=int,
    default=1,
    help="User ID for filtering items",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def enrichment_reset(
    ctx: click.Context,
    provider: str,
    content_type_str: str | None,
    item_id: int | None,
    user_id: int,
    yes: bool,
) -> None:
    """Re-queue items for enrichment, by provider, content type or one item."""
    storage = ctx.obj["storage"]

    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    provider_filter = None if provider == "all" else provider

    if item_id is not None:
        if provider_filter or content_type_str:
            abort_with("--id cannot be combined with --provider or --type.")
        if storage.get_content_item(item_id, user_id=user_id) is None:
            abort_with(f"Item {item_id} not found.")

    desc_parts = []
    if item_id is not None:
        desc_parts.append(f"item={item_id}")
    if provider_filter:
        desc_parts.append(f"provider={provider_filter}")
    if content_type_str:
        desc_parts.append(f"type={content_type_str}")
    desc = f" ({', '.join(desc_parts)})" if desc_parts else ""

    if not yes:
        if not click.confirm(f"Reset enrichment status for items{desc}?"):
            click.echo("Aborted.")
            return

    count = storage.enrichment.reset(
        provider=provider_filter,
        content_type=content_type,
        user_id=user_id,
        content_item_id=item_id,
    )

    click.echo(f"Reset enrichment status for {count} item(s).")
