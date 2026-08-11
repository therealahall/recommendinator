"""Every source plugin's log sink, swept for the shape that forges an entry.

Three plugins were fixed and four were missed, so the guard is the package.
Text slots come off the message's ``%`` conversions.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import src.ingestion.sources as sources_package
from src.utils.text import LINE_BREAKS

_SOURCES_ROOT = Path(sources_package.__file__).parent

_LOG_SANITIZERS = {"sanitize_for_log", "exception_for_log"}

_LOG_METHODS = {"debug", "info", "warning", "error", "critical"}

_CONVERSION_RE = re.compile(r"%[-+ #0]*[\d*]*(?:\.[\d*]*)?[hlL]?([diouxXeEfFgGcrsa%])")

#: The conversions that render a value as text. ``%r`` is one of them: it
#: escapes a break by accident, not by rule.
_TEXT_CONVERSIONS = frozenset("rsa")

#: Text slots no imported value reaches, each with the reason it cannot end an
#: entry. Anything else in a text slot goes through a sanitizer.
_NON_TEXT_LOG_ARGUMENTS = {
    "self.display_name": "a literal on each *arr subclass",
    "type(error).__name__": "an identifier holds no break",
    "file_format": "'JSON' or 'JSONL', chosen from literals in the module",
}

#: The modules that log at all. Named so discovery finding nothing fails here
#: rather than reporting a clean sweep over an empty package.
_MODULES_THAT_LOG = {
    "arr_base",
    "calibre_web",
    "epic_games",
    "generic_csv",
    "generic_json",
    "gog",
    "goodreads_csv",
    "goodreads_rss",
    "markdown",
    "radarr",
    "roms",
    "steam",
    "storygraph_csv",
    "trakt",
}


def _swept_modules() -> list[Path]:
    """Every shipped module in the package, plugin-local tests aside."""
    return sorted(
        path
        for path in _SOURCES_ROOT.rglob("*.py")
        if not path.name.startswith("test_")
    )


_TREES = {
    path.relative_to(_SOURCES_ROOT).as_posix(): ast.parse(
        path.read_text(encoding="utf-8")
    )
    for path in _swept_modules()
}


def _log_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``logger.<level>(...)`` call in a parsed module."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    ]


def _literal_message(call: ast.Call) -> str | None:
    """The call's format string, or None when it was not written as one."""
    if not call.args or not isinstance(call.args[0], ast.Constant):
        return None
    message = call.args[0].value
    return message if isinstance(message, str) else None


def _conversions(message: str) -> list[str]:
    """The message's ``%`` conversions in argument order, ``%%`` dropped."""
    return [found for found in _CONVERSION_RE.findall(message) if found != "%"]


def _names_bound_to_a_sanitizer(tree: ast.AST) -> set[str]:
    """Locals holding a sanitized copy, so one escape can serve several sinks."""
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in _LOG_SANITIZERS
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _is_escaped(argument: ast.expr, sanitized_names: set[str]) -> bool:
    return (isinstance(argument, ast.Name) and argument.id in sanitized_names) or (
        isinstance(argument, ast.Call)
        and isinstance(argument.func, ast.Name)
        and argument.func.id in _LOG_SANITIZERS
    )


def _unsanitized_text_arguments(tree: ast.AST) -> set[str]:
    sanitized_names = _names_bound_to_a_sanitizer(tree)
    reported = set()
    for call in _log_calls(tree):
        message = _literal_message(call)
        if message is None:
            continue
        arguments = call.args[1:]
        for index, conversion in enumerate(_conversions(message)):
            if conversion not in _TEXT_CONVERSIONS or index >= len(arguments):
                continue
            argument = arguments[index]
            if ast.unparse(argument) in _NON_TEXT_LOG_ARGUMENTS:
                continue
            if not _is_escaped(argument, sanitized_names):
                reported.add(f"{ast.unparse(argument)} (line {argument.lineno})")
    return reported


