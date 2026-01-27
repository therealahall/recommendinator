"""Conflict resolution for source-of-truth conflicts during ingestion."""

from enum import Enum

from src.models.content import ContentItem


class ConflictStrategy(str, Enum):
    """Strategy for resolving conflicts when an item already exists.

    LAST_WRITE_WINS: Incoming item overwrites existing (default behavior).
    SOURCE_PRIORITY: Higher-priority source wins based on configured ordering.
    KEEP_EXISTING: Never overwrite; only fill in None fields from incoming.
    """

    LAST_WRITE_WINS = "last_write_wins"
    SOURCE_PRIORITY = "source_priority"
    KEEP_EXISTING = "keep_existing"


def resolve_conflict(
    existing: ContentItem,
    incoming: ContentItem,
    strategy: ConflictStrategy = ConflictStrategy.LAST_WRITE_WINS,
    source_priority: list[str] | None = None,
) -> ContentItem:
    """Resolve a conflict between an existing and incoming content item.

    Args:
        existing: The content item already in the database.
        incoming: The new content item from ingestion.
        strategy: Which conflict resolution strategy to apply.
        source_priority: Ordered list of source names (highest priority first).
            Only used with SOURCE_PRIORITY strategy.

    Returns:
        The resolved ContentItem to save.
    """
    if strategy == ConflictStrategy.LAST_WRITE_WINS:
        return _last_write_wins(existing, incoming)
    elif strategy == ConflictStrategy.SOURCE_PRIORITY:
        return _source_priority(existing, incoming, source_priority or [])
    else:
        return _keep_existing(existing, incoming)


def _last_write_wins(existing: ContentItem, incoming: ContentItem) -> ContentItem:
    """Incoming item overwrites existing entirely."""
    return incoming


def _source_priority(
    existing: ContentItem,
    incoming: ContentItem,
    source_priority: list[str],
) -> ContentItem:
    """Higher-priority source wins; if same priority, incoming wins.

    If a source is not in the priority list, it is treated as lowest priority.

    Args:
        existing: The existing content item.
        incoming: The incoming content item.
        source_priority: Ordered list of source names (highest priority first).

    Returns:
        The winning ContentItem.
    """
    existing_source = existing.source or ""
    incoming_source = incoming.source or ""

    # Lower index = higher priority. Sources not in list get max index.
    max_index = len(source_priority)
    existing_priority = (
        source_priority.index(existing_source)
        if existing_source in source_priority
        else max_index
    )
    incoming_priority = (
        source_priority.index(incoming_source)
        if incoming_source in source_priority
        else max_index
    )

    if existing_priority < incoming_priority:
        # Existing source has higher priority — keep existing but fill None fields
        return _fill_none_fields(existing, incoming)
    else:
        # Incoming has equal or higher priority — use incoming but fill None fields
        return _fill_none_fields(incoming, existing)


def _keep_existing(existing: ContentItem, incoming: ContentItem) -> ContentItem:
    """Keep existing data; only fill in fields that are None on existing."""
    return _fill_none_fields(existing, incoming)


def _fill_none_fields(primary: ContentItem, secondary: ContentItem) -> ContentItem:
    """Return a copy of primary with None fields filled from secondary.

    Args:
        primary: The primary item (its non-None values take precedence).
        secondary: The secondary item (used to fill None fields).

    Returns:
        A new ContentItem with None fields filled from secondary.
    """
    filled_data = primary.model_dump()

    # Fields that can be filled from secondary
    fillable_fields = [
        "author",
        "rating",
        "review",
        "date_completed",
    ]

    for field_name in fillable_fields:
        if filled_data.get(field_name) is None:
            secondary_value = getattr(secondary, field_name)
            if secondary_value is not None:
                filled_data[field_name] = secondary_value

    # Merge metadata: primary metadata takes precedence, secondary fills gaps
    secondary_metadata = secondary.metadata or {}
    primary_metadata = filled_data.get("metadata", {}) or {}
    merged_metadata = {**secondary_metadata, **primary_metadata}
    filled_data["metadata"] = merged_metadata

    return ContentItem(**filled_data)
