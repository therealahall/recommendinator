"""Every text file ``src/web/app.py`` opens names the encoding it wants.

Text I/O with no ``encoding=`` takes the locale's, and a container running
under a non-UTF-8 one drops every accented title it logs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import src.web.app

_APP_TREE = ast.parse(Path(src.web.app.__file__).read_text(encoding="utf-8"))

_TEXT_IO_CALLS = {"open", "read_text", "write_text"}


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else ""


def _states_binary_mode(call: ast.Call) -> bool:
    """Whether the call opens in binary, where ``encoding=`` is a TypeError.

    A mode this cannot read — a variable — stays in the sweep: the sweep's
    failure mode should be a question, not a gap.
    """
    mode = next(
        (keyword.value for keyword in call.keywords if keyword.arg == "mode"),
        call.args[1] if len(call.args) > 1 else None,
    )
    return isinstance(mode, ast.Constant) and "b" in str(mode.value)


def _is_text_io(call: ast.Call) -> bool:
    # Matched by suffix because swapping FileHandler for a Rotating/Timed/
    # Watched one is the likeliest future edit to the log handler, and each
    # inherits the locale identically.
    name = _call_name(call)
    return (
        name in _TEXT_IO_CALLS or name.endswith("FileHandler")
    ) and not _states_binary_mode(call)


def _text_io_calls(tree: ast.AST) -> list[ast.Call]:
    """Every text-mode open in a parsed module, encoding stated or not."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_text_io(node)
    ]


def _unstated_encodings(tree: ast.AST) -> set[str]:
    """Every text-mode open in a parsed module that names no encoding."""
    return {
        f"{ast.unparse(call)} (line {call.lineno})"
        for call in _text_io_calls(tree)
        if not any(keyword.arg == "encoding" for keyword in call.keywords)
    }


class TestNoEncodingInAppIsInheritedFromTheLocaleRegression:
    """Reported twice, by two reviewers, in two places.

    Bug: the log ``FileHandler`` and the SPA's ``read_text`` each took the
    locale's encoding.
    Fix: name UTF-8 at both, and sweep so the third fails here.
    """

    def test_every_text_io_call_names_its_encoding(self) -> None:
        assert _unstated_encodings(_APP_TREE) == set()

    def test_the_sweep_still_reaches_the_calls_it_exists_for(self) -> None:
        """Both calls leaving app.py would make the assertion above vacuous."""
        found = {_call_name(call) for call in _text_io_calls(_APP_TREE)}
        assert {"FileHandler", "read_text"} <= found


class TestTheEncodingSweepFailsOnAnUnstatedOpen:
    """The sweep above passes; these prove it is not passing vacuously."""

    def test_a_module_that_opens_nothing_finds_nothing(self) -> None:
        """An anchor that could never come up empty is anchoring nothing.

        This is the state the anchor test asserts against — what app.py would
        look like to the sweep if both of its opens moved elsewhere.
        """
        assert _text_io_calls(ast.parse("x = json.dumps(payload)")) == []

    @pytest.mark.parametrize(
        "source",
        [
            "logging.FileHandler(path)",
            "logging.FileHandler(path, errors='backslashreplace')",
            "logging.handlers.RotatingFileHandler(path, maxBytes=1024)",
            "logging.handlers.TimedRotatingFileHandler(path, when='midnight')",
            "logging.handlers.WatchedFileHandler(path)",
            # ``from logging.handlers import ...`` reaches the suffix rule
            # through ``_call_name``'s ast.Name branch, not its attribute one.
            "WatchedFileHandler(path)",
            "index.read_text()",
            "path.write_text(body)",
            "open(path)",
            "open(path, 'w')",
            "open(path, mode=mode)",
            # Binary, yet reported: ``Path.open`` takes mode first, where the
            # sweep does not look. Same outcome as the unreadable mode above,
            # for the same reason — it asks rather than guesses.
            "index.open('rb')",
        ],
    )
    def test_an_unstated_encoding_is_reported(self, source: str) -> None:
        assert _unstated_encodings(ast.parse(source)) != set()

    @pytest.mark.parametrize(
        "source",
        [
            "logging.FileHandler(path, encoding='utf-8')",
            "index.read_text(encoding='utf-8')",
            "open(path, encoding='utf-8')",
            "index.read_bytes()",
            # Binary opens are exempt: `encoding=` is a TypeError there, so the
            # only way to satisfy a report would be to weaken the sweep.
            "open(path, 'rb')",
            "open(path, mode='rb')",
            # A stated encoding must clear the suffix rule too, or it is a rule
            # no rotating handler could ever be written to satisfy.
            "logging.handlers.RotatingFileHandler(path, encoding='utf-8')",
            # app.py's console handler, deliberately left on the process
            # encoding (see the comment there). Widening the sweep to reach it
            # would fail the module for a decision it made on purpose.
            "logging.StreamHandler(sys.stdout)",
        ],
    )
    def test_a_stated_encoding_is_not_reported(self, source: str) -> None:
        assert _unstated_encodings(ast.parse(source)) == set()
