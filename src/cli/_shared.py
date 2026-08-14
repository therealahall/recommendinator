"""What more than one command group needs.

A helper only one group calls belongs in that group's module.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import click

from src.storage.manager import StorageManager
from src.utils.text import exception_for_log

logger = logging.getLogger(__name__)

#: Read by ``source set-secret`` and ``settings set-secret`` for
#: non-interactive use: an environment variable is not exposed in shell
#: history or in the process list to other users.
SECRET_VALUE_ENV = "RECOMMENDINATOR_SECRET_VALUE"

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def abort_with(message: str) -> NoReturn:
    click.echo(f"Error: {message}", err=True)
    raise click.Abort()


def report_failure(
    ctx: click.Context,
    message: str,
    error: BaseException,
    log_message: str | None = None,
) -> None:
    """Refuse in the web's words, keeping the fault's own out of the terminal.

    Root ``--verbose`` is for the operator whose log file is unreadable — a
    root-owned ``logs/`` bind mount, say. It adds the message, never the
    traceback.
    """
    # chat's terminal wording is the web's generic sentence, which names no
    # operation for the log to report.
    logged = message if log_message is None else log_message
    logger.error("%s", logged, exc_info=True)
    if ctx.obj.get("verbose"):
        # A control character in the fault's words would otherwise rewrite the
        # terminal line it lands on, and a lone surrogate would raise here.
        click.echo(f"Error: {message}: {exception_for_log(error)}", err=True)
    else:
        click.echo(f"Error: {message}. Check logs for details.", err=True)


def abort_after_failure(
    ctx: click.Context,
    message: str,
    error: BaseException,
    log_message: str | None = None,
) -> NoReturn:
    """Refuse and stop. A loop that must keep going calls ``report_failure``."""
    report_failure(ctx, message, error, log_message)
    raise click.Abort()


def require_storage(ctx: click.Context) -> StorageManager:
    storage: StorageManager | None = ctx.obj.get("storage")
    if storage is None:
        abort_with("Storage unavailable")
    return storage


def is_blank_review(review: str | None) -> bool:
    """Whether a ``--review`` value is empty or all whitespace.

    Stored, ``""`` blocks a later import from filling one in, so ``complete``
    and ``library edit`` both refuse it. Each words its own message: only one
    has ``--clear-review``.
    """
    return review is not None and not review.strip()


class ValueCoercionError(Exception):
    """*raw* is not a value of *value_type*.

    Carries the type rather than a message: the source group answers by
    aborting and the settings group by raising a validation error.
    """

    def __init__(self, value_type: str) -> None:
        super().__init__(f"expected {value_type}")
        self.value_type = value_type


def coerce_value(value_type: str, raw: str) -> bool | int | float | list[str] | str:
    """Parse a CLI string argument into *value_type*.

    Booleans accept true/false/1/0/yes/no/on/off; lists are comma-separated;
    ints and floats are parsed as written. Any other type passes through for
    the caller's own validation.
    """
    if value_type == "bool":
        lowered = raw.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        raise ValueCoercionError(value_type)
    if value_type == "int":
        try:
            return int(raw)
        except ValueError as error:
            raise ValueCoercionError(value_type) from error
    if value_type == "float":
        try:
            return float(raw)
        except ValueError as error:
            raise ValueCoercionError(value_type) from error
    if value_type == "list":
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def read_json_payload(from_json: str) -> dict[str, Any]:
    """Read a JSON object from stdin (``-``) or a file path.

    Aborts with a friendly message rather than letting ``FileNotFoundError`` /
    ``PermissionError`` surface as a Python traceback.
    """
    if from_json == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(from_json).read_text(encoding="utf-8")
        except OSError as error:
            abort_with(f"Could not read {from_json}: {error}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        abort_with(f"Invalid JSON: {error}")
    if not isinstance(parsed, dict):
        abort_with("JSON payload must be an object mapping field names to values")
    return parsed


def emit_view(
    output_format: str,
    build_view: Callable[[], dict[str, Any]],
    success_message: str,
) -> None:
    """Render a mutation's result: the refreshed view, or a line of prose.

    The web mutations answer with the refreshed body, so a scripted caller
    gets it from the call that wrote, not from a second read.
    """
    if output_format == "json":
        click.echo(json.dumps(build_view(), indent=2))
    else:
        click.echo(success_message)
