"""The ``auth`` group."""

from __future__ import annotations  # noqa: I001

import time
import webbrowser
from collections.abc import Callable
from typing import Any

import click

from src.auth.epic import (
    EPIC_PLUGIN,
    exchange_code_for_tokens as exchange_epic_code,
    extract_code_from_input as extract_epic_code,
    get_epic_auth_url,
    has_epic_token,
    is_epic_enabled,
    save_epic_token,
)
from src.auth.gog import (
    GOG_PLUGIN,
    exchange_code_for_tokens as exchange_gog_code,
    extract_code_from_input as extract_gog_code,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from src.auth.oauth_sources import REFRESH_TOKEN_KEY, may_revoke
from src.auth.trakt import (
    TRAKT_PLUGIN,
    DevicePollStatus,
    TraktAuthError,
    has_trakt_token,
    poll_device_token,
    resolve_trakt_client_credentials,
    save_trakt_token,
    start_device_auth_flow,
)
from src.cli._shared import abort_after_failure, abort_with, require_storage
from src.sources.service import (
    SOURCE_ID_RULE,
    configured_source_plugins,
    is_valid_source_id,
)
from src.storage.manager import StorageManager

#: What both Trakt device-flow endpoints answer with. The ``TraktAuthError``
#: quotes the request it failed on, credentials in the URL and all.
TRAKT_AUTH_FAILED = "Trakt authentication failed"

#: What ``POST /api/{gog,epic}/exchange`` answers when the exchange fails.
GOG_AUTH_FAILED = "GOG authentication failed"
EPIC_AUTH_FAILED = "Epic Games authentication failed"

# The plugin behind each ``--source`` choice. The CLI accepts "epic" for
# brevity while the plugin, and so the default source id, is "epic_games".
_AUTH_PLUGINS = {"gog": GOG_PLUGIN, "epic": EPIC_PLUGIN, "trakt": TRAKT_PLUGIN}

_SOURCE_ID_HELP = (
    "Id of the source to act on, which owns the token. "
    "Defaults to the plugin's own name."
)


def _auth_source_id(source: str, source_id: str | None) -> str:
    """The id an auth verb acts on, refused unless the routes could address it.

    The id is a credential key, so an unvalidated one files a token where no
    web route can ever reach it.
    """
    if source_id is None:
        return _AUTH_PLUGINS[source]
    if not is_valid_source_id(source_id):
        abort_with(f"--source-id {SOURCE_ID_RULE}")
    return source_id


def _is_trakt_enabled(
    config: dict[str, Any],
    storage: StorageManager,
    source_id: str,
    user_id: int,
) -> bool:
    """Whether *source_id* has client credentials saved for the device flow."""
    try:
        resolve_trakt_client_credentials(config, storage, source_id, user_id)
    except TraktAuthError:
        return False
    return True


_StatusCheck = Callable[[dict[str, Any], StorageManager, str, int], bool]

# What ``GET /api/{provider}/status`` answers for a source on each plugin, so
# both interfaces call being enabled and holding a token the same thing.
_OAUTH_STATUS: dict[str, tuple[_StatusCheck, _StatusCheck]] = {
    GOG_PLUGIN: (is_gog_enabled, has_gog_token),
    EPIC_PLUGIN: (is_epic_enabled, has_epic_token),
    TRAKT_PLUGIN: (_is_trakt_enabled, has_trakt_token),
}


@click.group()
def auth() -> None:
    """Manage authentication for data sources."""


@auth.command("status")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.pass_context
def auth_status(ctx: click.Context, user_id: int) -> None:
    """Show enabled and connected state for every configured OAuth source."""
    config = ctx.obj["config"]
    # Every answer below is a credential-store read, so "storage is down" and
    # "nothing is connected" must not print the same thing.
    storage = require_storage(ctx)

    lines = [
        _auth_status_line(config, storage, source_id, plugin_name, user_id)
        for source_id, plugin_name in sorted(
            configured_source_plugins(config, storage, user_id).items()
        )
        if plugin_name in _OAUTH_STATUS
    ]

    if not lines:
        click.echo("No OAuth sources are configured.")
        return
    for line in lines:
        click.echo(line)


def _auth_status_line(
    config: dict[str, Any],
    storage: StorageManager,
    source_id: str,
    plugin_name: str,
    user_id: int,
) -> str:
    """One source's line, answering both questions separately.

    A disabled source keeps its token, and only a line saying so tells the
    operator there is still something to revoke.
    """
    is_enabled, has_token = _OAUTH_STATUS[plugin_name]
    enabled_state = (
        "enabled" if is_enabled(config, storage, source_id, user_id) else "not enabled"
    )
    token_state = (
        "connected"
        if has_token(config, storage, source_id, user_id)
        else "not connected"
    )
    return f"  {source_id} ({plugin_name}): {enabled_state}, {token_state}"


@auth.command("connect")
@click.option(
    "--source",
    type=click.Choice(["gog", "epic", "trakt"], case_sensitive=False),
    required=True,
    help="Source to authenticate",
)
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
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    connecting = _auth_source_id(source, source_id)

    if source == "trakt":
        _connect_trakt(ctx, config, storage, connecting, user_id)
        return

    if source == "gog":
        auth_failed = GOG_AUTH_FAILED
        is_enabled_fn, get_auth_url_fn = is_gog_enabled, get_gog_auth_url
        extract_code_fn = extract_gog_code
        exchange_fn, save_fn = exchange_gog_code, save_gog_token
    else:
        auth_failed = EPIC_AUTH_FAILED
        is_enabled_fn, get_auth_url_fn = is_epic_enabled, get_epic_auth_url
        extract_code_fn = extract_epic_code
        exchange_fn, save_fn = exchange_epic_code, save_epic_token

    if not is_enabled_fn(config, storage, connecting, user_id):
        click.echo(
            f"Error: '{connecting}' is not an enabled {source} source.", err=True
        )
        raise click.Abort()

    auth_url = get_auth_url_fn()
    click.echo(f"\nAuthorize {source} at:\n  {auth_url}\n")

    if not no_browser:
        # ``open`` answers False on a headless host rather than raising, and
        # the URL is already on screen, so there is no fault to report here.
        click.echo(
            "(Browser opened automatically)"
            if webbrowser.open(auth_url)
            else "(Could not open browser — copy the URL above)"
        )

    code = click.prompt("Paste the authorization code or redirect URL")

    try:
        extracted_code = extract_code_fn(code.strip())
        tokens = exchange_fn(extracted_code)
        refresh_token = tokens.get("refresh_token")
        if not (refresh_token and refresh_token.strip()):
            click.echo("Error: No refresh token received.", err=True)
            raise click.Abort()
        save_fn(storage, refresh_token.strip(), source_id=connecting, user_id=user_id)
        click.echo(f"\n{source} connected successfully.")
    except click.Abort:
        raise
    except Exception as error:
        abort_after_failure(ctx, auth_failed, error)


def _connect_trakt(
    ctx: click.Context,
    config: dict[str, Any],
    storage: StorageManager,
    source_id: str,
    user_id: int,
) -> None:
    """Run the Trakt device-code flow: print the user code, then poll to approval.

    Resolves the saved client_id/client_secret, starts the device flow, and
    polls at the cadence Trakt returned until the user approves, the code
    expires, or the request is denied.
    """
    try:
        client_id, client_secret = resolve_trakt_client_credentials(
            config, storage, source_id, user_id
        )
        flow = start_device_auth_flow(client_id)
    except TraktAuthError as error:
        abort_after_failure(ctx, TRAKT_AUTH_FAILED, error)

    click.echo(
        f"\nGo to {flow['verification_url']} and enter code: {flow['user_code']}\n"
    )
    click.echo("Waiting for approval... (press Ctrl-C to cancel)", err=True)

    interval = max(1, int(flow["interval"]))
    deadline = time.monotonic() + int(flow["expires_in"])

    try:
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                result = poll_device_token(
                    flow["device_code"], client_id, client_secret
                )
            except TraktAuthError as error:
                abort_after_failure(ctx, TRAKT_AUTH_FAILED, error)

            if result.status is DevicePollStatus.SUCCESS:
                if result.refresh_token is None:
                    click.echo(
                        "Error: Trakt approved but returned no refresh token.",
                        err=True,
                    )
                    raise click.Abort()
                save_trakt_token(
                    storage, result.refresh_token, source_id=source_id, user_id=user_id
                )
                click.echo("\ntrakt connected successfully.")
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

    click.echo("Error: Timed out waiting for Trakt approval.", err=True)
    raise click.Abort()


@auth.command("disconnect")
@click.option(
    "--source",
    type=click.Choice(["gog", "epic", "trakt"], case_sensitive=False),
    required=True,
    help="Source to disconnect",
)
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
    config = ctx.obj["config"]
    storage = require_storage(ctx)
    plugin_name = _AUTH_PLUGINS[source]
    disconnecting = _auth_source_id(source, source_id)

    if not yes:
        if not click.confirm(f"Disconnect '{disconnecting}' for user {user_id}?"):
            click.echo("Aborted.")
            return

    if may_revoke(
        plugin_name, disconnecting, config, storage, user_id
    ) and storage.delete_credential(user_id, disconnecting, REFRESH_TOKEN_KEY):
        click.echo(f"{source} disconnected.")
        return

    # Mirror DELETE /api/{source}/token, which answers 404 both for an id this
    # verb may not act on and for one holding no token.
    click.echo(f"No active {source} connection found.", err=True)
    raise click.Abort()
