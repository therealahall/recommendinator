"""Both interfaces build their JSON here, so a field added to a web response
model without a line here is a field the CLI stops emitting.
"""

from collections.abc import Iterable, Mapping
from typing import TypedDict

from src.covers import cover_payload_url
from src.enrichment.manager import EnrichmentStart
from src.enrichment.provider_base import pins_of
from src.enrichment.registry import get_enrichment_registry
from src.models.content import ContentItem, get_enum_value
from src.models.detail_fields import text_names, to_int
from src.storage.enrichment_status import ResetCounts
from src.storage.field_rebuild import RANK_FREE_FIELDS
from src.storage.field_writes import StoredFieldWrite, WriterBand
from src.storage.merge import MERGEABLE_DETAIL_COLUMNS
from src.utils.matching import Candidate
from src.utils.series import (
    get_series_name_from_metadata,
    get_series_position_from_metadata,
)


class RelatedItemPayload(TypedDict):
    """A library item named as the reason for another one."""

    db_id: int | None
    title: str
    author: str | None
    content_type: str
    cover_url: str | None


def related_item_to_dict(item: ContentItem) -> RelatedItemPayload:
    # A subset of ``item_to_dict``: a recommendation carries two lists of these,
    # and every description and genre list on them would dwarf the pick itself.
    return {
        "db_id": item.db_id,
        "title": item.title,
        "author": item.author,
        "content_type": get_enum_value(item.content_type),
        "cover_url": cover_payload_url(item),
    }


def extract_tv_season_fields(
    item: ContentItem,
) -> tuple[list[int] | None, int | None]:
    if get_enum_value(item.content_type) != "tv_show":
        return None, None
    metadata = item.metadata
    seasons_watched = metadata.get("seasons_watched")
    total_seasons: int | None = None
    seasons_raw = metadata.get("seasons")
    if seasons_raw is not None:
        try:
            total_seasons = int(seasons_raw)
        except (ValueError, TypeError):
            pass
    return seasons_watched, total_seasons


def item_to_dict(item: ContentItem) -> dict[str, object]:
    seasons_watched, total_seasons = extract_tv_season_fields(item)
    metadata = item.metadata
    return {
        "external_ids": [pair.model_dump() for pair in item.external_ids],
        "db_id": item.db_id,
        "title": item.title,
        "author": item.author,
        "content_type": get_enum_value(item.content_type),
        "status": get_enum_value(item.status),
        "rating": item.rating,
        "review": item.review,
        "source": item.source,
        "cover_url": cover_payload_url(item),
        "date_completed": (
            item.date_completed.isoformat() if item.date_completed else None
        ),
        "ignored": bool(item.ignored),
        "seasons_watched": seasons_watched,
        "total_seasons": total_seasons,
        # None means the state is unknown (an item not read back from storage),
        # which the wire type presents as "not enriched".
        "enriched": bool(item.enriched),
        "manual_fields": list(item.manual_fields),
        "pinned": pins_of(metadata),
        "release_year": to_int(metadata.get("release_year")),
        "series": get_series_name_from_metadata(metadata),
        "series_index": get_series_position_from_metadata(metadata),
        "genres": metadata.get("genres") or [],
        "tags": metadata.get("tags") or [],
        "description": metadata.get("description"),
    }


class FieldWriterPayload(TypedDict):
    """One writer's word on one field, rendered as both interfaces show it."""

    writer: str
    band: str
    value: str


class FieldOfferPayload(TypedDict):
    field: str
    chosen: str | None
    #: Why this field offers no writer, '' where it offers some.
    note: str
    writers: list[FieldWriterPayload]


#: Fields listed with no writer to follow: their value is not decided by rank,
#: so a choice on one could move nothing.
SETTLED_FIELDS: frozenset[str] = RANK_FREE_FIELDS | MERGEABLE_DETAIL_COLUMNS


def _settled_note(field: str) -> str:
    """One sentence for both interfaces, so neither reads a settled field back
    as one nobody has stated.
    """
    return (
        f"{field.replace('_', ' ').capitalize()} is not decided by rank,"
        " so no choice can move it."
    )


def _offer_order(write: StoredFieldWrite) -> tuple[str, bool, bool, str]:
    """The operator's own entry heads a field, then the item's own rows."""
    return (
        write.field,
        write.writer_kind is not WriterBand.MANUAL,
        write.absorbed,
        write.writer,
    )


def field_writers_to_dict(
    db_id: int,
    writes: Iterable[StoredFieldWrite],
    choices: Mapping[str, str],
) -> dict[str, object]:
    """What every writer says about each field, the operator's own entry among
    them. A legacy row is left out: nobody claimed those values. A settled field
    is listed with no writer, keeping it apart from one nobody has stated.
    """
    offered: dict[str, list[FieldWriterPayload]] = {}
    for write in sorted(writes, key=_offer_order):
        if write.writer_kind is WriterBand.LEGACY:
            continue
        if write.field in SETTLED_FIELDS:
            offered.setdefault(write.field, [])
            continue
        value = ", ".join(text_names(write.value))
        if not value:
            continue
        stated = offered.setdefault(write.field, [])
        # A merged group holds one row per writer per row it absorbed, and a
        # choice names the writer rather than any one of those rows.
        if any(row["writer"] == write.writer for row in stated):
            continue
        stated.append(
            {"writer": write.writer, "band": write.writer_kind.value, "value": value}
        )
    return {
        "item_id": db_id,
        "fields": [
            {
                "field": name,
                "chosen": choices.get(name),
                "note": _settled_note(name) if name in SETTLED_FIELDS else "",
                "writers": writers,
            }
            for name, writers in sorted(offered.items())
        ],
    }


