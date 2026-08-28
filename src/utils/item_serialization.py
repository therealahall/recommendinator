"""Both interfaces build their JSON here, so a field added to a web response
model without a line here is a field the CLI stops emitting.
"""

from src.models.content import ContentItem, get_enum_value
from src.models.detail_fields import to_int
from src.utils.series import (
    get_series_name_from_metadata,
    get_series_position_from_metadata,
)


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
        "date_completed": (
            item.date_completed.isoformat() if item.date_completed else None
        ),
        "ignored": bool(item.ignored),
        "seasons_watched": seasons_watched,
        "total_seasons": total_seasons,
        # None means the state is unknown (an item not read back from storage),
        # which the wire type presents as "not enriched".
        "enriched": bool(item.enriched),
        "manually_enriched": bool(item.manually_enriched),
        "release_year": to_int(metadata.get("release_year")),
        "series": get_series_name_from_metadata(metadata),
        "series_index": get_series_position_from_metadata(metadata),
        "genres": metadata.get("genres") or [],
        "tags": metadata.get("tags") or [],
        "description": metadata.get("description"),
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
