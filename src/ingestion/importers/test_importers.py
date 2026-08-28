import ast
import csv
import inspect
import io
import json
import re
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
    assert {
        "goodreads_csv",
        "storygraph_csv",
        "csv_import",
        "json_import",
        "markdown_import",
    } - {importer.name for importer in IMPORTERS} == set()


DATA_SOURCES_PAGE = Path(__file__).resolve().parents[3] / "docs" / "DATA_SOURCES.md"

_RELATIVE_DOC_LINK = re.compile(r"\]\((\.\.?/[^)\s]+\.md)\)")


def test_every_shipped_format_reaches_its_guide_from_the_shared_page() -> None:
    page = DATA_SOURCES_PAGE.read_text(encoding="utf-8")
    linked = {
        (DATA_SOURCES_PAGE.parent / target).resolve()
        for target in _RELATIVE_DOC_LINK.findall(page)
    }

    assert {
        importer.name
        for importer in IMPORTERS
        if Path(inspect.getfile(type(importer))).with_name("README.md").resolve()
        not in linked
    } == set()


def test_every_template_names_a_format_the_registry_offers() -> None:
    """A renamed importer would leave its template unreachable from either door."""
    assert set(TEMPLATE_IMPORTERS) <= {importer.name for importer in IMPORTERS}


def test_the_templates_directory_is_found_beside_the_package_not_the_cwd() -> None:
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
    text = decode_import_text(read_template(template))
    offered = (
        set(json.loads(text)[0])
        if template.importer == "json_import"
        else set(next(csv.reader(io.StringIO(text))))
    )
    consumed = COMMON_COLUMNS | set(CONTENT_TYPE_COLUMNS[template.content_type])

    assert offered - consumed == set()