def _mismatched_argument_counts(tree: ast.AST) -> set[str]:
    """A slot with no argument beside it is a slot the sweep cannot judge."""
    return {
        f"{ast.unparse(call)} (line {call.lineno})"
        for call in _log_calls(tree)
        if (message := _literal_message(call)) is not None
        and len(_conversions(message)) != len(call.args) - 1
    }


def _non_literal_log_messages(tree: ast.AST) -> set[str]:
    """Messages built before the call, where no ``%s`` argument is swept."""
    return {
        f"{ast.unparse(call)} (line {call.lineno})"
        for call in _log_calls(tree)
        if _literal_message(call) is None
    }


def _attaches_a_traceback(keyword: ast.keyword) -> bool:
    """``exc_info=False`` is the default written out, so it renders nothing."""
    return keyword.arg == "exc_info" and not (
        isinstance(keyword.value, ast.Constant) and not keyword.value.value
    )


def _traceback_log_calls(tree: ast.AST) -> set[str]:
    """Calls that put the exception's own unescaped words back in the file."""
    return {
        f"{call.func.attr} (line {call.lineno})"
        for call in _log_calls(tree)
        if isinstance(call.func, ast.Attribute)
        and (
            call.func.attr not in _LOG_METHODS
            or any(_attaches_a_traceback(keyword) for keyword in call.keywords)
        )
    }


def _hand_rolled_break_escapes(tree: ast.AST) -> set[str]:
    """A local escape rule covers ``\\n`` and none of the other nine."""
    return {
        f"{ast.unparse(node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and set(str(node.args[0].value)) & set(LINE_BREAKS + "\0")
    }


def _log_sinks_the_sweep_cannot_see(tree: ast.AST) -> set[str]:
    """Writes reaching a log or console without going through ``logger``."""
    return {
        f"{ast.unparse(node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "print")
            or (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logging"
                and node.func.attr in _LOG_METHODS | {"exception", "log"}
            )
        )
    }


def _is_logger_expression(value: ast.expr) -> bool:
    """A ``getLogger`` result, the module logger, or a method bound off it."""
    if isinstance(value, ast.Call):
        if isinstance(value.func, ast.Attribute):
            return value.func.attr == "getLogger"
        return isinstance(value.func, ast.Name) and value.func.id == "getLogger"
    root = value.value if isinstance(value, ast.Attribute) else value
    return isinstance(root, ast.Name) and root.id == "logger"


def _logger_binding_names(tree: ast.AST) -> set[str]:
    """The names a logger, or anything reached through one, is bound to."""
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and _is_logger_expression(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _plain_sanitizer_on_a_caught_exception(tree: ast.AST) -> set[str]:
    """``sanitize_for_log(error)`` drops the class name a bare fault needs.

    ``exception_for_log`` keeps it, and scrubs a ``requests`` fault's URL
    before escaping it.
    """
    handler_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.name
    }
    return {
        f"{ast.unparse(call)} (line {call.lineno})"
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "sanitize_for_log"
        and any(
            isinstance(node, ast.Name) and node.id in handler_names
            for node in ast.walk(call)
        )
    }


