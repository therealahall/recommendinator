"""The ``update`` command."""

from __future__ import annotations

import logging
import threading

import click

from src.cli._shared import abort_after_failure
from src.ingestion.sync import (
    MAX_WORKERS_CEILING,
    execute_multi_source_sync,
    resolve_max_workers,
)
from src.sources.service import (
    ResolvedInput,
    get_available_sync_sources,
    misconfigured_detail,
    redact_credentials,
    resolve_inputs,
    source_plugin_not_loaded,
    unusable_detail,
    validate_source_config,
)
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

#: What a failed sync sets on the web's job record.
SYNC_FAILED = "Sync failed due to an internal error"


def _refusal(entry: ResolvedInput, errors: list[str]) -> str:
    """Name the settings, as the sync endpoint does; log the plugin's reason.

    A plugin quotes the path it looked for, which is filesystem layout the
    terminal has no business printing.
    """
    logger.warning(
        "Sync config validation failed for %s: %s",
        sanitize_for_log(entry.source_id),
        sanitize_for_log(
            redact_credentials("; ".join(errors), entry.plugin, entry.config)
        ),
    )
    return misconfigured_detail(entry.plugin, errors)


@click.command()
@click.option(
    "--source",
    default="all",
    help="Data source to update (use 'list' to see available sources, 'all' for everything)",
)
@click.option(
    "--workers",
    type=click.IntRange(1, MAX_WORKERS_CEILING),
    default=None,
    help=(
        f"Number of sources to sync in parallel (1-{MAX_WORKERS_CEILING}). "
        "Defaults to config.sync.max_workers (or 4 if unset). "
        "Use 1 for sequential."
    ),
)
@click.pass_context
def update(ctx: click.Context, source: str, workers: int | None) -> None:
    """Update data from configured sources."""
    storage = ctx.obj["storage"]
    config = ctx.obj["config"]

    # Handle 'list' to show available sources (read-only — no migration needed).
    # Use the DB-aware helper so sources that live only in the database (created
    # via ``source create`` or the web Add-source modal, never in config.yaml)
    # are discoverable — otherwise a user can't find the id to pass to --source.
    if source == "list":
        available = get_available_sync_sources(config, storage=storage)
        if not available:
            click.echo("No sources configured.")
            return

        click.echo("Available sources:")
        for info in available:
            status = (
                f"unusable: {unusable_detail(info.plugin_not_loaded)}"
                if info.plugin_not_loaded is not None
                else "enabled" if info.enabled else "disabled"
            )
            click.echo(f"  {info.id:20s} plugin={info.plugin_display_name} [{status}]")
        return

    # Check if auto-enrichment is enabled
    enrichment_config = config.get("enrichment", {})
    auto_enrich = enrichment_config.get("enabled", False) and enrichment_config.get(
        "auto_enrich_on_sync", False
    )

    # Determine which sources to sync
    if source == "all":
        resolved = resolve_inputs(config, storage=storage)
        if not resolved:
            click.echo(
                "No sources enabled in config. Use --source list to see available sources."
            )
            return
    else:
        # Resolve a single source through the DB-aware path (mirrors the web
        # /update endpoint) so a source that lives only in the database — with
        # no config.yaml entry — is synced, not rejected as "unknown". The
        # filter also drops disabled sources (resolve_inputs excludes them).
        resolved = [
            resolved_entry
            for resolved_entry in resolve_inputs(config, storage=storage)
            if resolved_entry.source_id == source
        ]
        if not resolved:
            not_loaded = source_plugin_not_loaded(source, config, storage=storage)
            click.echo(
                (
                    f"Error: {unusable_detail(not_loaded)}"
                    if not_loaded is not None
                    else f"Error: Unknown or disabled source '{source}'. "
                    "Use --source list to see available sources."
                ),
                err=True,
            )
            raise click.Abort()

        validation_errors = validate_source_config(source, config, storage=storage)
        if validation_errors:
            click.echo(f"Error: {_refusal(resolved[0], validation_errors)}", err=True)
            raise click.Abort()

    # Filter out resolved entries that fail validation (preserves the
    # per-source error reporting from the legacy sequential path).
    valid: list[ResolvedInput] = []
    for resolved_entry in resolved:
        validation_errors = validate_source_config(
            resolved_entry.source_id, config, storage=storage
        )
        if validation_errors:
            click.echo(
                f"  {resolved_entry.plugin.display_name}: Error: "
                f"{_refusal(resolved_entry, validation_errors)}",
                err=True,
            )
            continue
        valid.append(resolved_entry)

    if not valid:
        click.echo(
            "No items were updated. Check your configuration and source settings."
        )
        return

    max_workers = resolve_max_workers(config, override=workers)

    click.echo(
        f"Updating data from {', '.join(entry.source_id for entry in valid)}"
        + (f" (workers={max_workers})" if max_workers > 1 else "")
        + "...",
        err=True,
    )

    # Click's echo is not thread-safe and progress messages from parallel
    # workers can interleave; serialise output via a lock.
    output_lock = threading.Lock()
    last_reported: dict[str, int] = {}

    def cli_progress(
        items_processed: int,
        total_items: int | None,
        current_item: str | None,
        current_source: str | None = None,
    ) -> None:
        if not (total_items and items_processed > 0 and items_processed % 10 == 0):
            return
        prefix = f"[{current_source}] " if current_source else ""
        with output_lock:
            # Suppress duplicate "Processed N/M" lines a worker may emit
            # if its callback fires twice for the same threshold.
            key = current_source or ""
            if last_reported.get(key) == items_processed:
                return
            last_reported[key] = items_processed
            click.echo(
                f"    {prefix}Processed {items_processed}/{total_items}...", err=True
            )

    try:
        results = execute_multi_source_sync(
            sources=[(entry.plugin, entry.config) for entry in valid],
            storage_manager=storage,
            progress_callback=cli_progress,
            mark_for_enrichment=auto_enrich,
            max_workers=max_workers,
        )

        total_count = 0
        for result, resolved_entry in zip(results, valid, strict=True):
            click.echo(
                f"  Updated {result.items_synced} items from "
                f"{resolved_entry.plugin.display_name} ({resolved_entry.source_id})"
            )
            for error in result.errors:
                click.echo(f"    Warning: {error}", err=True)
            total_count += result.items_synced

        if total_count == 0:
            click.echo(
                "No items were updated. Check your configuration and source settings."
            )
        else:
            click.echo(f"Total: {total_count} items updated.")

    except Exception as error:
        abort_after_failure(ctx, SYNC_FAILED, error)
