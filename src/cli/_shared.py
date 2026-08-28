from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import click

from src.storage.manager import StorageManager
from src.utils.series import (
    get_series_name_from_metadata,
    get_series_position_from_metadata,
)
from src.utils.text import exception_for_log, is_blank

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
) -> None:
    """Refuse in the web's words, keeping the fault's own out of the terminal."""
    logger.error("%s", message, exc_info=True)
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
) -> NoReturn:
    """A loop that must keep going calls ``report_failure``."""
    report_failure(ctx, message, error)
    raise click.Abort()


def require_storage(ctx: click.Context) -> StorageManager:
    storage: StorageManager | None = ctx.obj.get("storage")
    if storage is None:
        abort_with("Storage unavailable")
    return storage


def is_blank_review(review: str | None) -> bool:
    """Stored, ``""`` blocks a later import from filling one in, so ``complete``
    and ``library edit`` both refuse it.
    """
    return review is not None and is_blank(review)


def series_label(metadata: dict[str, Any] | None) -> str:
    """The series a table row states — "The Expanse #2" — or N/A for none."""
    series = get_series_name_from_metadata(metadata)
    if not series:
        return "N/A"
    position = get_series_position_from_metadata(metadata)
    return series if position is None else f"{series} #{position:g}"


class ValueCoercionError(Exception):
    """Carries the type rather than a message: the source group answers by
    aborting and the settings group by raising a validation error.
    """

    def __init__(self, value_type: str) -> None:
        super().__init__(f"expected {value_type}")
        self.value_type = value_type


def coerce_value(value_type: str, raw: str) -> bool | int | float | list[str] | str:
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
    """Read a JSON object from stdin (``-``) or a file path."""
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


def write_output_file(
    ctx: click.Context,
    output_path: Path,
    data: bytes,
    *,
    assume_yes: bool,
) -> bool:
    """False means the operator kept the file that was already there."""
    if not assume_yes and output_path.is_file():
        if not click.confirm(f"{output_path} already exists. Overwrite it?"):
            click.echo("Aborted.")
            return False
    try:
        output_path.write_bytes(data)
    except OSError as error:
        abort_after_failure(ctx, f"Could not write {output_path}", error)
    return True


def emit_view(
    output_format: str,
    build_view: Callable[[], dict[str, Any]],
    success_message: str,
) -> None:
    """The web mutations answer with the refreshed body, so a scripted caller
    gets it from the call that wrote, not from a second read.
    """
    if output_format == "json":
        click.echo(json.dumps(build_view(), indent=2))
    else:
        click.echo(success_message)
