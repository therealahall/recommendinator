"""Series detection and parsing utilities for all content types.

Supports series detection for:
- Books: Book 1, 2, 3, etc. (e.g., "The Witcher, Book 1")
- TV Shows: Season 1, 2, 3, etc. (e.g., "The Expanse, Season 1")
- Movies: Part 1, 2, 3, etc. or Episode N (e.g., "Lord of the Rings, Part 1")
- Video Games: Part 1, 2, 3, etc. (e.g., "Mass Effect, #1")
"""

import re
from collections import defaultdict
from typing import NamedTuple

from src.models.content import ContentItem, ContentType


class _SeriesPattern(NamedTuple):
    """Pre-compiled regex pattern for series detection."""

    regex: re.Pattern[str]
    max_number: int


# Pre-compiled patterns tried in order. Each captures (series_name, number).
_SERIES_PATTERNS: list[_SeriesPattern] = [
    # (Series Name, #N) or (Series Name #N)
    _SeriesPattern(re.compile(r"\(([^,]+?)(?:,\s*)?#\s*(\d+)\)"), 1000),
    # (Series Name, Book N)
    _SeriesPattern(re.compile(r"\(([^,]+?),\s*Book\s+(\d+)\)", re.IGNORECASE), 1000),
    # (Series Name, Season N)
    _SeriesPattern(re.compile(r"\(([^,]+?),\s*Season\s+(\d+)\)", re.IGNORECASE), 100),
    # (Series Name, SN) — shorthand
    _SeriesPattern(re.compile(r"\(([^,]+?),\s*S(\d+)\)", re.IGNORECASE), 100),
    # (Series Name, Part N)
    _SeriesPattern(re.compile(r"\(([^,]+?),\s*Part\s+(\d+)\)", re.IGNORECASE), 100),
    # (Series Name, Episode N)
    _SeriesPattern(re.compile(r"\(([^,]+?),\s*Episode\s+(\d+)\)", re.IGNORECASE), 100),
    # (Series Name N) — generic fallback
    _SeriesPattern(re.compile(r"\(([^,]+?)\s+(\d+)\)"), 100),
]


def extract_series_info(
    title: str,
    metadata: dict | None = None,
    content_type: ContentType | None = None,
) -> tuple[str, int] | None:
    """Extract series name and item number from title or metadata.

    Works for all content types (books, games, TV shows, movies, etc.).

    Handles patterns like:
    - Books: "Title (Series Name, #1)", "Title (Series Name, Book 1)"
    - TV Shows: "Title (Series Name, Season 1)", "Title (Series Name, S1)"
    - Movies: "Title (Series Name, Part 1)", "Title (Series Name, Episode 1)"
    - Games: "Title (Series Name, #1)", "Title (Series Name, Part 1)"

    Also checks metadata for series information if title parsing fails.

    Args:
        title: Content title
        metadata: Optional metadata dictionary that may contain series info
        content_type: Optional content type to help with pattern matching

    Returns:
        Tuple of (series_name, item_number) if found, None otherwise
    """
    # First try to extract from metadata if available
    if metadata:
        series_info = _extract_from_metadata(metadata, content_type)
        if series_info:
            return series_info

    # Try each pre-compiled pattern in order
    for pattern in _SERIES_PATTERNS:
        match = pattern.regex.search(title)
        if match:
            series_name = match.group(1).strip()
            item_num = int(match.group(2))
            if 1 <= item_num <= pattern.max_number:
                return (series_name, item_num)

    return None


