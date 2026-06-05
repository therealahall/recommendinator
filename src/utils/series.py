"""Series detection and parsing utilities for all content types.

Supports series detection for:
- Books: Book 1, 2, 3, etc. (e.g., "The Witcher, Book 1")
- TV Shows: Season 1, 2, 3, etc. (e.g., "The Expanse, Season 1")
- Movies: Part 1, 2, 3, etc. or Episode N (e.g., "Lord of the Rings, Part 1")
- Video Games: Part 1, 2, 3, etc. (e.g., "Mass Effect, #1")
"""

import math
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
    # (Series Name, #N) or (Series Name #N) — N may be fractional (e.g. #2.5
    # for half-numbered novellas like "Gods of Risk (The Expanse, #2.5)").
    _SeriesPattern(re.compile(r"\(([^,]+?)(?:,\s*)?#\s*(\d+(?:\.\d+)?)\)"), 1000),
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
    # (Series Name N) — generic fallback (N may be fractional)
    _SeriesPattern(re.compile(r"\(([^,]+?)\s+(\d+(?:\.\d+)?)\)"), 100),
]


def _roman_to_int(roman: str) -> int | None:
    """Convert a Roman numeral string to an integer.

    Args:
        roman: Roman numeral string (e.g. "XII", "IV").

    Returns:
        Integer value, or ``None`` if the string is not a valid Roman numeral.
    """
    roman_values: dict[str, int] = {
        "I": 1,
        "V": 5,
        "X": 10,
        "L": 50,
        "C": 100,
        "D": 500,
        "M": 1000,
    }
    upper = roman.upper().strip()
    if not upper or not all(char in roman_values for char in upper):
        return None

    total = 0
    previous = 0
    for char in reversed(upper):
        value = roman_values[char]
        if value < previous:
            total -= value
        else:
            total += value
        previous = value

    return total if total > 0 else None


# Trailing Arabic numeral in a title (e.g., "Dungeon Siege 3",
# "Fallout 4: New Vegas", "FINAL FANTASY XII THE ZODIAC AGE").
# The series name must start with a letter to avoid matching titles like
# "1942" or "2048".  The series-name capture uses ``.*?`` (lazy) so it
# can include colons/dashes (e.g., "Batman: Arkham Knight 2").  The
# suffix pattern allows space, colon, dash, em-dash, plus, or slash as
# delimiters so subtitles like "THE ZODIAC AGE" (space) and
# "+ Re Mind (DLC)" (plus) are matched.
_TITLE_ARABIC_PATTERN: re.Pattern[str] = re.compile(
    r"^([A-Za-z].*?)\s+(\d+)(?:[\s:—\-+/].+)?$"
)

# Trailing Roman numeral (e.g., "Final Fantasy XII", "Grand Theft Auto V").
# Uses ``[IVXLCDM]+`` instead of a strict structural regex so that
# standalone V (5), X (10), L (50), C (100) are accepted.  Validation
# happens downstream via ``_roman_to_int()`` + range check (1-100).
_TITLE_ROMAN_PATTERN: re.Pattern[str] = re.compile(
    r"^([A-Za-z].*?)\s+([IVXLCDM]+)(?:[\s:—\-+/].+)?$"
)


def _extract_series_from_title(title: str) -> tuple[str, float] | None:
    """Try to extract series info from trailing numbers in a title.

    Matches patterns like "Dungeon Siege 3" or "Final Fantasy XII".
    The series name must start with a letter to avoid false positives
    on titles that are just numbers (e.g. "1942", "2048").

    Args:
        title: Content title.

    Returns:
        Tuple of (series_name, number) or ``None``.
    """
    # Try Arabic numerals first (more common). Title-embedded game numbers are
    # whole numbers, but return a float to match the series-number type used
    # everywhere else (fractional novella positions like #2.5).
    match = _TITLE_ARABIC_PATTERN.match(title.strip())
    if match:
        series_name = match.group(1).strip()
        number = int(match.group(2))
        if 1 <= number <= 100 and len(series_name) >= 2:
            return (series_name, float(number))

    # Try Roman numerals
    match = _TITLE_ROMAN_PATTERN.match(title.strip())
    if match:
        series_name = match.group(1).strip()
        roman_str = match.group(2)
        roman_number = _roman_to_int(roman_str)
        if (
            roman_number is not None
            and 1 <= roman_number <= 100
            and len(series_name) >= 2
        ):
            return (series_name, float(roman_number))

    return None


