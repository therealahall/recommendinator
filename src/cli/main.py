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
    status,
    update,
)
from src.cli.config import (
    create_llm_components,
    create_recommendation_engine,
    create_storage_manager,
    load_config,
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


if __name__ == "__main__":
    cli()
