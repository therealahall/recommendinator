from __future__ import annotations

import json
import time
from typing import Any, cast

import click

from src.cli._shared import abort_with
from src.enrichment.manager import (
    EnrichmentJobStatus,
    EnrichmentManager,
    EnrichmentStart,
    PinRefused,
    job_status,
)
from src.enrichment.provider_base import pins_of
from src.models.content import ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.item_serialization import (
    ENRICHMENT_UNAVAILABLE,
    enrichment_candidates_to_dict,
    enrichment_pin_to_dict,
    enrichment_reset_to_dict,
)
from src.utils.sorting import MAX_SEARCH_LENGTH


def _echo_errors(errors: list[str], *, err: bool = False) -> None:
    if not errors:
        return
    click.echo("  Errors:", err=err)
    for error in errors:
        click.echo(f"    - {error}", err=err)


def _finished_state(status: EnrichmentJobStatus) -> str:
    if status.cancelled:
        return "cancelled"
    if status.completed:
        return "completed"
    return "stopped on an error"


def run_enrichment(
    storage: StorageManager,
    config: dict[str, Any],
    content_type: ContentType | None,
    *,
    user_id: int = 1,
    include_not_found: bool = False,
    err: bool = False,
) -> EnrichmentStart:
    manager = EnrichmentManager(storage, config)
    started = manager.start_enrichment(
        content_type=content_type,
        user_id=user_id,
        include_not_found=include_not_found,
    )
    if started is not EnrichmentStart.STARTED:
        return started

    type_desc = content_type.value if content_type else "all types"
    click.echo(f"Started enrichment for {type_desc}...", err=True)
    _await_run(manager, storage, err=err)
    return started


def _await_run(
    manager: EnrichmentManager, storage: StorageManager, *, err: bool
) -> None:
    """Never backgrounded: the worker is a daemon thread, so a CLI that exited
    first would strand the claim until it went stale.
    """
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

        click.echo("", err=True)
        click.echo(f"Enrichment {_finished_state(status)}.", err=err)

        click.echo(f"  Items processed: {status.items_processed}", err=err)
        click.echo(f"  Items enriched: {status.items_enriched}", err=err)
        click.echo(f"  Items not found: {status.items_not_found}", err=err)
        click.echo(f"  Items failed: {status.items_failed}", err=err)
        click.echo(f"  Elapsed time: {status.elapsed_seconds:.1f}s", err=err)

        _echo_errors(status.errors, err=err)

    except KeyboardInterrupt:
        click.echo("\nStopping after the item in flight, up to 10s...", err=True)
        manager.stop_enrichment()
        # A daemon worker leaves the claim held when the process exits first,
        # blocking both Start doors until it goes stale. A second Ctrl-C is
        # caught here rather than escaping the join, which would do the same.
        try:
            released = manager._wait_for_completion(timeout=10.0)
        except KeyboardInterrupt:
            released = False
        if not released:
            # Extended, not replaced: the reason for interrupting is usually in
            # the failures the run had already published.
            errors = [*storage.enrichment_jobs.read().errors, "Interrupted."]
            storage.enrichment_jobs.finish(
                completed=False, cancelled=True, errors=errors
            )
        click.echo("Enrichment stopped.", err=err)


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

    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    started = run_enrichment(
        storage,
        config,
        content_type,
        user_id=user_id,
        include_not_found=retry_not_found,
    )
    if started is EnrichmentStart.UNAVAILABLE:
        abort_with(ENRICHMENT_UNAVAILABLE)
    if started is EnrichmentStart.ALREADY_RUNNING:
        click.echo("Enrichment job is already running.", err=True)
        raise click.Abort()


#: Matches ``EnrichmentJobStatusResponse`` in src/web/api/_enrichment.py key for key; the
#: parity reviewer blocks on any drift between the two.
def _job_payload(status: EnrichmentJobStatus) -> dict[str, object]:
    return {
        "running": status.running,
        "completed": status.completed,
        "cancelled": status.cancelled,
        "items_processed": status.items_processed,
        "items_enriched": status.items_enriched,
        "items_failed": status.items_failed,
        "items_not_found": status.items_not_found,
        "total_items": status.total_items,
        "current_item": status.current_item,
        "content_type": status.content_type,
        "errors": status.errors,
        "elapsed_seconds": status.elapsed_seconds,
        "progress_percent": status.progress_percent,
    }


@enrichment.command("job")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def enrichment_job(ctx: click.Context, output_format: str) -> None:
    """Show the live enrichment job (mirrors GET /api/enrichment/status).

    Reads the job whatever started it — the web UI, another terminal, or a
    backgrounded run — and returns without starting or waiting for one.
    """
    status = job_status(ctx.obj["storage"])

    if output_format == "json":
        click.echo(json.dumps(_job_payload(status), indent=2))
        return

    if not status.running and status.started_at is None:
        click.echo("No enrichment job has run.")
        return

    state = "running" if status.running else _finished_state(status)
    click.echo(f"Enrichment job: {state}")
    if status.current_item:
        click.echo(f"  Current item: {status.current_item}")
    click.echo(f"  Content type: {status.content_type or 'all types'}")
    click.echo(
        f"  Progress: {status.items_processed}/{status.total_items} "
        f"({status.progress_percent:.1f}%)"
    )
    click.echo(f"  Items enriched: {status.items_enriched}")
    click.echo(f"  Items not found: {status.items_not_found}")
    click.echo(f"  Items failed: {status.items_failed}")
    click.echo(f"  Elapsed time: {status.elapsed_seconds:.1f}s")
    _echo_errors(status.errors)


