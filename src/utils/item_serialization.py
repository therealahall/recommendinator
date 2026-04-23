"""Shared serialization helpers for ContentItem.

Used by both the CLI commands and the web API to guarantee identical
JSON output shapes between the two interfaces. Any new field on the
web ContentItemResponse must be added here so the CLI emits it too.
"""

from src.models.content import ContentItem, get_enum_value


def extract_tv_season_fields(
    item: ContentItem,
) -> tuple[list[int] | None, int | None]:
    """Extract seasons_watched and total_seasons from TV show metadata.

    Returns (None, None) for non-TV items or when the metadata does not
    contain the expected keys.
    """
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
    """Serialize a ContentItem to the shared CLI/web field set.

    The CLI emits this dict directly as JSON; the web API unpacks it
    into a ContentItemResponse. Keeping the construction in one place
    prevents field-set drift between the two interfaces.
    """
    seasons_watched, total_seasons = extract_tv_season_fields(item)
    return {
        "id": item.id,
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
    }
