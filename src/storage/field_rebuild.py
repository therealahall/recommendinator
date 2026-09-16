"""What the ledger says an item's fields are, which no door writes yet."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.models.detail_fields import (
    COVER_FIELD,
    SOURCE_FIRST_FIELDS,
    text_names,
    to_int,
)
from src.storage.field_writes import StoredFieldWrite, WriterBand, read_field_writes
from src.storage.merge import (
    MERGEABLE_DETAIL_COLUMNS,
    MONOTONIC_DETAIL_COLUMNS,
    cover_url_is_dead,
)
from src.utils.list_merge import merge_string_lists
from src.utils.series import (
    SERIES_AUTHORITY_KEY,
    SERIES_POSITION_KEY,
    SeriesAuthority,
    get_series_position_from_metadata,
    stored_series_authority,
)

_BAND_STRENGTH: dict[WriterBand, int] = {
    band: strength for strength, band in enumerate(WriterBand)
}

_AUTHORITY_STRENGTH: dict[SeriesAuthority, int] = {
    authority: strength for strength, authority in enumerate(SeriesAuthority)
}

_SOURCE_FIRST_TRADE: dict[WriterBand, WriterBand] = {
    WriterBand.SOURCE: WriterBand.PROVIDER,
    WriterBand.PROVIDER: WriterBand.SOURCE,
}

_RankKey = tuple[int, int, str, str]


@dataclass(frozen=True)
class WriterRanks:
    providers: tuple[str, ...] = ()
    pinned: frozenset[str] = frozenset()

    def band_of(self, write: StoredFieldWrite) -> WriterBand | None:
        if write.writer_kind is not WriterBand.PROVIDER:
            return write.writer_kind
        if write.writer not in self.providers:
            return None
        return WriterBand.PINNED if write.writer in self.pinned else WriterBand.PROVIDER

    def position(self, writer: str) -> int:
        if writer not in self.providers:
            return len(self.providers)
        return self.providers.index(writer)


def rebuild_item_fields(
    cursor: sqlite3.Cursor, db_id: int, ranks: WriterRanks
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for field, writes in _ranked(read_field_writes(cursor, db_id), ranks).items():
        if field == SERIES_POSITION_KEY:
            resolved.update(_best_founded_ordinal(writes))
        elif field == COVER_FIELD:
            resolved.update(_first_live_cover(cursor, db_id, writes))
        elif field in MERGEABLE_DETAIL_COLUMNS:
            resolved[field] = _combined_names(writes)
        elif field in MONOTONIC_DETAIL_COLUMNS:
            resolved.update(_highest_count(field, writes))
        else:
            resolved[field] = writes[0].value
    return resolved


def _strength(band: WriterBand, field: str) -> int:
    if field in SOURCE_FIRST_FIELDS:
        band = _SOURCE_FIRST_TRADE.get(band, band)
    return _BAND_STRENGTH[band]


def _ranked(
    writes: Iterable[StoredFieldWrite], ranks: WriterRanks
) -> dict[str, list[StoredFieldWrite]]:
    graded: defaultdict[str, list[tuple[_RankKey, StoredFieldWrite]]] = defaultdict(
        list
    )
    for write in writes:
        band = ranks.band_of(write)
        if band is None:
            continue
        key = (
            -_strength(band, write.field),
            ranks.position(write.writer),
            write.written_at,
            write.writer,
        )
        graded[write.field].append((key, write))
    return {
        field: [write for _, write in sorted(entries, key=lambda entry: entry[0])]
        for field, entries in graded.items()
    }


def _combined_names(writes: list[StoredFieldWrite]) -> list[str]:
    combined: list[str] = []
    for write in writes:
        combined = merge_string_lists(combined, text_names(write.value))
    return combined


def _highest_count(field: str, writes: list[StoredFieldWrite]) -> dict[str, int]:
    counts = [count for write in writes if (count := to_int(write.value)) is not None]
    return {field: max(counts)} if counts else {}


def _first_live_cover(
    cursor: sqlite3.Cursor, db_id: int, writes: list[StoredFieldWrite]
) -> dict[str, Any]:
    for write in writes:
        if not cover_url_is_dead(cursor, db_id, str(write.value)):
            return {COVER_FIELD: write.value}
    return {}


def _founded_authority(
    write: StoredFieldWrite, stated: Mapping[str, Any]
) -> SeriesAuthority | None:
    """The operator's hand is the ladder's own top rung: the manual band records
    the ordinal and no authority beside it, so reading the row alone lost a
    hand-set position to any source that founded its own.
    """
    if write.writer_kind is WriterBand.MANUAL:
        return SeriesAuthority.MANUAL
    return stored_series_authority(stated)


def _best_founded_ordinal(writes: list[StoredFieldWrite]) -> dict[str, Any]:
    best: tuple[float, SeriesAuthority] | None = None
    for write in writes:
        stated = {
            SERIES_POSITION_KEY: write.value,
            SERIES_AUTHORITY_KEY: write.authority,
        }
        authority = _founded_authority(write, stated)
        position = get_series_position_from_metadata(stated)
        if authority is None or position is None:
            continue
        if (
            best is None
            or _AUTHORITY_STRENGTH[authority] > _AUTHORITY_STRENGTH[best[1]]
        ):
            best = (position, authority)
    if best is None:
        return {}
    return {SERIES_POSITION_KEY: best[0], SERIES_AUTHORITY_KEY: best[1].value}