@pytest.mark.parametrize("module", sorted(_TREES))
class TestNoSourcePluginInterpolatesAValueRaw:
    """The sweep, run against every shipped module in the package.

    Parametrized over what discovery finds rather than a list, so a plugin
    added tomorrow is swept without anyone remembering to add it here.
    """

    def test_every_logged_text_value_is_sanitized(self, module: str) -> None:
        assert _unsanitized_text_arguments(_TREES[module]) == set()

    def test_every_slot_in_the_message_has_an_argument(self, module: str) -> None:
        assert _mismatched_argument_counts(_TREES[module]) == set()

    def test_every_log_message_is_a_literal(self, module: str) -> None:
        """An f-string message carries its values past the check above."""
        assert _non_literal_log_messages(_TREES[module]) == set()

    def test_no_log_call_attaches_a_traceback(self, module: str) -> None:
        """A traceback puts the exception's own words back, unescaped."""
        assert _traceback_log_calls(_TREES[module]) == set()

    def test_no_line_break_is_escaped_by_hand(self, module: str) -> None:
        assert _hand_rolled_break_escapes(_TREES[module]) == set()

    def test_no_sink_emits_under_another_name(self, module: str) -> None:
        """``logging.warning`` and ``print`` write the same file, unswept."""
        assert _log_sinks_the_sweep_cannot_see(_TREES[module]) == set()

    def test_the_only_logger_binding_is_the_one_swept(self, module: str) -> None:
        """A second logger under another name would be swept by nothing."""
        assert _logger_binding_names(_TREES[module]) <= {"logger"}

    def test_no_caught_exception_takes_the_plain_sanitizer(self, module: str) -> None:
        assert _plain_sanitizer_on_a_caught_exception(_TREES[module]) == set()


class TestTheSweptPopulationIsNotEmpty:
    """``set()`` is also what a sweep that found no modules at all returns."""

    def test_discovery_finds_the_plugins(self) -> None:
        assert {"roms/roms.py", "goodreads_csv/goodreads_csv.py"} <= set(_TREES)

    def test_every_module_that_logs_is_swept(self) -> None:
        assert {
            Path(module).stem for module, tree in _TREES.items() if _log_calls(tree)
        } == _MODULES_THAT_LOG

    def test_the_sweep_sees_the_text_slots_it_judges(self) -> None:
        """A conversion parser matching nothing would pass every module."""
        assert {
            Path(module).stem
            for module, tree in _TREES.items()
            for call in _log_calls(tree)
            if (message := _literal_message(call)) is not None
            and _TEXT_CONVERSIONS & set(_conversions(message))
        } == _MODULES_THAT_LOG


