"""Tests for the importer package as a whole."""

import ast
import csv
import io
import json
from pathlib import Path

import pytest

from src.ingestion.import_templates import (
    TEMPLATE_IMPORTERS,
    TEMPLATES_DIR,
    ImportTemplate,
    available_templates,
    read_template,
)
from src.ingestion.importers.base import SkippedRow
from src.ingestion.importers.registry import IMPORTERS, get_importer
from src.ingestion.importers.service import decode_import_text
from src.models.content import ContentType
from src.models.templates import COMMON_COLUMNS, CONTENT_TYPE_COLUMNS

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


@pytest.mark.parametrize(
    "source",
    [
        "def read(path):\n    return open(path).read()\n",
        "from pathlib import Path\n\n\ndef read(name):\n    return Path(name).read_text()\n",
        "import os\n\n\ndef read(name):\n    return os.stat(name)\n",
        "import io\n\n\ndef read(name):\n    return io.open(name).read()\n",
    ],
    ids=["open", "pathlib", "os", "io.open"],
)
def test_the_guard_above_rejects_a_module_that_reads_a_file(
    source: str, tmp_path: Path
) -> None:
    """Trimming the names or the walk would leave that guard passing vacuously."""
    module = tmp_path / "leaky.py"
    module.write_text(source, encoding="utf-8")

    assert _names_used(module) & FILESYSTEM_NAMES


def test_no_shipped_format_disappears_from_the_registry() -> None:
    """The operator's picker is rendered from this list, so an entry that quietly
    goes missing is a file format nobody can choose any more.
    """
    assert {
        "goodreads_csv",
        "storygraph_csv",
        "csv_import",
        "json_import",
        "markdown_import",
    } - {importer.name for importer in IMPORTERS} == set()


def test_every_template_names_a_format_the_registry_offers() -> None:
    """A renamed importer would leave its template unreachable from either door."""
    assert set(TEMPLATE_IMPORTERS) <= {importer.name for importer in IMPORTERS}


def test_the_templates_directory_is_found_beside_the_package_not_the_cwd() -> None:
    """A relative path passes here and 503s in the image, whose working
    directory is /app and not the repository root (bd-qs5i.5.24).
    """
    assert TEMPLATES_DIR.is_absolute()
    assert available_templates(), "nothing resolved, so the directory is not the one"


def test_every_importer_answers_to_the_name_it_publishes() -> None:
    """Two importers sharing a name would silently shadow one another."""
    assert [get_importer(importer.name) for importer in IMPORTERS] == list(IMPORTERS)


def test_a_format_nobody_implements_is_not_guessed_at() -> None:
    assert get_importer("goodreads") is None


def _template_id(template: ImportTemplate) -> str:
    return f"{template.importer}-{template.filename}"


@pytest.mark.parametrize("template", available_templates(), ids=_template_id)
def test_every_shipped_template_imports_through_the_format_it_names(
    template: ImportTemplate,
) -> None:
    """A template is downloaded, filled in and uploaded back, and this is the
    only thing tying ``templates/`` to the parsers: one whose example row
    stopped parsing ships as a file whose every line is skipped.
    """
    importer = get_importer(template.importer)
    assert importer is not None

    rows = list(
        importer.parse(
            decode_import_text(read_template(template)),
            ContentType(template.content_type),
        )
    )

    assert rows, "the template ships no example row, so this proves nothing"
    assert [row for row in rows if isinstance(row, SkippedRow)] == []


@pytest.mark.parametrize(
    "template",
    [
        template
        for template in available_templates()
        if template.importer in {"csv_import", "json_import"}
    ],
    ids=_template_id,
)
def test_no_tabular_template_offers_a_column_the_importer_drops(
    template: ImportTemplate,
) -> None:
    """A column the importer does not know is warned about and ignored, never
    skipped, so a template naming one loses whatever the operator typed into it
    with nothing said on either interface.
    """
    text = decode_import_text(read_template(template))
    offered = (
        set(json.loads(text)[0])
        if template.importer == "json_import"
        else set(next(csv.reader(io.StringIO(text))))
    )
    consumed = COMMON_COLUMNS | set(CONTENT_TYPE_COLUMNS[template.content_type])

    assert offered - consumed == set()