class EnrichmentProviderPayload(TypedDict):
    """An installed provider, as both interfaces name one to the operator."""

    name: str
    display_name: str


def enrichment_providers_to_list() -> list[EnrichmentProviderPayload]:
    """Discovered rather than listed, so installing a provider is the whole of
    offering it a reset.
    """
    return [
        {"name": name, "display_name": provider.display_name}
        for name, provider in sorted(
            get_enrichment_registry().get_all_providers().items()
        )
    ]


class UnknownEnrichmentProvider(ValueError):
    """A reset naming a provider nothing installs, worded here so neither
    interface answers it by resetting nothing.
    """


def enrichment_provider_filter(named: str | None) -> str | None:
    """The provider a reset filters on, ``None`` for no filter — which an absent
    name, an empty one and "all" all mean. Lower-cased because the CLI's
    ``--provider`` is free text.
    """
    if named is None:
        return None
    provider = named.strip().lower()
    if not provider or provider == "all":
        return None
    installed = sorted(get_enrichment_registry().get_all_providers())
    if provider not in installed:
        raise UnknownEnrichmentProvider(
            f"Unknown provider '{named}'. Installed: {', '.join(installed)}."
        )
    return provider


def enrichment_candidates_to_dict(
    db_id: int, offered: list[tuple[str, Candidate]], pinned: dict[str, str]
) -> dict[str, object]:
    return {
        "item_id": db_id,
        "candidates": [
            {
                "provider": provider,
                "record_id": candidate.record_id,
                "title": candidate.title,
                "year": candidate.year,
                "creator": candidate.creator,
                "cover_url": candidate.cover_url,
            }
            for provider, candidate in offered
        ],
        "pinned": pinned,
    }


_TURN_ONE_ON = (
    "Turn one on from the Data tab, or run: settings set enrichment.enabled true"
)

#: Two refusals, because searching is a capability of its own: a provider can
#: cover a type it can neither be asked to search nor to match.
ENRICHMENT_UNAVAILABLE = (
    f"Enrichment is off, or no enabled provider covers that type. {_TURN_ONE_ON}"
)
CANDIDATES_UNAVAILABLE = (
    f"Enrichment is off, or no enabled provider searches that type. {_TURN_ONE_ON}"
)

_RUN_CLAUSES = {
    EnrichmentStart.STARTED: "Enriching it now.",
    EnrichmentStart.ALREADY_RUNNING: "Queued for the next enrichment run.",
    EnrichmentStart.UNAVAILABLE: (
        "Queued: enrichment is off, or no enabled provider covers this type."
    ),
}


def enrichment_pin_to_dict(
    db_id: int,
    provider: str,
    record_id: str | None,
    pinned: dict[str, str],
    started: EnrichmentStart | None,
) -> dict[str, object]:
    said = (
        f"Item {db_id} is back to matching {provider} by title"
        if started is None
        else f"Item {db_id} now enriches from {provider} record {record_id}. "
        f"{_RUN_CLAUSES[started]}"
    )
    return {
        "item_id": db_id,
        "pinned": pinned,
        "message": said,
        "run": started.value if started else None,
    }


def enrichment_requeue_to_dict(
    db_id: int, started: EnrichmentStart
) -> dict[str, object]:
    return {
        "item_id": db_id,
        "message": (
            f"Item {db_id} is queued for enrichment, everything it holds "
            f"standing. {_RUN_CLAUSES[started]}"
        ),
        "run": started.value,
    }


def enrichment_reset_to_dict(
    counts: ResetCounts, started: EnrichmentStart | None, hard: bool = False
) -> dict[str, object]:
    stated = "what the providers stated"
    if hard:
        stated += " and the values no writer claimed"
    said = (
        f"Dropped {stated} for {counts.stripped} item(s)"
        f" and re-queued {counts.requeued} item(s)"
    )
    if started is not None:
        said = f"{said}. {_RUN_CLAUSES[started]}"
    return {
        "message": said,
        "dropped": counts.stripped,
        "requeued": counts.requeued,
        "run": started.value if started else None,
    }


def completion_to_dict(title: str, db_id: int) -> dict[str, object]:
    return {"message": f"Marked '{title}' as completed", "id": db_id}


def ignore_result_to_dict(db_id: int, title: str, ignored: bool) -> dict[str, object]:
    return {
        "db_id": db_id,
        "title": title,
        "ignored": ignored,
        "message": f"Item '{title}' {'ignored' if ignored else 'unignored'}",
    }
