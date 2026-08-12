"""Main CLI entry point."""

import sys
from pathlib import Path

import click

from src.cli.commands import (
    auth,
    chat,
    complete,
    enrichment,
    library,
    memory,
    preferences,
    profile,
    recommend,
    settings,
    source,
    status,
    update,
)
from src.config.service import (
    create_llm_components,
    create_recommendation_engine,
    create_storage_manager,
    load_config,
)
from src.storage.credential_migration import migrate_config_credentials
from src.storage.global_secrets import migrate_config_secrets
from src.storage.settings_migration import migrate_config_settings
from src.storage.source_migration import (
    migrate_source_attribution,
    migrate_source_config_plugins,
    migrate_source_labels,
)


@click.group()
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),  # type: ignore[type-var]
    default=None,
    help="Path to configuration file",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None) -> None:
    """Recommendinator CLI - Get intelligent recommendations based on your consumption history."""
    ctx.ensure_object(dict)

    # Load configuration
    try:
        ctx.obj["config"] = load_config(config)
    except FileNotFoundError as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(1)

    # Initialize components
    try:
        ctx.obj["storage"] = create_storage_manager(ctx.obj["config"])
        # Assemble the effective global config (const default < YAML < DB) so
        # the database wins over YAML for the rest of the invocation.
        migrate_config_settings(ctx.obj["config"], ctx.obj["storage"])
        # Per-source credentials, on every command as the web app does it on
        # every startup: while it ran inside ``update`` alone, ``auth status``
        # read a file-held token as not connected until a sync had happened.
        migrate_config_credentials(ctx.obj["config"], ctx.obj["storage"])
        # Relocate global provider secrets (api keys) into encrypted storage,
        # stripping them from the in-memory plaintext config.
        migrate_config_secrets(ctx.obj["config"], ctx.obj["storage"])
        ctx.obj["llm_client"], ctx.obj["embedding_gen"], ctx.obj["rec_gen"] = (
            create_llm_components(ctx.obj["config"])
        )
        ctx.obj["engine"] = create_recommendation_engine(
            ctx.obj["storage"],
            ctx.obj["embedding_gen"],
            ctx.obj["rec_gen"],
            ctx.obj["config"],
        )
    except Exception as error:
        click.echo(f"Error initializing components: {error}", err=True)
        sys.exit(1)

    # Relabel stored goodreads source values and plugin names after the plugin
    # rename. Runs for every CLI command so a CLI-only user is migrated even if
    # they never run ``update`` (the web app runs both on startup/reload).
    migrate_source_labels(ctx.obj["storage"])
    migrate_source_config_plugins(ctx.obj["storage"])
    # After the plugin relabel, so a source config that still said
    # ``goodreads`` is matched under the name the registry now serves.
    migrate_source_attribution(ctx.obj["config"], ctx.obj["storage"])


# Register commands
cli.add_command(auth)
cli.add_command(chat)
cli.add_command(status)
cli.add_command(recommend)
cli.add_command(update)
cli.add_command(complete)
cli.add_command(preferences)
cli.add_command(enrichment)
cli.add_command(library)
cli.add_command(memory)
cli.add_command(profile)
cli.add_command(source)
cli.add_command(settings)


if __name__ == "__main__":
    cli()
