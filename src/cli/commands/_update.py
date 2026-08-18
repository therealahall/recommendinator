"""The ``update`` command."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

import click

from src.cli._shared import abort_after_failure
from src.config.service import auto_enrich_enabled
from src.ingestion.sync import (
    ALL_SOURCES_LABEL,
    MAX_WORKERS_CEILING,
    SyncResult,
    execute_multi_source_sync,
    resolve_max_workers,
    sync_run_failed,
    sync_run_recorder,
)
from src.sources.service import (
    ResolvedInput,
    build_sources_view,
    get_available_sync_sources,
    misconfigured_detail,
    redact_credentials,
    resolve_inputs,
    source_plugin_not_loaded,
    unusable_detail,
    validate_source_config,
)
from src.utils.text import humanize_source_id, sanitize_for_log

logger = logging.getLogger(__name__)

#: What a failed sync sets on the web's job record.
SYNC_FAILED = "Sync failed due to an internal error"


def _source_view(result: SyncResult) -> dict[str, Any]:
    """One source's counts, shaped as ``SyncSourceProgressResponse``."""
    return {
        "source": result.source_name,
        "items_processed": result.items_synced,
        "total_items": result.total_items,
        "current_item": None,
        "progress_percent": (
            int(result.items_synced * 100 / result.total_items)
            if result.total_items
            else None
        ),
        "items_added": result.items_added,
        "items_updated": result.items_updated,
        "items_unchanged": result.items_unchanged,
    }


def _status_view(
    source_label: str,
    results: list[SyncResult],
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    """The finished run, shaped as ``GET /api/sync/status`` answers.

    A synchronous run is one job that is already over, so the aggregate
    status is idle and the job's own is terminal.
    """
    items_processed = sum(result.items_synced for result in results)
    total_items = sum(result.total_items for result in results)
    errors = [
        {"source": result.source_name, "message": message}
        for result in results
        for message in result.errors
    ]
    failed = sync_run_failed(items_processed, errors)
    return {
        "status": "idle",
        "jobs": [
            {
                "source": source_label,
                "status": "failed" if failed else "completed",
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "items_processed": items_processed,
                "total_items": total_items or None,
                "current_item": None,
                "current_source": None,
                "error_message": errors[0]["message"] if failed else None,
                "progress_percent": (
                    int(items_processed * 100 / total_items) if total_items else None
                ),
                "items_added": sum(result.items_added for result in results),
                "items_updated": sum(result.items_updated for result in results),
                "items_unchanged": sum(result.items_unchanged for result in results),
                "errors": errors,
                "sources": [_source_view(result) for result in results],
            }
        ],
    }


def _counts(view: dict[str, Any]) -> str:
    """The added/updated/unchanged split both the CLI lines carry."""
    return (
        f"{view['items_processed']} of {view['total_items'] or 0} items saved "
        f"({view['items_added']} added, {view['items_updated']} updated, "
        f"{view['items_unchanged']} unchanged)"
    )


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
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def update(
    ctx: click.Context, source: str, workers: int | None, output_format: str
) -> None:
    """Update data from configured sources (mirrors POST /api/update)."""
    storage = ctx.obj["storage"]
    config = ctx.obj["config"]

    def report_nothing_ran(message: str) -> None:
        """Answer a run that had no source to sync, in the asked-for format."""
        if output_format == "json":
            click.echo(json.dumps({"status": "idle", "jobs": []}, indent=2))
        else:
            click.echo(message)

    # Handle 'list' to show available sources (read-only — no migration needed).
    # Use the DB-aware helper so sources that live only in the database (created
    # via ``source create`` or the web Add-source modal, never in config.yaml)
    # are discoverable — otherwise a user can't find the id to pass to --source.
    if source == "list":
        available = get_available_sync_sources(config, storage=storage)
        if output_format == "json":
            click.echo(json.dumps(build_sources_view(available), indent=2))
            return
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
            click.echo(
                f"  {info.id:20s} plugin={info.plugin_display_name} [{status}] "
                f"cadence={info.sync_interval} last={info.last_run_at or '—'} "
                f"({info.last_run_status or '—'})"
            )
        return

    auto_enrich = auto_enrich_enabled(config)

    # Determine which sources to sync
    if source == "all":
        resolved = resolve_inputs(config, storage=storage)
        if not resolved:
            report_nothing_ran(
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
        report_nothing_ran(
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

    started_at = datetime.now()
    try:
        results = execute_multi_source_sync(
            sources=[(entry.plugin, entry.config) for entry in valid],
            storage_manager=storage,
            progress_callback=cli_progress,
            result_callback=sync_run_recorder(storage),
            mark_for_enrichment=auto_enrich,
            max_workers=max_workers,
        )
    except Exception as error:
        abort_after_failure(ctx, SYNC_FAILED, error)

    label = ALL_SOURCES_LABEL if source == "all" else humanize_source_id(source)
    view = _status_view(label, results, started_at, datetime.now())
    job = view["jobs"][0]

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    for source_view, result, entry in zip(job["sources"], results, valid, strict=True):
        click.echo(
            f"  {entry.plugin.display_name} ({entry.source_id}): {_counts(source_view)}"
        )
        for message in result.errors:
            click.echo(f"    Warning: {message}", err=True)

    if job["items_processed"] == 0:
        click.echo(
            "No items were updated. Check your configuration and source settings."
        )
    else:
        click.echo(f"Total: {_counts(job)}.")
