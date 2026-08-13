"""The ``source`` group, mirroring the /api/sync/sources endpoints."""

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
from src.ingestion.plugin_base import SourcePlugin
from src.sources.service import (
    SourceConfigError,
    build_config_view,
    build_schema_view,
    clear_source_secret_value,
    create_source,
    delete_source,
    field_type_name,
    get_available_sync_sources,
    list_available_plugins,
    migrate_source,
    resolve_source_plugin,
    set_source_enabled_state,
    set_source_secret_value,
    update_source_config_values,
)

_SOURCE_DEFAULT_USER_ID = 1

#: How this group words a value its field type cannot represent.
_FIELD_TYPE_ERRORS = {
    "bool": "Field '{name}' is bool — pass true/false",
    "int": "Field '{name}' must be an integer",
    "float": "Field '{name}' must be a number",
}


def _resolve_cli_plugin(ctx: click.Context, source_id: str) -> SourcePlugin:
    plugin = resolve_source_plugin(
        source_id,
        ctx.obj.get("config"),
        ctx.obj.get("storage"),
        user_id=_SOURCE_DEFAULT_USER_ID,
    )
    if plugin is None:
        abort_with(f"Unknown source: {source_id}")
    return plugin


def _config_view(
    ctx: click.Context, source_id: str, plugin: SourcePlugin
) -> dict[str, Any]:
    """The SourceConfigResponse-shaped view a mutation hands back."""
    return build_config_view(
        source_id,
        plugin,
        ctx.obj.get("config"),
        require_storage(ctx),
        user_id=_SOURCE_DEFAULT_USER_ID,
    )


@click.group()
def source() -> None:
    """Manage data source configuration."""


@source.command("list")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_list(ctx: click.Context, output_format: str) -> None:
    """List configured data sources (mirrors GET /api/sync/sources)."""
    config = ctx.obj.get("config") or {}
    storage = ctx.obj.get("storage")
    sources = get_available_sync_sources(
        config, storage=storage, user_id=_SOURCE_DEFAULT_USER_ID
    )
    payload = [
        {
            "id": entry.id,
            "display_name": entry.display_name,
            "plugin_display_name": entry.plugin_display_name,
            "enabled": entry.enabled,
        }
        for entry in sources
    ]

    if output_format == "json":
        click.echo(json.dumps(payload, indent=2))
        return

    if not payload:
        click.echo("No sync sources configured.")
        return

    rows = [
        [
            item["id"],
            item["display_name"],
            item["plugin_display_name"],
            "yes" if item["enabled"] else "no",
        ]
        for item in payload
    ]
    click.echo(
        tabulate(
            rows,
            headers=["ID", "Display Name", "Plugin", "Enabled"],
            tablefmt="grid",
        )
    )


