"""Tests for the importer package as a whole."""

import ast
from pathlib import Path

from src.ingestion.importers.registry import IMPORTERS, get_importer

# An importer that could open a file would be a second way to read the disk,
# reached from an upload that never gave it a path.
FILESYSTEM_NAMES = {"open", "os", "pathlib", "Path", "shutil", "glob", "tempfile"}


def _names_used(module: Path) -> set[str]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])

    return names


def test_no_importer_module_can_reach_the_filesystem() -> None:
    """An upload is bytes in memory, and this is what keeps it that way."""
    modules = [
        module
        for module in sorted(Path(__file__).parent.rglob("*.py"))
        if not module.name.startswith("test_")
    ]
    used = {module.name: _names_used(module) & FILESYSTEM_NAMES for module in modules}

    assert len(modules) > len(IMPORTERS), "no modules were read, so this proves nothing"
    assert {name: sorted(found) for name, found in used.items() if found} == {}


def test_every_importer_answers_to_the_name_it_publishes() -> None:
    """Two importers sharing a name would silently shadow one another."""
    assert [get_importer(importer.name) for importer in IMPORTERS] == list(IMPORTERS)


def test_a_format_nobody_implements_is_not_guessed_at() -> None:
    assert get_importer("goodreads") is None
