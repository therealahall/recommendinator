from __future__ import annotations

import time
import webbrowser
from typing import NoReturn

import click

from src.auth.service import (
    NotConnectable,
    OAuthPlugin,
    connect_with_code,
    connection_status,
    disconnect,
    oauth_plugins,
    poll_device_flow,
    refusal_message,
    sign_in_url,
    start_device_flow,
)
from src.cli._shared import abort_after_failure, abort_with, require_storage
from src.ingestion.plugin_base import CodePasteFlow, DevicePollStatus, OAuthError
from src.sources.service import (
    SOURCE_ID_RULE,
    configured_source_plugins,
    is_valid_source_id,
)
from src.storage.manager import StorageManager

_PLUGIN_HELP = "Plugin of the source to act on"

_SOURCE_ID_HELP = (
    "Id of the source to act on, which owns the token. "
    "Defaults to the plugin's own name."
)


def _declared(source: str) -> OAuthPlugin:
    """Not a ``click.Choice``: which plugins declare a flow is known only once
    the registry has loaded.
    """
    declared = oauth_plugins()
    found = declared.get(source.lower())
    if found is None:
        abort_with(f"--source must be one of: {', '.join(sorted(declared))}")
    return found


def _refuse(ctx: click.Context, declared: OAuthPlugin, error: Exception) -> NoReturn:
    """A source that may not connect is the operator's to fix, so it gets the
    remedy the web panel shows rather than a logged fault.
    """
    message = refusal_message(declared, error)
    if isinstance(error, NotConnectable):
        abort_with(f"{message} {declared.flow.setup_hint}".rstrip())
    abort_after_failure(ctx, message, error)


def _auth_source_id(declared: OAuthPlugin, source_id: str | None) -> str:
    """The id is a credential key, so an unvalidated one files a token where no
    web route can ever reach it.
    """
    if source_id is None:
        return declared.plugin.name
    if not is_valid_source_id(source_id):
        abort_with(f"--source-id {SOURCE_ID_RULE}")
    return source_id


@click.group()
def auth() -> None:
    """Manage authentication for data sources."""


@auth.command("status")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_status(ctx: click.Context, user_id: int) -> None:
    """Show enabled and connected state for every configured OAuth source."""
    # Every answer below is a credential-store read, so "storage is down" and
    # "nothing is connected" must not print the same thing.
    storage = require_storage(ctx)
    declared = oauth_plugins()

    lines = [
        _auth_status_line(storage, source_id, declared[plugin_name], user_id)
        for source_id, plugin_name in sorted(
            configured_source_plugins(storage, user_id).items()
        )
        if plugin_name in declared
    ]

    if not lines:
        click.echo("No OAuth sources are configured.")
        return
    for line in lines:
        click.echo(line)


def _auth_status_line(
    storage: StorageManager,
    source_id: str,
    declared: OAuthPlugin,
    user_id: int,
) -> str:
    status = connection_status(declared, source_id, storage, user_id)
    enabled_state = "enabled" if status["enabled"] else "not enabled"
    token_state = "connected" if status["connected"] else "not connected"
    return f"  {source_id} ({declared.plugin.name}): {enabled_state}, {token_state}"


@auth.command("connect")
@click.option("--source", required=True, help=_PLUGIN_HELP)
@click.option("--source-id", default=None, help=_SOURCE_ID_HELP)
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_connect(
    ctx: click.Context,
    source: str,
    source_id: str | None,
    no_browser: bool,
    user_id: int,
) -> None:
    """Connect an OAuth source by authenticating in browser."""
    storage = require_storage(ctx)
    declared = _declared(source)
    name = declared.display_name
    connecting = _auth_source_id(declared, source_id)

    if not isinstance(declared.flow, CodePasteFlow):
        _connect_device(ctx, storage, declared, connecting, user_id)
        return

    try:
        auth_url = sign_in_url(declared, connecting, storage, user_id)
    except OAuthError as error:
        _refuse(ctx, declared, error)
    if auth_url is None:
        abort_with(f"{name} did not return a sign-in link. Try again in a moment.")

    click.echo(f"\nAuthorize {name} at:\n  {auth_url}\n")

    if not no_browser:
        # ``open`` answers False on a headless host rather than raising, and
        # the URL is already on screen, so there is no fault to report here.
        click.echo(
            "(Browser opened automatically)"
            if webbrowser.open(auth_url)
            else "(Could not open browser — copy the URL above)"
        )

    # Click appends its own colon to a prompt.
    help_text = declared.flow.code_help.rstrip(": ")
    code = click.prompt(help_text or "Paste the authorization code")

    try:
        connect_with_code(declared, connecting, code.strip(), storage, user_id)
    except Exception as error:
        _refuse(ctx, declared, error)
    click.echo(f"\n{name} account connected successfully.")


def _connect_device(
    ctx: click.Context,
    storage: StorageManager,
    declared: OAuthPlugin,
    source_id: str,
    user_id: int,
) -> None:
    name = declared.display_name
    try:
        authorization = start_device_flow(declared, source_id, storage, user_id)
    except OAuthError as error:
        _refuse(ctx, declared, error)

    click.echo(
        f"\nGo to {authorization.verification_url} and enter code: "
        f"{authorization.user_code}\n"
    )
    click.echo("Waiting for approval... (press Ctrl-C to cancel)", err=True)

    interval = max(1, authorization.interval)
    deadline = time.monotonic() + authorization.expires_in

    try:
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                result = poll_device_flow(
                    declared, source_id, authorization.device_code, storage, user_id
                )
            except OAuthError as error:
                _refuse(ctx, declared, error)

            if result.status is DevicePollStatus.SUCCESS:
                click.echo(f"\n{name} account connected successfully.")
                return
            if result.status is DevicePollStatus.SLOW_DOWN:
                interval += 5
            elif result.status is DevicePollStatus.EXPIRED:
                click.echo(
                    "Error: The authorization code expired. Run connect again.",
                    err=True,
                )
                raise click.Abort()
            elif result.status is DevicePollStatus.DENIED:
                click.echo("Error: The authorization request was denied.", err=True)
                raise click.Abort()
    except KeyboardInterrupt:
        click.echo("\nCancelled.")
        raise click.Abort() from None

    click.echo(f"Error: Timed out waiting for {name} approval.", err=True)
    raise click.Abort()


@auth.command("disconnect")
@click.option("--source", required=True, help=_PLUGIN_HELP)
@click.option("--source-id", default=None, help=_SOURCE_ID_HELP)
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_disconnect(
    ctx: click.Context,
    source: str,
    source_id: str | None,
    yes: bool,
    user_id: int,
) -> None:
    """Disconnect an OAuth source by removing stored credentials."""
    storage = require_storage(ctx)
    declared = _declared(source)
    disconnecting = _auth_source_id(declared, source_id)

    if not yes:
        if not click.confirm(f"Disconnect '{disconnecting}' for user {user_id}?"):
            click.echo("Aborted.")
            return

    if disconnect(declared, disconnecting, storage, user_id):
        click.echo(f"{declared.display_name} disconnected.")
        return

    # Mirror DELETE /api/oauth/{plugin_name}/token, which answers 404 both for an
    # id this verb may not act on and for one holding no token.
    click.echo(f"No active {declared.display_name} connection found.", err=True)
    raise click.Abort()