def extract_series_info(
    title: str,
    metadata: dict | None = None,
    content_type: ContentType | None = None,
) -> tuple[str, float] | None:
    """Extract series name and item number from title or metadata.

    Works for all content types (books, games, TV shows, movies, etc.).

    Handles patterns like:
    - Books: "Title (Series Name, #1)", "Title (Series Name, Book 1)"
    - TV Shows: "Title (Series Name, Season 1)", "Title (Series Name, S1)"
    - Movies: "Title (Series Name, Part 1)", "Title (Series Name, Episode 1)"
    - Games: "Title (Series Name, #1)", "Title (Series Name, Part 1)"
    - Video Games (title-embedded): "Dungeon Siege 3", "Final Fantasy XII"

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
            item_num = float(match.group(2))
            if 1 <= item_num <= pattern.max_number:
                return (series_name, item_num)

    # For video games, try title-embedded numbers (e.g., "Dungeon Siege 3",
    # "Final Fantasy XII").  Only video games get this treatment — other
    # types too often have non-series numbers in titles ("2001: A Space
    # Odyssey", "1984").
    if content_type == ContentType.VIDEO_GAME:
        return _extract_series_from_title(title)

    return None


def _extract_from_metadata(
    metadata: dict, content_type: ContentType | None = None
) -> tuple[str, float] | None:
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
    item_num: float | None = None

    if content_type == ContentType.TV_SHOW:
        # For TV shows, look for season number
        for key in ["series_position", "season", "season_number", "season_num"]:
            if key in metadata and metadata[key]:
                try:
                    item_num = float(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue
    elif content_type == ContentType.MOVIE:
        # For movies, look for part/episode number
        for key in [
            "series_position",
            "part",
            "part_number",
            "episode",
            "episode_number",
            "movie_number",
        ]:
            if key in metadata and metadata[key]:
                try:
                    item_num = float(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue
    else:
        # For books and games, look for series number
        for key in [
            "series_position",
            "series_number",
            "series_num",
            "book_number",
            "book_num",
            "part",
            "part_number",
        ]:
            if key in metadata and metadata[key]:
                try:
                    item_num = float(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue

    # ``float()`` accepts "inf"/"nan" where ``int()`` raised; reject non-finite
    # values explicitly so a malformed metadata position cannot poison ordering.
    if (
        series_name
        and item_num is not None
        and math.isfinite(item_num)
        and 1 <= item_num <= 1000
    ):
        return (series_name, item_num)

    return None


def get_series_name_from_metadata(metadata: dict | None) -> str | None:
    """Extract series name from metadata without requiring an item number.

    Used for show-level items that carry a series_name but no season marker.
    Unlike :func:`extract_series_info`, this does not require an item number,
    so it returns a result for show-level entries like ``{"series_name": "The
    Expanse"}`` that lack a ``season`` field.

    Args:
        metadata: Metadata dictionary, or None.

    Returns:
        Series name if found, None otherwise.
    """
    if not metadata:
        return None
    for key in ("series_name", "series", "series_title", "franchise"):
        val = metadata.get(key)
        if val is not None:
            stripped = str(val).strip()
            if stripped:
                return stripped
    return None


def _get_series_info(
    item: ContentItem | None = None, *, title: str | None = None
) -> tuple[str, float] | None:
    """Extract series info from a ContentItem or title string.

    Shared implementation for get_series_name and get_series_item_number.
    """
    if item is not None:
        return extract_series_info(item.title, item.metadata, item.content_type)
    if title is not None:
        return extract_series_info(title)
    return None


def get_series_name(
    item: ContentItem | None = None, *, title: str | None = None
) -> str | None:
    """Get series name from ContentItem or title (checks title and metadata).

    Args:
        item: Optional ContentItem to extract series name from
        title: Optional title string (keyword-only)

    Returns:
        Series name if found, None otherwise
    """
    info = _get_series_info(item, title=title)
    return info[0] if info else None


def get_series_item_number(
    item: ContentItem | None = None, *, title: str | None = None
) -> float | None:
    """Get item number in series from ContentItem or title.

    Args:
        item: Optional ContentItem
        title: Optional title string (keyword-only)

    Returns:
        Item number if found, None otherwise
    """
    info = _get_series_info(item, title=title)
    return info[1] if info else None


def inject_seasons_watched_tracking(
    unconsumed_items: list[ContentItem],
    series_tracking: dict[str, set[float]],
) -> dict[str, set[float]]:
    """Add seasons_watched metadata from unconsumed TV shows to series tracking.

    When a user imports TV shows with specific seasons_watched (e.g., [5, 6]),
    those seasons should be treated as consumed for recommendation purposes
    even though the show-level item is still "unconsumed" in the library.

    Args:
        unconsumed_items: List of unconsumed ContentItem objects (TV shows)
        series_tracking: Existing series tracking dictionary from consumed items

    Returns:
        New dictionary with seasons_watched merged in (does not mutate original)
    """
    merged = dict(series_tracking)

    for item in unconsumed_items:
        if item.content_type != ContentType.TV_SHOW:
            continue

        seasons_watched = item.metadata.get("seasons_watched")
        if not isinstance(seasons_watched, list) or not seasons_watched:
            continue

        show_title = item.title
        if show_title not in merged:
            merged[show_title] = set()
        else:
            merged[show_title] = set(merged[show_title])

        for season_num in seasons_watched:
            if isinstance(season_num, int):
                merged[show_title].add(season_num)

    return merged


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

        # Determine which seasons to skip (already watched)
        seasons_watched_raw = item.metadata.get("seasons_watched")
        watched_set: set[int] = set()
        if isinstance(seasons_watched_raw, list):
            watched_set = {
                season for season in seasons_watched_raw if isinstance(season, int)
            }

        for season_num in range(1, total_seasons + 1):
            if season_num in watched_set:
                continue
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
                    ignored=item.ignored,
                    parent_id=item.id,
                    metadata=season_metadata,
                    source=item.source,
                )
            )

    return expanded


def build_series_tracking(
    consumed_items: list[ContentItem],
) -> dict[str, set[float]]:
    """Build a map of series names to item numbers the user has consumed.

    Works for all content types (books, games, TV shows, movies).

    Args:
        consumed_items: List of consumed ContentItem objects

    Returns:
        Dictionary mapping series names to sets of item numbers
    """
    series_tracking: dict[str, set[float]] = defaultdict(set)

    for item in consumed_items:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
        if series_info:
            series_name, item_num = series_info
            series_tracking[series_name].add(item_num)

    return dict(series_tracking)


def is_first_item_in_series(
    item: ContentItem | None = None, *, title: str | None = None
) -> bool:
    """Check if this is the first item in a series.

    Works for all content types (Book 1, Season 1, Part 1, etc.).

    Args:
        item: Optional ContentItem
        title: Optional title string (keyword-only)

    Returns:
        True if this is item #1 in a series
    """
    info = _get_series_info(item, title=title)
    return info is not None and info[1] == 1


def should_recommend_item(
    item: ContentItem,
    series_tracking: dict[str, set[float]],
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
    - Prequels: consuming item #0 unlocks item #1 as the next entry (this
      falls out of the gap-finding logic rather than being special-cased)

    Item numbers may be fractional — half-numbered novellas such as
    "(The Expanse, #2.5)" sort *after* book ``#2`` and *before* ``#3``, so a
    user who has only read book ``#1`` is not recommended a ``#2.5`` novella
    until they have read ``#2``.

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
    unconsumed_item_nums: set[float] = set()
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
        # User hasn't started this series. Only recommend the first item (#1)
        # or a prequel (#0); a later entry waits until an earlier one in the
        # data is consumed. If unconsumed_items is None we can't verify what
        # exists, so be conservative and don't recommend a later entry.
        if item_num == 1 or item_num == 0:
            return True
        if unconsumed_items is None:
            return False
        # If any earlier entry exists in the unconsumed data, hold this one.
        return not any(num < item_num for num in unconsumed_item_nums)

    # User has started this series. Recommend the item only if it is the
    # earliest entry the user has not yet consumed. The candidate pool is the
    # union of: numbers known to exist in the data (consumed + unconsumed),
    # this item's own number (so direct callers that pass an item absent from
    # ``unconsumed_items`` are still considered), and the virtual sequential
    # slots 1..max+1 (so a missing next book still blocks later entries, and
    # gaps like a skipped season 2 are surfaced before season 7). Fractional
    # novellas naturally slot between their neighbouring integer books.
    #
    # ``int(max_consumed)`` floors fractional positions intentionally: a
    # consumed #2.5 novella still yields integer slots up to the next whole
    # book. ``max_consumed`` is bounded by the 1..1000 range enforced in
    # ``extract_series_info``, so the slot set never grows without bound.
    max_consumed = max(consumed_numbers)
    virtual_slots = {float(slot) for slot in range(1, int(max_consumed) + 2)}
    positions = consumed_numbers | unconsumed_item_nums | {item_num} | virtual_slots
    remaining = sorted(pos for pos in positions if pos not in consumed_numbers)
    return bool(remaining) and item_num == remaining[0]


def find_earliest_recommendable(
    series_name: str,
    series_tracking: dict[str, set[float]],
    unconsumed_items: list[ContentItem],
) -> ContentItem | None:
    """Find the earliest unconsumed item in a series that passes series rules.

    Used by the engine to substitute a later series entry (e.g., FF XII) with
    the earliest playable entry (e.g., FF X) when ``series_in_order`` is
    enabled.

    Args:
        series_name: Name of the series to search.
        series_tracking: Series tracking dictionary (consumed items).
        unconsumed_items: All unconsumed items to search through.

    Returns:
        The earliest unconsumed item in the series that passes
        :func:`should_recommend_item`, or ``None`` if none qualifies.
    """
    # Collect unconsumed items belonging to this series, paired with their number
    series_candidates: list[tuple[float, ContentItem]] = []
    for item in unconsumed_items:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
        if series_info and series_info[0] == series_name:
            series_candidates.append((series_info[1], item))

    if not series_candidates:
        return None

    # Sort by item number (ascending) so earliest comes first
    series_candidates.sort(key=lambda pair: pair[0])

    for _item_number, candidate in series_candidates:
        if should_recommend_item(
            candidate, series_tracking, unconsumed_items=unconsumed_items
        ):
            return candidate

    return None


def is_active_series_continuation(
    item: ContentItem,
    series_tracking: dict[str, set[float]],
    unconsumed_items: list[ContentItem] | None = None,
) -> bool:
    """Return True if *item* is the next entry in an already-started series.

    "Already-started" means the user has consumed at least one entry of the
    series; "next entry" means the item passes :func:`should_recommend_item`.
    The first book of an *unstarted* series and standalone items return False —
    beginning a brand-new series is not a continuation and should not be
    shielded from the variety penalty.

    Used to soften (not remove) the genre-fatigue variety penalty for the book
    a user is actively working towards, so the next entry in a series they are
    mid-way through is not buried beneath unrelated content.

    Args:
        item: Candidate item being scored.
        series_tracking: Series tracking dictionary (consumed items).
        unconsumed_items: Optional unconsumed items for ordering checks.

    Returns:
        True if the item continues a series the user has already started.
    """
    series_info = extract_series_info(item.title, item.metadata, item.content_type)
    if series_info is None:
        return False
    series_name = series_info[0]
    if not series_tracking.get(series_name):
        # Unstarted series (or untracked) — starting fresh is not continuation.
        return False
    return should_recommend_item(item, series_tracking, unconsumed_items)


def strip_series_suffix_from_title(title: str) -> str:
    """Strip trailing parenthetical series info from a title for display.

    Removes patterns like "(Shannara, #2)", "(Series, Book N)".

    Args:
        title: Original title string.

    Returns:
        Title without series suffix.
    """
    cleaned = re.sub(r"\s*\([^)]*#\d+[^)]*\)\s*$", "", title)
    cleaned = re.sub(r"\s*\([^)]*Book\s+\d+[^)]*\)\s*$", "", cleaned, flags=re.I)
    return cleaned.strip()
