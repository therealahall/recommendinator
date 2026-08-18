"""The ``import`` command, mirroring POST /api/import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from src.cli._shared import abort_with, require_storage
from src.ingestion.importers.base import ImporterError
from src.ingestion.importers.registry import IMPORTERS
from src.ingestion.importers.service import (
    ImportResult,
    decode_import_text,
    import_file,
)
from src.models.content import DEFAULT_USER_ID, ContentType, get_enum_value


def _import_view(result: ImportResult, filename: str) -> dict[str, Any]:
    """What one import did, shaped as ``ImportResponse``."""
    return {
        "importer": result.importer,
        "content_type": (
            get_enum_value(result.content_type) if result.content_type else None
        ),
        "filename": filename,
        "added": result.added,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "skipped": result.skipped,
        "failed": result.failed,
        "total_rows": result.total_rows,
        "errors": result.errors,
    }


@click.command("import")
# An ordinary path to a local file, not a source path: it does not go through
# ``resolve_source_path`` and ``security.allowed_source_roots`` does not bound
# it. Whoever runs this already has the shell those roots exist to contain.
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),  # type: ignore[type-var]
)
@click.option(
    "--importer",
    "importer_name",
    type=click.Choice([entry.name for entry in IMPORTERS]),
    required=True,
    help="Format to parse the file as",
)
@click.option(
    "--content-type",
    "content_type_str",
    type=click.Choice([member.value for member in ContentType]),
    default=None,
    help="Content type, for a format that does not decide one itself",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def import_command(
    ctx: click.Context,
    path: Path,
    importer_name: str,
    content_type_str: str | None,
    output_format: str,
) -> None:
    """Import a file in one shot (mirrors POST /api/import)."""
    storage = require_storage(ctx)
    enrichment_config = ctx.obj["config"].get("enrichment", {})
    importer = next(entry for entry in IMPORTERS if entry.name == importer_name)

    try:
        result = import_file(
            storage,
            DEFAULT_USER_ID,
            decode_import_text(path.read_bytes()),
            importer,
            ContentType(content_type_str) if content_type_str else None,
            mark_for_enrichment=enrichment_config.get("enabled", False)
            and enrichment_config.get("auto_enrich_on_sync", False),
        )
    except ImporterError as error:
        # Verbatim, as the import endpoint answers it: the message quotes the
        # operator's own file, which is what makes it actionable.
        abort_with(str(error))

    if output_format == "json":
        click.echo(json.dumps(_import_view(result, path.name), indent=2))
        return

    # stderr for the prose, as the sync progress lines do it: stdout is the
    # data channel ``--format json`` writes, and a caller may read both.
    click.echo(
        f"Added {result.added}, updated {result.updated}, "
        f"unchanged {result.unchanged}, skipped {result.skipped}, "
        f"failed {result.failed}. {result.total_rows} rows read.",
        err=True,
    )
    for message in result.errors:
        click.echo(message, err=True)
