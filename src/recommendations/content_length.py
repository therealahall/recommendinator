from __future__ import annotations

from enum import Enum

from src.models.content import ContentItem, get_enum_value


class LengthPreference(str, Enum):
    ANY = "any"
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


# Thresholds per content type: (short_max, medium_max)
_THRESHOLDS: dict[str, tuple[int, int]] = {
    "book": (250, 500),  # pages
    "movie": (90, 150),  # minutes
    "tv_show": (3, 6),  # seasons
    "video_game": (10, 40),  # hours of average playtime
}

# A game has one key only, RAWG's average across players. playtime_hours holds
# the user's own hours, from Steam or an imported hours_played column, which
# describes the player rather than the game.
_LENGTH_METADATA_KEYS: dict[str, list[str]] = {
    "book": ["pages", "num_pages", "number_of_pages"],
    "movie": ["runtime", "runtime_minutes"],
    "tv_show": ["seasons", "number_of_seasons"],
    "video_game": ["average_playtime_hours"],
}


def get_length_value(item: ContentItem) -> int | None:
    content_type_str = get_enum_value(item.content_type)
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
    content_type_str = get_enum_value(item.content_type)
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
    content_type_str = get_enum_value(item.content_type)
    preference_str = content_length_preferences.get(content_type_str, "any")

    if preference_str == "any":
        return 1.0

    classification = classify_length(item)
    if classification is None:
        return 0.8  # no metadata — benefit of the doubt

    if classification.value == preference_str:
        return 1.0

    order = [LengthPreference.SHORT, LengthPreference.MEDIUM, LengthPreference.LONG]
    try:
        pref_enum = LengthPreference(preference_str)
    except ValueError:
        return 1.0  # unrecognised preference string — no penalty

    distance = abs(order.index(classification) - order.index(pref_enum))
    if distance == 1:
        return 0.7
    return 0.4
