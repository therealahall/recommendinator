"""Every import format, in the order they are offered.

Listed rather than discovered: an import writes to the library off a name the
operator typed, so the set of formats is one a reader can see whole.
"""

from __future__ import annotations

from src.ingestion.importers.base import Importer
from src.ingestion.importers.generic_csv.generic_csv import CsvImporter
from src.ingestion.importers.generic_json.generic_json import JsonImporter
from src.ingestion.importers.goodreads_csv.goodreads_csv import GoodreadsCsvImporter
from src.ingestion.importers.markdown.markdown import MarkdownImporter
from src.ingestion.importers.storygraph_csv.storygraph_csv import StorygraphCsvImporter

IMPORTERS: tuple[Importer, ...] = (
    GoodreadsCsvImporter(),
    StorygraphCsvImporter(),
    CsvImporter(),
    JsonImporter(),
    MarkdownImporter(),
)

_BY_NAME: dict[str, Importer] = {importer.name: importer for importer in IMPORTERS}


def get_importer(name: str) -> Importer | None:
    """The format the operator named, or None if it is not one of ours."""
    return _BY_NAME.get(name)