def _extract_from_metadata(
    metadata: dict, content_type: ContentType | None = None
) -> tuple[str, int] | None:
    """Extract series information from metadata.

    Checks common metadata fields for series information:
    - series_name, series, series_title
    - series_number, series_num, season, episode, part

    Args:
        metadata: Metadata dictionary
        content_type: Optional content type to help with field selection

    Returns:
        Tuple of (series_name, item_number) if found, None otherwise
    """
    # Try to find series name
    series_name = None
    for key in ["series_name", "series", "series_title", "franchise"]:
        if key in metadata and metadata[key]:
            series_name = str(metadata[key]).strip()
            break

    if not series_name:
        return None

    # Try to find item number based on content type
    item_num = None

    if content_type == ContentType.TV_SHOW:
        # For TV shows, look for season number
        for key in ["season", "season_number", "season_num"]:
            if key in metadata and metadata[key]:
                try:
                    item_num = int(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue
    elif content_type == ContentType.MOVIE:
        # For movies, look for part/episode number
        for key in [
            "part",
            "part_number",
            "episode",
            "episode_number",
            "movie_number",
        ]:
            if key in metadata and metadata[key]:
                try:
                    item_num = int(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue
    else:
        # For books and games, look for series number
        for key in [
            "series_number",
            "series_num",
            "book_number",
            "book_num",
            "part",
            "part_number",
        ]:
            if key in metadata and metadata[key]:
                try:
                    item_num = int(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue

    if series_name and item_num and 1 <= item_num <= 1000:
        return (series_name, item_num)

    return None


def get_series_name(
    item: ContentItem | None = None, title: str | None = None
) -> str | None:
    """Get series name from ContentItem or title (checks title and metadata).

    Args:
        item: Optional ContentItem to extract series name from
        title: Optional title string (for backward compatibility)

    Returns:
        Series name if found, None otherwise
    """
    if item:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
    elif title:
        series_info = extract_series_info(title)
    else:
        return None

    return series_info[0] if series_info else None


def get_series_item_number(
    item: ContentItem | str | None = None, title: str | None = None
) -> int | None:
    """Get item number in series from ContentItem or title.

    Args:
        item: Optional ContentItem or string (for backward compatibility)
        title: Optional title string (for backward compatibility)

    Returns:
        Item number if found, None otherwise
    """
    # Handle backward compatibility: if first arg is a string, treat as title
    if isinstance(item, str):
        title = item
        item = None

    if item:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
    elif title:
        series_info = extract_series_info(title)
    else:
        return None

    return series_info[1] if series_info else None


def expand_tv_shows_to_seasons(items: list[ContentItem]) -> list[ContentItem]:
    """Expand TV show items into season-level items for granular recommendations.

    Library stays at show level; this expansion is for recommendation scoring only.
    Each show with total_seasons in metadata becomes N items (one per season).
    Shows without season info are passed through unchanged.

    Args:
        items: List of ContentItem (expected ContentType.TV_SHOW)

    Returns:
        Expanded list with season-level items where applicable
    """
    expanded: list[ContentItem] = []
    for item in items:
        if item.content_type != ContentType.TV_SHOW:
            expanded.append(item)
            continue

        total_seasons = None
        for key in ["total_seasons", "seasons", "number_of_seasons"]:
            val = item.metadata.get(key)
            if val is not None:
                try:
                    total_seasons = int(val)
                    break
                except (ValueError, TypeError):
                    continue

        if total_seasons is None or total_seasons < 1:
            expanded.append(item)
            continue

        base_id = item.id or ""
        show_title = item.title

        for season_num in range(1, total_seasons + 1):
            season_title = f"{show_title} (Season {season_num})"
            season_id = f"{base_id}:s{season_num}" if base_id else None
            season_metadata = dict(item.metadata)
            season_metadata["series_name"] = show_title
            season_metadata["season"] = season_num
            season_metadata["season_number"] = season_num

            expanded.append(
                ContentItem(
                    id=season_id,
                    title=season_title,
                    author=item.author,
                    content_type=ContentType.TV_SHOW,
                    rating=item.rating,
                    status=item.status,
                    parent_id=item.id,
                    metadata=season_metadata,
                    source=item.source,
                )
            )

    return expanded


def build_series_tracking(
    consumed_items: list[ContentItem],
) -> dict[str, set[int]]:
    """Build a map of series names to item numbers the user has consumed.

    Works for all content types (books, games, TV shows, movies).

    Args:
        consumed_items: List of consumed ContentItem objects

    Returns:
        Dictionary mapping series names to sets of item numbers
    """
    series_tracking: dict[str, set[int]] = defaultdict(set)

    for item in consumed_items:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
        if series_info:
            series_name, item_num = series_info
            series_tracking[series_name].add(item_num)

    return dict(series_tracking)


def is_series_started(series_name: str, series_tracking: dict[str, set[int]]) -> bool:
    """Check if user has started a series.

    Args:
        series_name: Series name
        series_tracking: Series tracking dictionary

    Returns:
        True if user has at least one item from the series
    """
    return series_name in series_tracking and len(series_tracking[series_name]) > 0


def is_first_item_in_series(
    item: ContentItem | str | None = None, title: str | None = None
) -> bool:
    """Check if this is the first item in a series.

    Works for all content types (Book 1, Season 1, Part 1, etc.).

    Args:
        item: Optional ContentItem or string (for backward compatibility)
        title: Optional title string (for backward compatibility)

    Returns:
        True if this is item #1 in a series
    """
    # Handle backward compatibility: if first arg is a string, treat as title
    if isinstance(item, str):
        title = item
        item = None

    if item:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
    elif title:
        series_info = extract_series_info(title)
    else:
        return False

    return series_info is not None and series_info[1] == 1


def should_recommend_item(
    item: ContentItem,
    series_tracking: dict[str, set[int]],
    unconsumed_items: list[ContentItem] | None = None,
) -> bool:
    """Determine if an item should be recommended based on series rules.

    Works for all content types (books, games, TV shows, movies). Rules:
    - If not in a series: recommend
    - If first item (#1) in unstarted series: recommend
    - If user has completed all previous items: recommend
    - If previous items exist in unconsumed data but aren't completed:
      don't recommend
    - If previous items don't exist in unconsumed data: recommend
      (assume they don't exist)
    - Special case: If user has consumed item #0 (prequel), recommend item #1

    Examples:
    - Books: If you've read Book 1 and 2, Book 3 is recommended
    - TV Shows: If you've watched Season 1, Season 2 is recommended
    - Movies: If you've watched Part 1, Part 2 is recommended
    - Games: If you've played Game 1, Game 2 is recommended

    Args:
        item: ContentItem to check
        series_tracking: Series tracking dictionary (consumed items)
        unconsumed_items: Optional list of unconsumed items to check if
                         previous items exist in the data

    Returns:
        True if item should be recommended
    """
    series_info = extract_series_info(item.title, item.metadata, item.content_type)
    if not series_info:
        # Not in a series, always recommend
        return True

    series_name, item_num = series_info
    consumed_numbers = series_tracking.get(series_name, set())

    # Build set of unconsumed item numbers for this series
    unconsumed_item_nums: set[int] = set()
    if unconsumed_items:
        for unconsumed in unconsumed_items:
            unconsumed_series_info = extract_series_info(
                unconsumed.title, unconsumed.metadata, unconsumed.content_type
            )
            if unconsumed_series_info:
                (
                    unconsumed_series_name,
                    unconsumed_item_num,
                ) = unconsumed_series_info
                if unconsumed_series_name == series_name:
                    unconsumed_item_nums.add(unconsumed_item_num)

    if not consumed_numbers:
        # User hasn't started this series
        # Only recommend if it's the first item (#1) or prequel (#0)
        if item_num == 1 or item_num == 0:
            return True
        # If it's a later item, check if previous items exist in
        # unconsumed data. If unconsumed_items is None, be conservative
        # and don't recommend (we can't verify if previous items exist)
        if unconsumed_items is None:
            return False
        # If previous items exist in unconsumed data but aren't
        # completed, don't recommend
        for prev_num in range(1, item_num):
            if prev_num in unconsumed_item_nums:
                # Previous item exists in unconsumed data but isn't
                # completed
                return False
        # Previous items don't exist in unconsumed data, so recommend
        return True
    else:
        # User has started this series
        # Find the highest item number they've consumed
        max_consumed = max(consumed_numbers)

        # Special case: if they've consumed #0, recommend #1
        if max_consumed == 0 and item_num == 1:
            return True

        # Check if user has completed all previous items
        # Need to check all items from 1 to (item_num - 1)
        for prev_num in range(1, item_num):
            if prev_num not in consumed_numbers:
                # Previous item not consumed - check if it exists in
                # unconsumed data
                if prev_num in unconsumed_item_nums:
                    # Previous item exists but isn't completed - don't
                    # recommend
                    return False
                # Previous item doesn't exist in unconsumed data - assume OK
                # (might be a gap in the data or user can start anywhere)

        # User has completed all previous items (or they don't exist in
        # data). Recommend if it's the next item
        return item_num == max_consumed + 1
