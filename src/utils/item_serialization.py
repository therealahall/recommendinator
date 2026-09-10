"""Both interfaces build their JSON here, so a field added to a web response
model without a line here is a field the CLI stops emitting.
"""

from typing import TypedDict

from src.covers import cover_payload_url
from src.enrichment.manager import EnrichmentStart
from src.enrichment.provider_base import pins_of
from src.models.content import ContentItem, get_enum_value
from src.models.detail_fields import to_int
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


#: Both start doors word the refusal identically; drift between them is a defect.
ENRICHMENT_UNAVAILABLE = (
    "Enrichment is off, or no enabled provider handles that type. Turn one on "
    "from the Data tab, or run: settings set enrichment.enabled true"
)

_RUN_CLAUSES = {
    EnrichmentStart.STARTED: "Enriching it now.",
    EnrichmentStart.ALREADY_RUNNING: "Queued for the next enrichment run.",
    EnrichmentStart.UNAVAILABLE: (
        "Queued: enrichment is off, or no enabled provider handles this type."
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


def enrichment_reset_to_dict(
    count: int, started: EnrichmentStart | None
) -> dict[str, object]:
    said = f"Reset enrichment status for {count} item(s)"
    if started is not None:
        said = f"{said}. {_RUN_CLAUSES[started]}"
    return {"message": said, "count": count, "run": started.value if started else None}


def completion_to_dict(title: str, db_id: int) -> dict[str, object]:
    return {"message": f"Marked '{title}' as completed", "id": db_id}


def ignore_result_to_dict(db_id: int, title: str, ignored: bool) -> dict[str, object]:
    return {
        "db_id": db_id,
        "title": title,
        "ignored": ignored,
        "message": f"Item '{title}' {'ignored' if ignored else 'unignored'}",
    }
