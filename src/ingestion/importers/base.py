"""What an importer is: a format, and a parse over text.

Nothing here takes a path, and ``test_importers.py`` holds every format to it:
an importer that could open a file would be a second way to read the disk.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar, Literal

from src.models.content import ContentItem, ContentType

#: What a row's number counts, since only the importer knows. A JSON array's
#: entries do not sit one per line, so calling entry 2 "line 2" names neither.
RowUnit = Literal["line", "entry"]


class ImporterError(Exception):
    """The text is not this format at all, so no row survived it."""


@dataclass(frozen=True, slots=True)
class ImportedRow:
    """An item, and the 1-based line or entry it was read from."""

    number: int
    item: ContentItem
    unit: RowUnit = "line"


@dataclass(frozen=True, slots=True)
class SkippedRow:
    """A row the parser refused, and a reason the operator can act on."""

    number: int
    reason: str
    unit: RowUnit = "line"


#: One row of an import: an item, or the reason there is none.
ParsedRow = ImportedRow | SkippedRow


class Importer(ABC):
    """One import format, parsed from decoded text."""

    #: The id the operator picks this format by, e.g. ``goodreads_csv``.
    name: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    #: What the format itself decides. Empty means the operator must say.
    content_types: ClassVar[tuple[ContentType, ...]]

    @abstractmethod
    def parse(
        self, text: str, content_type: ContentType | None = None
    ) -> Iterator[ParsedRow]:
        """One result per row; ``ImporterError`` if the text is not this format."""
        ...

    def required_content_type(self, content_type: ContentType | None) -> ContentType:
        if content_type is None:
            valid = ", ".join(member.value for member in ContentType)
            raise ImporterError(
                f"{self.display_name} needs a content type. One of: {valid}"
            )
        return content_type