class TestTheSweepFailsOnANewRawSink:
    """The sweep above passes; these prove it is not passing vacuously.

    Each feeds the offending source to the predicate the test above calls and
    asserts the whole report, so a predicate naming the wrong node fails too.
    """

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("logger.info('Parsing file: %s', file_path)", "file_path"),
            ("logger.debug('title %r', title)", "title"),
            ("logger.warning('%s: %s', sanitize_for_log(a), b)", "b"),
            ("logger.warning('%d items from %s', count, source)", "source"),
        ],
    )
    def test_an_unsanitized_text_argument_is_reported(
        self, source: str, reported: str
    ) -> None:
        assert _unsanitized_text_arguments(ast.parse(source)) == {
            f"{reported} (line 1)"
        }

    @pytest.mark.parametrize(
        "source",
        [
            "logger.info('Found %d entries', count)",
            "logger.info('Retrying in %.1fs (%d/%d)', delay, attempt, limit)",
            "logger.info('%d/%d (%d%%)', current, total, percent)",
        ],
    )
    def test_a_numeric_slot_needs_no_sanitizer(self, source: str) -> None:
        """Escaping an integer counter would be noise, so ``%d`` is exempt."""
        assert _unsanitized_text_arguments(ast.parse(source)) == set()

    @pytest.mark.parametrize(
        "source",
        [
            "logger.info('Parsing %s', file_path, extra_argument)",
            "logger.info('Parsing %s and %s', file_path)",
        ],
    )
    def test_a_mismatched_argument_count_is_reported(self, source: str) -> None:
        assert _mismatched_argument_counts(ast.parse(source)) == {f"{source} (line 1)"}

    @pytest.mark.parametrize(
        "source",
        [
            "logger.info(f'Parsing {file_path}')",
            "logger.info('Parsing %s' % file_path)",
            "logger.info()",
        ],
    )
    def test_a_message_that_is_not_a_literal_is_reported(self, source: str) -> None:
        assert _non_literal_log_messages(ast.parse(source)) == {f"{source} (line 1)"}

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("logger.error('boom', exc_info=True)", "error"),
            ("logger.error('boom', exc_info=error)", "error"),
            ("logger.exception('boom')", "exception"),
        ],
    )
    def test_a_traceback_is_reported(self, source: str, reported: str) -> None:
        assert _traceback_log_calls(ast.parse(source)) == {f"{reported} (line 1)"}

    def test_the_default_written_out_is_not_a_traceback(self) -> None:
        """``exc_info=False`` attaches nothing, so a report is a false alarm."""
        tree = ast.parse("logger.error('boom', exc_info=False)")

        assert _traceback_log_calls(tree) == set()

    @pytest.mark.parametrize("breaker", [*LINE_BREAKS, "\0"])
    def test_a_hand_rolled_escape_is_reported(self, breaker: str) -> None:
        escape = f"title.replace({breaker!r}, ' ')"

        assert _hand_rolled_break_escapes(ast.parse(f"safe = {escape}")) == {
            f"{escape} (line 1)"
        }

    @pytest.mark.parametrize(
        "source",
        [
            "logging.warning('%s', title)",
            "logging.exception('boom')",
            "print(title)",
        ],
    )
    def test_a_sink_under_another_name_is_reported(self, source: str) -> None:
        assert _log_sinks_the_sweep_cannot_see(ast.parse(source)) == {
            f"{source} (line 1)"
        }

    @pytest.mark.parametrize(
        ("source", "bound"),
        [
            ("audit = logging.getLogger('audit')", "audit"),
            ("audit = getLogger('audit')", "audit"),
            ("_LOG = logger", "_LOG"),
            ("warn = logger.warning", "warn"),
        ],
    )
    def test_a_second_logger_binding_is_reported(self, source: str, bound: str) -> None:
        """The alias is named, and the module's own binding is still found.

        A bare ``not <= {'logger'}`` on the offending line alone passes on a
        predicate recognising nothing: the empty set is a subset.
        """
        tree = ast.parse(f"logger = logging.getLogger(__name__)\n{source}")

        assert _logger_binding_names(tree) == {"logger", bound}

    @pytest.mark.parametrize("argument", ["error", "str(error)", "f'{error}'"])
    def test_the_plain_sanitizer_on_a_caught_exception_is_reported(
        self, argument: str
    ) -> None:
        call = f"sanitize_for_log({argument})"
        tree = ast.parse(f"try:\n    go()\nexcept OSError as error:\n    log({call})")

        assert _plain_sanitizer_on_a_caught_exception(tree) == {f"{call} (line 4)"}

    def test_the_helper_for_a_caught_exception_is_not_reported(self) -> None:
        """``exception_for_log(error)`` is the spelling the sweep asks for."""
        tree = ast.parse(
            "try:\n    go()\nexcept OSError as error:\n"
            "    logger.warning('%s', exception_for_log(error))"
        )

        assert _plain_sanitizer_on_a_caught_exception(tree) == set()
        assert _unsanitized_text_arguments(tree) == set()

    def test_the_clean_shape_is_not_reported(self) -> None:
        """The predicates accept what the plugins actually write."""
        tree = ast.parse(
            "logger = logging.getLogger(__name__)\n"
            "safe_path = sanitize_for_log(str(file_path))\n"
            "logger.info('Parsing %s: %d rows', safe_path, total)"
        )

        assert _unsanitized_text_arguments(tree) == set()
        assert _mismatched_argument_counts(tree) == set()
        assert _non_literal_log_messages(tree) == set()
        assert _traceback_log_calls(tree) == set()
        assert _hand_rolled_break_escapes(tree) == set()
        assert _log_sinks_the_sweep_cannot_see(tree) == set()
        assert _logger_binding_names(tree) == {"logger"}
        assert _plain_sanitizer_on_a_caught_exception(tree) == set()
