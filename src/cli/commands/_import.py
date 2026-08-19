"""The ``import`` command and its templates, mirroring the upload endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from src.cli._shared import abort_with, require_storage
from src.config.service import auto_enrich_enabled
from src.ingestion.import_templates import (
    TEMPLATE_IMPORTERS,
    ImportTemplate,
    TemplatesUnavailable,
    available_templates,
    find_template,
    read_template,
)
from src.ingestion.importers.base import Importer, ImporterError
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
        "notes": result.notes,
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
    importer = next(entry for entry in IMPORTERS if entry.name == importer_name)

    try:
        result = import_file(
            storage,
            DEFAULT_USER_ID,
            decode_import_text(path.read_bytes()),
            importer,
            ContentType(content_type_str) if content_type_str else None,
            mark_for_enrichment=auto_enrich_enabled(ctx.obj["config"]),
        )
    except ImporterError as error:
        # Verbatim, as the import endpoint answers it: the message quotes the
        # operator's own file, which is what makes it actionable.
        abort_with(str(error))

    if output_format == "json":
        click.echo(json.dumps(_import_view(result, path.name), indent=2))
        return

    # stdout, as ``update`` puts its counts there: in table mode the counts and
    # the lines that missed are the output, not progress chatter about it.
    click.echo(
        f"Added {result.added}, updated {result.updated}, "
        f"unchanged {result.unchanged}, skipped {result.skipped}, "
        f"failed {result.failed}. {result.total_rows} rows read."
    )
    # Ahead of the misses, so the tally that closes them stays last: a note
    # printed after "… and 5 more" reads as one of the rows it omitted.
    for note in result.notes:
        click.echo(note)
    for message in result.errors:
        click.echo(message)


def _importer_view(importer: Importer) -> dict[str, Any]:
    """One import format, shaped as ``ImporterResponse``."""
    return {
        "name": importer.name,
        "display_name": importer.display_name,
        "description": importer.description,
        "requires_content_type": not importer.content_types,
    }


@click.command("import-formats")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
def import_formats(output_format: str) -> None:
    """List every import format (mirrors GET /api/importers)."""
    if output_format == "json":
        click.echo(json.dumps([_importer_view(entry) for entry in IMPORTERS], indent=2))
        return

    click.echo("Available import formats:")
    for entry in IMPORTERS:
        needs = "" if entry.content_types else " (needs --content-type)"
        click.echo(
            f"  {entry.name:16s} {entry.display_name:28s} {entry.description}{needs}"
        )


def _template_view(template: ImportTemplate) -> dict[str, Any]:
    """One template, shaped as ``ImportTemplateResponse``."""
    return {
        "importer": template.importer,
        "content_type": template.content_type,
        "filename": template.filename,
    }


def _list_templates(output_format: str) -> None:
    """Answer as ``GET /api/import/templates`` does, in the asked-for format."""
    try:
        templates = available_templates()
    except TemplatesUnavailable as error:
        abort_with(str(error))

    if output_format == "json":
        click.echo(json.dumps([_template_view(entry) for entry in templates], indent=2))
        return

    if not templates:
        click.echo("No import templates found.")
        return

    click.echo("Available import templates:")
    for entry in templates:
        click.echo(f"  {entry.importer:16s} {entry.content_type:12s} {entry.filename}")


@click.command("import-template")
@click.option(
    "--importer",
    "importer_name",
    type=click.Choice(TEMPLATE_IMPORTERS),
    default=None,
    help="Format to write a template for",
)
@click.option(
    "--content-type",
    "content_type_str",
    type=click.Choice([member.value for member in ContentType]),
    default=None,
    help="Content type the template holds",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),  # type: ignore[type-var]
    default=None,
    help="Output file path (default: stdout)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    # Unset rather than "table", so ``--format json`` named while writing a
    # template is refused instead of dropped, answering raw bytes to a caller
    # that asked for JSON.
    default=None,
    help="Output format for the listing (default: table)",
)
def import_template(
    importer_name: str | None,
    content_type_str: str | None,
    output_path: Path | None,
    output_format: str | None,
) -> None:
    """Write an import template, or list them (mirrors GET /api/import/templates)."""
    if importer_name is None and content_type_str is None and output_path is None:
        _list_templates(output_format or "table")
        return
    if importer_name is None or content_type_str is None:
        abort_with(
            "Name both --importer and --content-type, or neither to list "
            "the templates available."
        )
    if output_format is not None:
        abort_with("--format describes the template listing. Drop it to write one.")

    try:
        template = find_template(importer_name, content_type_str)
    except TemplatesUnavailable as error:
        abort_with(str(error))
    if template is None:
        abort_with(f"No import template for {importer_name} and {content_type_str}.")

    data = read_template(template)
    if output_path:
        output_path.write_bytes(data)
        click.echo(f"Wrote {template.filename} to {output_path}")
    else:
        click.echo(data, nl=False)