@source.command("show")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_show(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Show current values for a source (mirrors GET /api/sync/sources/<id>/config)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = ctx.obj.get("storage")
    view = build_config_view(
        source_id,
        plugin,
        ctx.obj.get("config"),
        storage,
        user_id=_SOURCE_DEFAULT_USER_ID,
    )

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    rows: list[list[str]] = [
        ["plugin", view["plugin"]],
        ["enabled", str(view["enabled"])],
        ["migrated", str(view["migrated"])],
        ["migrated_at", str(view["migrated_at"] or "—")],
    ]
    for name, value in view["field_values"].items():
        rows.append([name, json.dumps(value)])
    for name, is_set in view["secret_status"].items():
        rows.append([f"{name} (secret)", "set" if is_set else "unset"])
    click.echo(tabulate(rows, headers=["Field", "Value"], tablefmt="grid"))


@source.command("schema")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_schema(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Show the plugin schema for a source (mirrors GET /api/sync/sources/<id>/schema)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    view = build_schema_view(source_id, plugin)

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    rows = [
        [
            field["name"],
            field["field_type"],
            "yes" if field["required"] else "no",
            "yes" if field["sensitive"] else "no",
            field["description"],
        ]
        for field in view["fields"]
    ]
    click.echo(
        tabulate(
            rows,
            headers=["Field", "Type", "Required", "Sensitive", "Description"],
            tablefmt="grid",
        )
    )


@source.command("migrate")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_migrate(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Migrate a YAML source entry into the database (idempotent)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)
    try:
        result = migrate_source(
            source_id,
            plugin,
            ctx.obj.get("config"),
            storage,
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        abort_with(error.message)

    if output_format == "json":
        click.echo(json.dumps(result, indent=2))
        return

    click.echo(f"Migrated source '{source_id}' to the database.")
    if result["fields_migrated"]:
        click.echo(f"  Fields: {', '.join(result['fields_migrated'])}")
    if result["secrets_migrated"]:
        click.echo(f"  Secrets: {', '.join(result['secrets_migrated'])}")


@source.command("enable")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_enable(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Enable a migrated source (mirrors PUT /api/sync/sources/<id>/enabled)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)
    try:
        set_source_enabled_state(
            source_id, storage, True, user_id=_SOURCE_DEFAULT_USER_ID
        )
    except SourceConfigError as error:
        abort_with(error.message)
    emit_view(
        output_format,
        lambda: _config_view(ctx, source_id, plugin),
        f"Enabled source '{source_id}'.",
    )


@source.command("disable")
@click.argument("source_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_disable(ctx: click.Context, source_id: str, output_format: str) -> None:
    """Disable a migrated source (mirrors PUT /api/sync/sources/<id>/enabled)."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)
    try:
        set_source_enabled_state(
            source_id, storage, False, user_id=_SOURCE_DEFAULT_USER_ID
        )
    except SourceConfigError as error:
        abort_with(error.message)
    emit_view(
        output_format,
        lambda: _config_view(ctx, source_id, plugin),
        f"Disabled source '{source_id}'.",
    )


@source.command("set")
@click.argument("source_id")
@click.argument("field_name")
@click.argument("value")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_set(
    ctx: click.Context,
    source_id: str,
    field_name: str,
    value: str,
    output_format: str,
) -> None:
    """Set a non-sensitive config field for a migrated source."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)

    schema = {f.name: f for f in plugin.get_config_schema()}
    field = schema.get(field_name)
    if field is None:
        abort_with(f"Unknown field: {field_name}")
    if field.sensitive:
        abort_with(f"Field '{field_name}' is sensitive — use 'source set-secret'")

    try:
        coerced = coerce_value(field_type_name(field.field_type), value)
    except ValueCoercionError as error:
        abort_with(_FIELD_TYPE_ERRORS[error.value_type].format(name=field.name))
    try:
        update_source_config_values(
            source_id,
            plugin,
            storage,
            {field_name: coerced},
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        abort_with(error.message)
    emit_view(
        output_format,
        lambda: _config_view(ctx, source_id, plugin),
        f"Set {source_id}.{field_name} = {coerced!r}",
    )


@source.command("apply")
@click.argument("source_id")
@click.option(
    "--from-json",
    "from_json",
    required=True,
    help=(
        "Path to a JSON file containing a values dict, or '-' to read from "
        "stdin. Mirrors PUT /api/sync/sources/<id>/config — applies all "
        "fields atomically."
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
def source_apply(
    ctx: click.Context, source_id: str, from_json: str, output_format: str
) -> None:
    """Apply a JSON dict of non-sensitive fields atomically (bulk update).

    The web ``PUT /api/sync/sources/<id>/config`` endpoint accepts an
    arbitrary ``values`` dict and updates every key in a single
    transaction. This command mirrors that path so scripts can perform
    multi-field updates without N round-trip CLI invocations.
    """
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)

    values = read_json_payload(from_json)

    try:
        update_source_config_values(
            source_id, plugin, storage, values, user_id=_SOURCE_DEFAULT_USER_ID
        )
    except SourceConfigError as error:
        abort_with(error.message)

    emit_view(
        output_format,
        lambda: _config_view(ctx, source_id, plugin),
        f"Applied {len(values)} field(s) to {source_id}.",
    )


@source.command("set-secret")
@click.argument("source_id")
@click.argument("field_name")
@click.pass_context
def source_set_secret(ctx: click.Context, source_id: str, field_name: str) -> None:
    """Store a sensitive field's value.

    Reads from the ``RECOMMENDINATOR_SECRET_VALUE`` environment variable for
    non-interactive use (env vars are not exposed in shell history or in the
    process list to other users); otherwise prompts with hidden input.
    """
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)

    value = os.environ.get(SECRET_VALUE_ENV)
    if value is None:
        value = click.prompt(
            f"New value for {source_id}.{field_name}",
            hide_input=True,
            confirmation_prompt=False,
        )

    try:
        set_source_secret_value(
            source_id,
            plugin,
            storage,
            field_name,
            value,
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        abort_with(error.message)
    click.echo(f"Stored secret for {source_id}.{field_name}.")


@source.command("clear-secret")
@click.argument("source_id")
@click.argument("field_name")
@click.pass_context
def source_clear_secret(ctx: click.Context, source_id: str, field_name: str) -> None:
    """Delete a sensitive field's stored value."""
    plugin = _resolve_cli_plugin(ctx, source_id)
    storage = require_storage(ctx)
    try:
        clear_source_secret_value(
            source_id,
            plugin,
            storage,
            field_name,
            user_id=_SOURCE_DEFAULT_USER_ID,
        )
    except SourceConfigError as error:
        abort_with(error.message)
    click.echo(f"Cleared secret for {source_id}.{field_name}.")


@source.command("plugins")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_plugins(ctx: click.Context, output_format: str) -> None:
    """List every registered source plugin (mirrors GET /api/plugins)."""
    plugins = list_available_plugins()
    if output_format == "json":
        click.echo(json.dumps(plugins, indent=2))
        return

    if not plugins:
        click.echo("No source plugins registered.")
        return

    rows = [
        [
            p["name"],
            p["display_name"],
            ",".join(p["content_types"]),
            "yes" if p["requires_api_key"] else "no",
            "yes" if p["requires_network"] else "no",
        ]
        for p in plugins
    ]
    click.echo(
        tabulate(
            rows,
            headers=["Name", "Display Name", "Content Types", "API Key", "Network"],
            tablefmt="grid",
        )
    )


@source.command("create")
@click.argument("source_id")
@click.argument("plugin_name")
@click.option(
    "--from-json",
    "from_json",
    default=None,
    help=(
        "Path to a JSON file with initial non-sensitive field values, or "
        "'-' for stdin. Sensitive fields must be set via "
        "``source set-secret`` after creation."
    ),
)
@click.option("--enabled/--disabled", default=True, help="Initial enabled state.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def source_create(
    ctx: click.Context,
    source_id: str,
    plugin_name: str,
    from_json: str | None,
    enabled: bool,
    output_format: str,
) -> None:
    """Create a new DB-backed source (mirrors POST /api/sync/sources)."""
    storage = require_storage(ctx)

    values: dict[str, Any] = (
        read_json_payload(from_json) if from_json is not None else {}
    )

    try:
        view = create_source(
            source_id,
            plugin_name,
            values,
            storage,
            enabled=enabled,
            user_id=_SOURCE_DEFAULT_USER_ID,
            config=ctx.obj["config"],
        )
    except SourceConfigError as error:
        abort_with(error.message)

    if output_format == "json":
        click.echo(json.dumps(view, indent=2))
        return

    click.echo(
        f"Created source '{source_id}' (plugin={plugin_name}, "
        f"enabled={'yes' if enabled else 'no'})."
    )


@source.command("remove")
@click.argument("source_id")
@click.option(
    "--yes",
    "skip_confirm",
    is_flag=True,
    default=False,
    help="Skip the destructive confirmation prompt (for scripting).",
)
@click.pass_context
def source_remove(ctx: click.Context, source_id: str, skip_confirm: bool) -> None:
    """Delete a DB-backed source and any stored credentials."""
    storage = require_storage(ctx)
    if not skip_confirm and not click.confirm(
        f"Remove source '{source_id}' and clear its credentials?",
        default=False,
    ):
        click.echo("Aborted.")
        return
    try:
        delete_source(
            source_id,
            storage,
            user_id=_SOURCE_DEFAULT_USER_ID,
            config=ctx.obj["config"],
        )
    except SourceConfigError as error:
        abort_with(error.message)
    click.echo(f"Removed source '{source_id}'.")
