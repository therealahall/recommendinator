"""What the ledger says an item's fields are, which every door writes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.enrichment.registry import get_enrichment_registry
from src.models.detail_fields import (
    COVER_FIELD,
    SOURCE_FIRST_FIELDS,
    WRITER_STATED_FIELDS,
    text_names,
    to_int,
)
from src.settings.metadata import (
    ENABLED_KEY_PATTERN,
    PROVIDER_ORDER_KEY,
    provider_of_enabled_key,
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
    SERIES_NAME_KEY,
    SERIES_POSITION_KEY,
    SeriesAuthority,
    get_series_position_from_metadata,
    series_names_agree,
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

#: What a row this item absorbed still states for it. Not the title: which row
#: survived is the operator's own decision, and rank would rename the item.
_ABSORBED_ROW_STATES: frozenset[str] = WRITER_STATED_FIELDS - {"title"}

_RankKey = tuple[int, int, bool, str, str]


@dataclass(frozen=True)
class RebuiltFields:
    values: dict[str, Any]
    cleared: frozenset[str]


@dataclass(frozen=True)
class WriterRanks:
    #: The precedence the operator stated. One this does not name is ranked
    #: below those it does, by when it wrote — the order the run asked in.
    providers: tuple[str, ...] = ()
    pinned: frozenset[str] = frozenset()
    #: Providers switched off. Their rows stay in the ledger and stop being
    #: resolved, which is what switching one back on undoes.
    disabled: frozenset[str] = frozenset()

    def band_of(self, write: StoredFieldWrite) -> WriterBand | None:
        if write.writer_kind is not WriterBand.PROVIDER:
            return write.writer_kind
        if write.writer in self.disabled:
            return None
        return WriterBand.PINNED if write.writer in self.pinned else WriterBand.PROVIDER

    def position(self, writer: str) -> int:
        if writer not in self.providers:
            return len(self.providers)
        return self.providers.index(writer)


def item_writer_ranks(cursor: sqlite3.Cursor, pinned: frozenset[str]) -> WriterRanks:
    """The precedence an item rebuilds under: what the operator set on the
    settings page — or, until they set one, the shipped order the run itself
    asks in — and the records they pinned this item to.
    """
    cursor.execute(
        "SELECT key, value_json FROM settings WHERE key = ? OR key LIKE ?",
        (PROVIDER_ORDER_KEY, ENABLED_KEY_PATTERN),
    )
    order = get_enrichment_registry().shipped_provider_order()
    disabled: set[str] = set()
    for row in cursor.fetchall():
        stored = json.loads(row["value_json"])
        if row["key"] == PROVIDER_ORDER_KEY:
            if isinstance(stored, list):
                order = tuple(str(name) for name in stored)
        elif not stored and (off := provider_of_enabled_key(row["key"])) is not None:
            disabled.add(off)
    return WriterRanks(providers=order, pinned=pinned, disabled=frozenset(disabled))


def rebuild_item_fields(
    cursor: sqlite3.Cursor, db_id: int, ranks: WriterRanks
) -> RebuiltFields:
    ranked = _ranked(read_field_writes(cursor, db_id), ranks)
    named = ranked.get(SERIES_NAME_KEY, [])
    resolved: dict[str, Any] = {}
    cleared: set[str] = set()
    for field, writes in ranked.items():
        if not writes:
            cleared.add(field)
            continue
        if field == SERIES_NAME_KEY:
            # The first writer to name the series keeps it: a later one naming
            # another series is describing a different work, not correcting
            # this one, so rank never re-opens the name.
            continue
        if field == SERIES_POSITION_KEY:
            resolved.update(
                _best_founded_ordinal(
                    writes, _first_named(named), _named_by_writer(named)
                )
            )
        elif field == COVER_FIELD:
            resolved.update(_first_live_cover(cursor, db_id, writes))
        elif field in MERGEABLE_DETAIL_COLUMNS:
            resolved[field] = _combined_names(_stopping_at_the_operator(writes))
        elif field in MONOTONIC_DETAIL_COLUMNS:
            resolved.update(_highest_count(field, writes))
        else:
            resolved[field] = writes[0].value
    return RebuiltFields(values=resolved, cleared=frozenset(cleared))


def counts_for_the_group(field: str, writer_kind: WriterBand, absorbed: bool) -> bool:
    """A hold belongs to the row it was made against: the manual_fields read and
    the release door are both survivor-only, so an absorbed row's hold entering
    the group would decide a field nobody can see or release.
    """
    return not absorbed or (
        writer_kind is not WriterBand.MANUAL and field in _ABSORBED_ROW_STATES
    )


def fields_leaving_the_group(
    cursor: sqlite3.Cursor, survivor_id: int, departed_id: int
) -> frozenset[str]:
    """The columns an undo empties: a field whose only word came from the rows
    that just left. Not one the rebuild leaves unresolved — a provider switched
    off still has its word on file.
    """
    departed = {
        write.field
        for write in read_field_writes(cursor, departed_id)
        # Read as the absorbed rows they were in the group they are leaving.
        if counts_for_the_group(write.field, write.writer_kind, True)
    }
    return frozenset(
        departed
        - {
            write.field
            for write in read_field_writes(cursor, survivor_id)
            if counts_for_the_group(write.field, write.writer_kind, write.absorbed)
        }
    )


def _strength(band: WriterBand, field: str) -> int:
    if field in SOURCE_FIRST_FIELDS:
        band = _SOURCE_FIRST_TRADE.get(band, band)
    return _BAND_STRENGTH[band]


def _ranked(
    writes: Iterable[StoredFieldWrite], ranks: WriterRanks
) -> dict[str, list[StoredFieldWrite]]:
    graded: dict[str, list[tuple[_RankKey, StoredFieldWrite]]] = {}
    for write in writes:
        if not counts_for_the_group(write.field, write.writer_kind, write.absorbed):
            continue
        entries = graded.setdefault(write.field, [])
        band = ranks.band_of(write)
        if band is None:
            continue
        key = (
            -_strength(band, write.field),
            ranks.position(write.writer),
            # The item's own word first: an absorbed row's older one would
            # otherwise outrank what was stated on the row the operator kept.
            write.absorbed,
            write.written_at,
            write.writer,
        )
        entries.append((key, write))
    return {
        field: [write for _, write in sorted(entries, key=lambda entry: entry[0])]
        for field, entries in graded.items()
    }


def _stopping_at_the_operator(
    writes: list[StoredFieldWrite],
) -> list[StoredFieldWrite]:
    """A hand-written list is the list: combining it with what a source offers
    would refill the genre the operator just deleted.
    """
    if writes and writes[0].writer_kind is WriterBand.MANUAL:
        return writes[:1]
    return writes


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


def _first_named(writes: list[StoredFieldWrite]) -> str | None:
    """The series the item is in, which is the name the blob kept. Its own rows
    come before an absorbed row's, which would move it into that row's series.
    """
    stated = sorted(
        (write for write in writes if write.value),
        key=lambda write: (write.absorbed, write.written_at, write.writer),
    )
    return str(stated[0].value) if stated else None


def _named_by_writer(
    writes: list[StoredFieldWrite],
) -> dict[tuple[WriterBand, str], str]:
    named: dict[tuple[WriterBand, str], str] = {}
    for write in writes:
        if write.value:
            # Rank-ordered, so the item's own word stands where a row it
            # absorbed shares the writer.
            named.setdefault((write.writer_kind, write.writer), str(write.value))
    return named


def _counted_in_this_series(
    write: StoredFieldWrite,
    named: Mapping[tuple[WriterBand, str], str],
    series: str | None,
) -> bool:
    """An ordinal counts only within the series its writer named, naming none
    included: a number counted elsewhere positions the wrong work under the
    franchise's name. Excepted is the operator, whose modal writes a position
    alone.
    """
    if series is None or write.writer_kind is WriterBand.MANUAL:
        return True
    counted = named.get((write.writer_kind, write.writer))
    return counted is not None and series_names_agree(counted, series)


def _best_founded_ordinal(
    writes: list[StoredFieldWrite],
    series: str | None,
    named: Mapping[tuple[WriterBand, str], str],
) -> dict[str, Any]:
    best: tuple[float, SeriesAuthority] | None = None
    for write in writes:
        if not _counted_in_this_series(write, named, series):
            continue
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
