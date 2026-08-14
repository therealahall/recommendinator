"""Main CLI entry point."""

import logging
import sys
from pathlib import Path
from typing import Any

import click

from src.cli.commands import (
    account,
    auth,
    complete,
    enrichment,
    library,
    preferences,
    profile,
    recommend,
    settings,
    source,
    status,
    update,
)
from src.config.service import (
    create_recommendation_engine,
    create_storage_manager,
    load_config,
)
from src.storage.credential_migration import migrate_config_credentials
from src.storage.global_secrets import migrate_config_secrets
from src.storage.settings_migration import migrate_config_settings
from src.utils import logging as log_config
from src.utils.text import exception_for_log, strip_lone_surrogates


class SurrogateFreeGroup(click.Group):
    """``surrogateescape`` turns an undecodable byte into a lone surrogate,
    which ``click.echo`` and a SQLite bind both raise on. Guarded here rather
    than per option, so the next option added is not the next crash.
    """

    def make_context(
        self,
        info_name: str,
        args: list[str],
        parent: click.Context | None = None,
        **extra: Any,
    ) -> click.Context:
        # Cost accepted: a path whose bytes are undecodable in the locale
        # encoding is refused as missing rather than opened.
        return super().make_context(
            info_name, [strip_lone_surrogates(arg) for arg in args], parent, **extra
        )


@click.group(cls=SurrogateFreeGroup)
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),  # type: ignore[type-var]
    default=None,
    help="Path to configuration file",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Also print the underlying error, not just the line in the log",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    """Recommendinator CLI - Get intelligent recommendations based on your consumption history."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    # Both exits below sanitize rather than saying "check the logs": there is
    # no log yet. ``configure_logging`` runs after the storage built below,
    # which is inside the second guard.
    try:
        ctx.obj["config"] = load_config(config)
    except FileNotFoundError as error:
        click.echo(f"Error: {exception_for_log(error)}", err=True)
        sys.exit(1)

    # Initialize components
    try:
        ctx.obj["storage"] = create_storage_manager(ctx.obj["config"])
        # Assemble the effective global config (const default < YAML < DB) so
        # the database wins over YAML for the rest of the invocation.
        migrate_config_settings(ctx.obj["config"], ctx.obj["storage"])
        # After the overlay, since logging.level and logging.file are DB-backed
        # settings; before the migrations below, whose diagnostics went to a
        # root logger with no handler. stderr, because stdout is the data
        # channel `--format json` and `library export` write to.
        try:
            log_config.configure_logging(
                ctx.obj["config"],
                console_stream=sys.stderr,
                console_tracebacks=False,
                console_floor=logging.WARNING,
            )
        except OSError as error:
            # `logs/` is routinely a bind mount the web container owns, and the
            # CLI is run by someone else. Losing the log is not losing the
            # command, so this reports and carries on rather than exiting 1.
            click.echo(
                f"Warning: no log file for this run: {exception_for_log(error)}",
                err=True,
            )
            # Degrading to no handler at all hands the records to
            # `logging.lastResort`, which prints the tracebacks this console
            # withholds — on the one run with no log file to read them from.
            log_config.configure_console_only(
                ctx.obj["config"],
                console_stream=sys.stderr,
                console_tracebacks=False,
                console_floor=logging.WARNING,
            )
        # Per-source credentials, on every command as the web app does it on
        # every startup: while it ran inside ``update`` alone, ``auth status``
        # read a file-held token as not connected until a sync had happened.
        migrate_config_credentials(ctx.obj["config"], ctx.obj["storage"])
        # Relocate global provider secrets (api keys) into encrypted storage,
        # stripping them from the in-memory plaintext config.
        migrate_config_secrets(ctx.obj["config"], ctx.obj["storage"])
        ctx.obj["engine"] = create_recommendation_engine(
            ctx.obj["storage"], ctx.obj["config"]
        )
    except Exception as error:
        # Blanket, and ``migrate_config_secrets`` is under it: the one fault
        # here whose words could quote a credential.
        click.echo(
            f"Error initializing components: {exception_for_log(error)}", err=True
        )
        sys.exit(1)


# Register commands
cli.add_command(account)
cli.add_command(auth)
cli.add_command(status)
cli.add_command(recommend)
cli.add_command(update)
cli.add_command(complete)
cli.add_command(preferences)
cli.add_command(enrichment)
cli.add_command(library)
cli.add_command(profile)
cli.add_command(source)
cli.add_command(settings)


if __name__ == "__main__":
    cli()
