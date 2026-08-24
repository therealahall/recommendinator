"""The ``account`` group."""

from __future__ import annotations

import json

import click

from src.cli._shared import abort_after_failure, abort_with, emit_view, require_storage
from src.storage.accounts import (
    MIN_PASSWORD_LENGTH,
    PASSWORD_TOO_SHORT,
    AccountNameError,
    AccountRecord,
    normalize_account_name,
)
from src.storage.manager import StorageManager

PASSWORD_WRITE_FAILED = "Could not set the password"
SESSION_SWEEP_FAILED = "The password was changed, but the sessions were not signed out"
RENAME_FAILED = "Could not rename the account"


@click.group()
def account() -> None:
    """Manage the web login, with no server running and no session.

    The instance holds one account, with no email and no reset link, so this
    is the way back in when its password is lost.
    """


def _account_or_abort(storage: StorageManager, user_id: int) -> AccountRecord:
    account_record = storage.accounts.describe(user_id)
    if account_record is None:
        abort_with(f"No user with id {user_id}.")
    return account_record


def _normalized_name(option: str, value: str, *, required: bool) -> str:
    """The storage door's own rule, named after the option that broke it.

    A second copy of the cap here is what let the two refuse different widths.
    """
    try:
        return normalize_account_name(value, required=required)
    except AccountNameError as error:
        abort_with(f"{option}: {error}")


@account.command("show")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def account_show(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Show the account and when its password last changed."""
    account_record = _account_or_abort(require_storage(ctx), user_id)

    if output_format == "json":
        click.echo(json.dumps(account_record, indent=2))
        return

    click.echo(f"Username: {account_record['username']}")
    click.echo(f"Display name: {account_record['display_name'] or '-'}")
    click.echo(f"Claimed: {'yes' if account_record['claimed'] else 'no'}")
    click.echo(f"Password changed: {account_record['password_updated_at'] or 'never'}")


@account.command("set-password")
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def account_set_password(ctx: click.Context, user_id: int, output_format: str) -> None:
    """Set the account password, prompting for it twice.

    Never an argument: that leaves the password in the shell history and in
    every process listing. Setting one signs every browser out.
    """
    storage = require_storage(ctx)
    account_record = _account_or_abort(storage, user_id)
    if not account_record["claimed"]:
        abort_with(
            "This instance is unclaimed, so it has no password to replace. "
            "Claim it from the web setup page first."
        )

    # ``err``: the prompt is interaction, and stdout is where ``--format json``
    # writes the document a caller pipes.
    password = click.prompt(
        "New password", hide_input=True, confirmation_prompt=True, err=True
    )
    # Ahead of the write, which raises the same rule: the refusal below reads
    # the operator's own words rather than "check the logs".
    if len(password) < MIN_PASSWORD_LENGTH:
        abort_with(PASSWORD_TOO_SHORT)

    try:
        storage.accounts.set_password(user_id, password)
    except Exception as error:
        abort_after_failure(ctx, PASSWORD_WRITE_FAILED, error)

    try:
        storage.accounts.revoke_all_sessions(user_id)
    except Exception as error:
        abort_after_failure(ctx, SESSION_SWEEP_FAILED, error)

    emit_view(
        output_format,
        lambda: dict(_account_or_abort(storage, user_id)),
        "Password set. Every session has been signed out.",
    )


@account.command("set-name")
@click.option("--username", default=None, help="New username, the one you sign in with")
@click.option(
    "--display-name",
    default=None,
    help="New display name; pass an empty value to clear it",
)
@click.option("--user", "user_id", type=int, default=1, help="User ID")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def account_set_name(
    ctx: click.Context,
    username: str | None,
    display_name: str | None,
    user_id: int,
    output_format: str,
) -> None:
    """Rename the account: its username, its display name, or both."""
    if username is None and display_name is None:
        abort_with("Pass --username, --display-name, or both.")

    storage = require_storage(ctx)
    account_record = _account_or_abort(storage, user_id)
    new_username = (
        _normalized_name("--username", username, required=True)
        if username is not None
        else account_record["username"]
    )
    new_display_name = (
        (_normalized_name("--display-name", display_name, required=False) or None)
        if display_name is not None
        else account_record["display_name"]
    )

    try:
        storage.update_user_identity(user_id, new_username, new_display_name)
    except Exception as error:
        abort_after_failure(ctx, RENAME_FAILED, error)

    emit_view(
        output_format,
        lambda: dict(_account_or_abort(storage, user_id)),
        f"Account updated. Username: {new_username}.",
    )