@enrichment.command("stop")
@click.pass_context
def enrichment_stop(ctx: click.Context) -> None:
    """Stop the running enrichment job, whatever started it."""
    if not ctx.obj["storage"].enrichment_jobs.request_stop():
        abort_with("No enrichment job is running.")
    click.echo("Stop requested. The job ends after the item it is on.")


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


def _item_or_abort(storage: StorageManager, item_id: int, user_id: int) -> ContentItem:
    item = storage.get_content_item(item_id, user_id=user_id)
    if item is None:
        abort_with(f"Item {item_id} not found")
    return item


@enrichment.command("candidates")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option(
    "--query",
    default=None,
    help="Title to search under, in place of the item's own",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def enrichment_candidates(
    ctx: click.Context,
    item_id: int,
    query: str | None,
    user_id: int,
    output_format: str,
) -> None:
    """List every enabled provider's own search results for one item."""
    if query is not None and len(query) > MAX_SEARCH_LENGTH:
        abort_with(f"--query must be at most {MAX_SEARCH_LENGTH} characters.")

    storage = ctx.obj["storage"]
    item = _item_or_abort(storage, item_id, user_id)
    manager = EnrichmentManager(storage, ctx.obj["config"])
    payload = enrichment_candidates_to_dict(
        item_id, manager.candidates(item, query), pins_of(item.metadata)
    )

    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
        return

    rows = cast(list[dict[str, Any]], payload["candidates"])
    if not rows:
        click.echo(f"No provider offered a record for item {item_id}.")
        return
    for row in rows:
        detail = " · ".join(str(part) for part in (row["creator"], row["year"]) if part)
        click.echo(
            f"{row['provider']} {row['record_id']}: {row['title']}"
            + (f" ({detail})" if detail else "")
        )


@enrichment.command("pin")
@click.option("--id", "item_id", type=int, required=True, help="Item database ID")
@click.option("--provider", required=True, help="Provider whose record to pin")
@click.option(
    "--record",
    "record_id",
    default=None,
    help="Provider's record id; omit with --clear to resume title matching",
)
@click.option("--clear", is_flag=True, help="Drop the pin and match by title again")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def enrichment_pin(
    ctx: click.Context,
    item_id: int,
    provider: str,
    record_id: str | None,
    clear: bool,
    user_id: int,
    output_format: str,
) -> None:
    """Bind one item to one provider record, or hand it back to title search."""
    if clear == bool(record_id):
        abort_with("Pass either --record or --clear.")

    storage = ctx.obj["storage"]
    item = _item_or_abort(storage, item_id, user_id)
    manager = EnrichmentManager(storage, ctx.obj["config"])
    try:
        pinned, started = manager.pin(
            item_id, item, provider, None if clear else record_id, user_id=user_id
        )
    except PinRefused as error:
        abort_with(str(error))
    payload = enrichment_pin_to_dict(
        item_id, provider, None if clear else record_id, pinned, started
    )

    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(payload["message"])
    if started is EnrichmentStart.STARTED:
        _await_run(manager, storage, err=output_format == "json")


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
    help="Re-queue this one item, whatever left it settled",
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
    """Re-queue items the next run would otherwise skip, by provider, content
    type, or the one item that failed.
    """
    storage = ctx.obj["storage"]

    content_type = (
        ContentType.from_string(content_type_str) if content_type_str else None
    )

    provider_filter = None if provider == "all" else provider

    if item_id is not None:
        if provider_filter or content_type_str:
            abort_with("--id cannot be combined with --provider or --type.")
        _item_or_abort(storage, item_id, user_id)

    desc_parts = []
    if item_id is not None:
        desc_parts.append(f"item={item_id}")
    if provider_filter:
        desc_parts.append(f"provider={provider_filter}")
    if content_type_str:
        desc_parts.append(f"type={content_type_str}")
    desc = f" ({', '.join(desc_parts)})" if desc_parts else ""

    if not yes:
        target = f"items{desc}"
        # Stats can count a provider filter ahead of the reset but not a content
        # type, and --id already names the single item it would touch.
        if content_type_str is None and item_id is None:
            stats = storage.enrichment.stats(user_id=user_id)
            count = (
                stats["by_provider"].get(provider_filter, 0)
                if provider_filter
                else stats["resettable"]
            )
            target = f"{count} item(s){desc}"
        if not click.confirm(f"Reset enrichment status for {target}?"):
            click.echo("Aborted.")
            return

    count = storage.enrichment.reset(
        provider=provider_filter,
        content_type=content_type,
        user_id=user_id,
        content_item_id=item_id,
    )

    if item_id is None:
        click.echo(enrichment_reset_to_dict(count, None)["message"])
        return

    manager = EnrichmentManager(storage, ctx.obj["config"])
    started = manager.start_enrichment(user_id=user_id, content_item_id=item_id)
    click.echo(enrichment_reset_to_dict(count, started)["message"])
    if started is EnrichmentStart.STARTED:
        _await_run(manager, storage, err=False)
