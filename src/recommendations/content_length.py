"""Normalized content length preferences and filtering.

Provides a unified length preference system across content types, replacing
the old ``minimum_book_pages`` / ``maximum_movie_runtime`` fields with a
simpler short/medium/long/any model.
"""

from __future__ import annotations

from enum import Enum

from src.models.content import ContentItem, ContentType


class LengthPreference(str, Enum):
    """User preference for content length."""

    ANY = "any"
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


# Thresholds per content type: (short_max, medium_max)
# Short: value <= short_max
# Medium: short_max < value <= medium_max
# Long: value > medium_max
_THRESHOLDS: dict[str, tuple[int, int]] = {
    "book": (250, 500),  # pages
    "movie": (90, 150),  # minutes
    "tv_show": (3, 6),  # seasons
    "video_game": (10, 40),  # hours (main story)
}

# Metadata keys to check for each content type
_LENGTH_METADATA_KEYS: dict[str, list[str]] = {
    "book": ["pages", "num_pages", "number_of_pages"],
    "movie": ["runtime", "runtime_minutes"],
    "tv_show": ["seasons", "number_of_seasons"],
    "video_game": ["playtime_hours", "main_story_hours", "average_playtime_hours"],
}


def _get_content_type_value(content_type: ContentType | str) -> str:
    """Extract string value from a ContentType enum or string.

    Args:
        content_type: ContentType enum or string value.

    Returns:
        Lowercase string value.
    """
    # ContentType is a str subclass, so use isinstance to properly narrow the type
    if isinstance(content_type, ContentType):
        return content_type.value.lower()
    return content_type.lower()


def get_length_value(item: ContentItem) -> int | None:
    """Extract the length value from a content item's metadata.

    Args:
        item: Content item to inspect.

    Returns:
        The numeric length value, or ``None`` if no length metadata is present.
    """
    content_type_str = _get_content_type_value(item.content_type)
    keys = _LENGTH_METADATA_KEYS.get(content_type_str, [])

    if not item.metadata:
        return None

    for key in keys:
        value = item.metadata.get(key)
        if value is not None:
            try:
                return int(value)
            except (ValueError, TypeError):
                continue

    return None


def classify_length(
    item: ContentItem,
) -> LengthPreference | None:
    """Classify an item's length as short, medium, or long.

    Args:
        item: Content item to classify.

    Returns:
        The length classification, or ``None`` if no length metadata is
        available.
    """
    content_type_str = _get_content_type_value(item.content_type)
    thresholds = _THRESHOLDS.get(content_type_str)
    if thresholds is None:
        return None

    length_value = get_length_value(item)
    if length_value is None:
        return None

    short_max, medium_max = thresholds
    if length_value <= short_max:
        return LengthPreference.SHORT
    if length_value <= medium_max:
        return LengthPreference.MEDIUM
    return LengthPreference.LONG


def score_length_match(
    item: ContentItem,
    content_length_preferences: dict[str, str],
) -> float:
    """Score how well an item matches the user's length preference.

    Returns a score between 0.0 and 1.0:
    - 1.0: exact match or ``"any"`` preference or no metadata
    - 0.7: adjacent category (short↔medium or medium↔long)
    - 0.4: opposite ends (short↔long)
    - 0.8: no length metadata available (benefit of the doubt)

    Args:
        item: Content item to score.
        content_length_preferences: Mapping of content type string to
            length preference string.

    Returns:
        A float between 0.0 and 1.0.
    """
    content_type_str = _get_content_type_value(item.content_type)
    preference_str = content_length_preferences.get(content_type_str, "any")

    if preference_str == "any":
        return 1.0

    classification = classify_length(item)
    if classification is None:
        return 0.8  # no metadata — benefit of the doubt

    if classification.value == preference_str:
        return 1.0

    # Adjacent vs opposite penalty
    order = [LengthPreference.SHORT, LengthPreference.MEDIUM, LengthPreference.LONG]
    try:
        pref_enum = LengthPreference(preference_str)
    except ValueError:
        return 1.0  # unrecognised preference string — no penalty

    distance = abs(order.index(classification) - order.index(pref_enum))
    if distance == 1:
        return 0.7  # adjacent
    return 0.4  # opposite ends


def passes_length_filter(
    item: ContentItem,
    content_length_preferences: dict[str, str],
) -> bool:
    """Check whether an item passes the user's length preference filter.

    Items missing length metadata always pass (we don't filter what we
    can't measure).  A preference of ``"any"`` always passes.

    Args:
        item: Content item to check.
        content_length_preferences: Mapping of content type string to
            length preference string (e.g. ``{"book": "short"}``).

    Returns:
        ``True`` if the item should be included.
    """
    content_type_str = _get_content_type_value(item.content_type)
    preference_str = content_length_preferences.get(content_type_str, "any")

    if preference_str == "any":
        return True

    classification = classify_length(item)
    if classification is None:
        # No length metadata -- pass through unfiltered
        return True

    return classification.value == preference_str


def filter_by_length(
    items: list[ContentItem],
    content_length_preferences: dict[str, str],
) -> list[ContentItem]:
    """Filter a list of items by length preferences.

    Args:
        items: Candidate items.
        content_length_preferences: Mapping of content type string to
            length preference string.

    Returns:
        Filtered list of items that pass length preferences.
    """
    return [
        item for item in items if passes_length_filter(item, content_length_preferences)
    ]
