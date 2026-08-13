"""One module per command group, and one copy of what the groups share.

Regression: ``src/cli/commands.py`` reached 3427 lines holding ten groups, two
spellings of the same coercion and two of the same view emitter. Nothing
failed while it grew.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import src as src_package
from src.cli import commands
from src.cli._shared import coerce_value, emit_view
from src.cli.main import cli

_CLI_ROOT = Path(src_package.__file__).parent / "cli"

#: Past this, a module is on its way back to the 3427-line one it was split out
#: of. Loose on purpose: a cap whose first failure is a legitimate feature is
#: one people edit rather than heed.
_MAX_LINES = 900

_SHARED_MODULE = "src.cli._shared"

#: The two groups that each carried their own coercion and their own emitter.
_COERCING_GROUPS = ("src.cli.commands._source", "src.cli.commands._settings")

#: What a boolean argument may be spelled as. A second module declaring either
#: table is a second coercion, whatever the function around it is called.
_BOOLEAN_TABLES = ({"true", "1", "yes", "on"}, {"false", "0", "no", "off"})


def _module_name(path: Path) -> str:
    parts = path.relative_to(_CLI_ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("src", "cli", *parts))


def _discover() -> tuple[dict[str, ast.Module], dict[str, int]]:
    trees: dict[str, ast.Module] = {}
    lines: dict[str, int] = {}
    for path in sorted(_CLI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        module = _module_name(path)
        trees[module] = ast.parse(source)
        lines[module] = len(source.splitlines())
    return trees, lines


_TREES, _LINE_COUNTS = _discover()


def _string_collection_literals(tree: ast.AST) -> list[set[str]]:
    """Sets, tuples and lists alike: ``lowered in ("true", "1", "yes", "on")``
    is the same table, and ``in`` does not care which one holds it.
    """
    return [
        {element.value for element in node.elts}
        for node in ast.walk(tree)
        if isinstance(node, ast.Set | ast.Tuple | ast.List)
        and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in node.elts
        )
    ]


def _modules_coercing_a_boolean(trees: Mapping[str, ast.Module]) -> set[str]:
    """Which of *trees* decides for itself what ``true`` and ``false`` mean."""
    return {
        module
        for module, tree in trees.items()
        if any(table in _BOOLEAN_TABLES for table in _string_collection_literals(tree))
    }


def _names_imported_from(tree: ast.AST, module: str) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


class TestTheGroupsAreSeparateModules:
    @pytest.mark.parametrize("module", sorted(_LINE_COUNTS))
    def test_no_module_is_growing_back_into_a_god_module(self, module: str) -> None:
        assert _LINE_COUNTS[module] <= _MAX_LINES

    def test_every_re_exported_group_has_a_module_of_its_own(self) -> None:
        """A group folded into a sibling's module, or declared in the package
        ``__init__``, still re-exports and still runs. This is what says each
        one kept a module named after it."""
        assert {f"src.cli.commands._{name}" for name in commands.__all__} <= set(_TREES)

    def test_the_re_exported_groups_are_what_the_cli_registers(self) -> None:
        """``main`` imports the names off the package, so a group left out of
        the re-export is a command nobody can run. Two derived populations, so
        the anchor is what says they are not both empty."""
        assert set(cli.commands) == set(commands.__all__) != set()


class TestOneCoercionAndOneEmitter:
    def test_only_the_shared_module_decides_what_true_means(self) -> None:
        assert _modules_coercing_a_boolean(_TREES) == {_SHARED_MODULE}

    def test_the_groups_that_used_to_coerce_are_in_the_swept_population(self) -> None:
        """A sweep reading no group module would pass the assertion above."""
        assert set(_COERCING_GROUPS) <= set(_TREES)

    @pytest.mark.parametrize(
        "source",
        [
            '_TRUE = {"true", "1", "yes", "on"}',
            '_TRUE = ("true", "1", "yes", "on")',
            '_TRUE = ["true", "1", "yes", "on"]',
        ],
    )
    def test_a_second_coercion_is_reported(self, source: str) -> None:
        """The sweep is a set equality, which a stalled sweep also passes. One
        case per spelling, because a detector reading only one of them reports
        nothing for the other two."""
        thief = ast.parse(source)

        assert _modules_coercing_a_boolean({**_TREES, "src.cli.thief": thief}) == {
            _SHARED_MODULE,
            "src.cli.thief",
        }

    @pytest.mark.parametrize("value_type", ["str", "string"])
    def test_a_type_name_with_no_branch_passes_its_value_through(
        self, value_type: str
    ) -> None:
        """The source group passes ``field_type_name``'s ``"str"`` where it used
        to branch on the Python type object, and the settings group passes
        ``SettingType``'s ``"string"``. Equivalent only while neither name
        reaches a branch."""
        assert coerce_value(value_type, " a, b ") == " a, b "

    def test_table_mode_does_not_build_the_view_it_will_not_print(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Why the emitter takes a thunk: the group it came from built the
        source view and then threw it away on this branch."""
        builds = 0

        def build_view() -> dict[str, Any]:
            nonlocal builds
            builds += 1
            return {"enabled": True}

        emit_view("table", build_view, "Enabled source 'my_games'.")

        assert builds == 0
        assert capsys.readouterr().out == "Enabled source 'my_games'.\n"

    def test_json_mode_prints_the_view_the_builder_returned(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        view: dict[str, Any] = {"id": "my_games", "config": {"tags": ["rpg"]}}

        emit_view("json", lambda: view, "Enabled source 'my_games'.")

        printed = capsys.readouterr().out
        assert json.loads(printed) == view
        assert "Enabled source" not in printed

    @pytest.mark.parametrize("module", _COERCING_GROUPS)
    @pytest.mark.parametrize("name", ["coerce_value", "emit_view"])
    def test_each_group_reaches_the_shared_helper(self, module: str, name: str) -> None:
        assert name in _names_imported_from(_TREES[module], _SHARED_MODULE)
