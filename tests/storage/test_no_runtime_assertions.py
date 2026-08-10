"""No storage module may state a runtime precondition with ``assert``.

``python -O`` strips them, so the guard is absent where it matters. The tree
is scanned whole because the next such precondition lands somewhere nobody is
looking.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# parents[2] resolves /tests/storage/<file> -> repo root.
_STORAGE_DIRECTORY = Path(__file__).resolve().parents[2] / "src" / "storage"


def _assert_line_numbers(path: Path) -> list[int]:
    """Return the line of every ``assert`` statement in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]


class TestStorageRuntimeAssertionRegression:
    """Regression test for a precondition that disappears under ``python -O``."""

    def test_no_storage_module_asserts_a_runtime_precondition_regression(self) -> None:
        """Regression test: ``_merge_detail_metadata`` asserted its rows non-None.

        Bug reported: ``python -O`` strips it, so the merge subscripts None.
        Root cause: assert used as a runtime guard.
        Fix: an explicit None branch, scanned tree-wide.
        """
        offences = [
            f"  {path.name}:{line}"
            for path in sorted(_STORAGE_DIRECTORY.glob("*.py"))
            for line in _assert_line_numbers(path)
        ]
        assert not offences, (
            "`assert` is stripped by `python -O`, so it cannot carry a runtime "
            "precondition. Raise, or branch and return, instead:\n"
            + "\n".join(offences)
        )

    def test_the_scan_covers_the_storage_modules(self) -> None:
        """A scan that matched no files would pass while proving nothing."""
        scanned = {path.name for path in _STORAGE_DIRECTORY.glob("*.py")}
        assert {"merge.py", "schema.py", "sqlite_db.py"} <= scanned

    def test_the_scan_locates_an_assert(self, tmp_path: Path) -> None:
        """A guard that cannot fire would stay green through the bug's return."""
        module = tmp_path / "example.py"
        module.write_text(
            "def merge(row):\n    assert row is not None\n    return row\n",
            encoding="utf-8",
        )
        assert _assert_line_numbers(module) == [2]

    @pytest.mark.parametrize(
        "source",
        [
            "def check(name):\n    if name is None:\n        raise ValueError(name)\n",
            "from src.storage.merge import assert_safe_identifier\n",
            "assert_safe_identifier('genres')\n",
        ],
    )
    def test_a_raising_guard_is_not_an_offence(
        self, tmp_path: Path, source: str
    ) -> None:
        """Storage's ``assert_`` helpers raise and are calls, not statements.

        Flagging them would push someone to rename the guards instead of fixing
        a precondition.
        """
        module = tmp_path / "example.py"
        module.write_text(source, encoding="utf-8")
        assert _assert_line_numbers(module) == []
