import math
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any, NamedTuple, TypedDict

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.dates import local_date_from_iso_timestamp

MAX_SEASONS = 200


class _SeriesPattern(NamedTuple):
    regex: re.Pattern[str]
    max_number: int


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


# The series name must start with a letter to avoid matching titles like
# "1942" or "2048".  The series-name capture uses ``.*?`` (lazy) so it
# can include colons/dashes (e.g., "Batman: Arkham Knight 2").
_TITLE_ARABIC_PATTERN: re.Pattern[str] = re.compile(
    r"^([A-Za-z].*?)\s+(\d+)(?:[\s:—\-+/].+)?$"
)

# Uses ``[IVXLCDM]+`` instead of a strict structural regex so that
# standalone V (5), X (10), L (50), C (100) are accepted.  Validation
# happens downstream via ``_roman_to_int()`` + range check (1-100).
_TITLE_ROMAN_PATTERN: re.Pattern[str] = re.compile(
    r"^([A-Za-z].*?)\s+([IVXLCDM]+)(?:[\s:—\-+/].+)?$"
)


def _extract_series_from_title(title: str) -> tuple[str, float] | None:
    # Try Arabic numerals first (more common). Title-embedded game numbers are
    # whole numbers, but return a float to match the series-number type used
    # everywhere else (fractional novella positions like #2.5).
    match = _TITLE_ARABIC_PATTERN.match(title.strip())
    if match:
        series_name = match.group(1).strip()
        number = int(match.group(2))
        if 1 <= number <= 100 and len(series_name) >= 2:
            return (series_name, float(number))

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
    metadata: dict[str, Any] | None = None,
    content_type: ContentType | None = None,
) -> tuple[str, float] | None:
    if metadata:
        series_info = _extract_from_metadata(metadata, content_type)
        if series_info:
            return series_info

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
    metadata: dict[str, Any], content_type: ContentType | None = None
) -> tuple[str, float] | None:
    series_name = None
    for key in ["series_name", "series", "series_title", "franchise"]:
        if key in metadata and metadata[key]:
            series_name = str(metadata[key]).strip()
            break

    if not series_name:
        return None

    item_num: float | None = None

    if content_type == ContentType.TV_SHOW:
        for key in ["series_position", "season", "season_number", "season_num"]:
            if key in metadata and metadata[key]:
                try:
                    item_num = float(metadata[key])
                    break
                except (ValueError, TypeError):
                    continue
    elif content_type == ContentType.MOVIE:
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
        for key in [
            "series_position",
            "series_number",
            "series_num",
            "series_index",
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


def get_series_name_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    for key in ("series_name", "series", "series_title", "franchise"):
        val = metadata.get(key)
        if val is not None:
            stripped = str(val).strip()
            if stripped:
                return stripped
    return None


def get_series_position_from_metadata(metadata: dict[str, Any] | None) -> float | None:
    if not metadata:
        return None
    for key in ("series_position", "series_index"):
        try:
            position = float(metadata[key])
        except (KeyError, ValueError, TypeError):
            continue
        if math.isfinite(position):
            return position
    return None


def _get_series_info(
    item: ContentItem | None = None, *, title: str | None = None
) -> tuple[str, float] | None:
    if item is not None:
        return extract_series_info(item.title, item.metadata, item.content_type)
    if title is not None:
        return extract_series_info(title)
    return None


def get_series_name(
    item: ContentItem | None = None, *, title: str | None = None
) -> str | None:
    info = _get_series_info(item, title=title)
    return info[0] if info else None


def get_series_item_number(
    item: ContentItem | None = None, *, title: str | None = None
) -> float | None:
    info = _get_series_info(item, title=title)
    return info[1] if info else None


def inject_seasons_watched_tracking(
    unconsumed_items: list[ContentItem],
    series_tracking: dict[str, set[float]],
) -> dict[str, set[float]]:
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
            if isinstance(season_num, int) and 1 <= season_num <= MAX_SEASONS:
                merged[show_title].add(season_num)

    return merged


def all_seasons_watched(
    seasons_watched: Sequence[int] | None, total_seasons: int | None
) -> bool:
    if not seasons_watched or not total_seasons or total_seasons < 1:
        return False
    # Deduplicated, and int-filtered because a stored list arrives from a JSON
    # blob: `--seasons-watched 1,1,1` must not finish a three-season show.
    return len({s for s in seasons_watched if isinstance(s, int)}) >= total_seasons


def status_for_seasons_watched(
    seasons_watched: Sequence[int] | None, total_seasons: int | None
) -> ConsumptionStatus:
    if not seasons_watched:
        return ConsumptionStatus.UNREAD
    if all_seasons_watched(seasons_watched, total_seasons):
        return ConsumptionStatus.COMPLETED
    return ConsumptionStatus.CURRENTLY_CONSUMING


def seasons_watched_for_completed(total_seasons: int | None) -> list[int] | None:
    if not total_seasons or total_seasons < 1:
        return None
    return list(range(1, min(total_seasons, MAX_SEASONS) + 1))


def merge_seasons_watched(existing: Any, incoming: Any) -> list[int] | None:
    """A sync may add a season, never remove one: manual check-offs share the list."""
    sides = [side for side in (existing, incoming) if isinstance(side, list)]
    if not sides:
        return None
    return sorted(
        {season for side in sides for season in side if isinstance(season, int)}
    )


def latest_season_watched_date(item: ContentItem) -> date | None:
    """The stored timestamps are UTC instants, so each is narrowed to the
    host's local calendar day (see ``local_date_from_iso_timestamp``) rather
    than the UTC one.
    """
    dates = item.metadata.get("seasons_watched_dates")
    if not isinstance(dates, dict) or not dates:
        return None
    parsed = [
        local_day
        for value in dates.values()
        if (local_day := local_date_from_iso_timestamp(value)) is not None
    ]
    return max(parsed) if parsed else None


def expand_tv_shows_to_seasons(items: list[ContentItem]) -> list[ContentItem]:
    """Library stays at show level; this expansion is for recommendation scoring
    only.
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

        # Cap the expansion: a malformed or hostile ``total_seasons`` must not
        # allocate an unbounded number of season-level items.
        total_seasons = min(total_seasons, MAX_SEASONS)

        base_id = item.id or ""
        show_title = item.title

        seasons_watched_raw = item.metadata.get("seasons_watched")
        watched_set: set[int] = set()
        if isinstance(seasons_watched_raw, list):
            watched_set = {
                season
                for season in seasons_watched_raw
                if isinstance(season, int) and 1 <= season <= MAX_SEASONS
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
                    db_id=item.db_id,
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
    items: list[ContentItem],
) -> dict[str, set[float]]:
    series_tracking: dict[str, set[float]] = defaultdict(set)

    for item in items:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
        if series_info:
            series_name, item_num = series_info
            series_tracking[series_name].add(item_num)

    return dict(series_tracking)


def is_first_item_in_series(
    item: ContentItem | None = None, *, title: str | None = None
) -> bool:
    info = _get_series_info(item, title=title)
    return info is not None and info[1] == 1


def is_next_after_consumed(
    item_number: float,
    consumed_numbers: set[float],
    known_positions: set[float],
) -> bool:
    max_consumed = max(consumed_numbers)
    if item_number <= max_consumed:
        return False

    ahead = {pos for pos in known_positions if pos > max_consumed}
    ahead.add(float(int(max_consumed) + 1))
    ahead.add(item_number)
    return item_number == min(ahead)


def should_recommend_item(
    item: ContentItem,
    series_tracking: dict[str, set[float]],
    unconsumed_items: list[ContentItem] | None = None,
) -> bool:
    series_info = extract_series_info(item.title, item.metadata, item.content_type)
    if not series_info:
        return True

    series_name, item_num = series_info
    consumed_numbers = series_tracking.get(series_name, set())

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
        # data is consumed.
        if item_num == 1 or item_num == 0:
            return True
        if unconsumed_items is None:
            return False
        # If any earlier entry exists in the unconsumed data, hold this one.
        return not any(num < item_num for num in unconsumed_item_nums)

    # ``max_consumed`` is bounded — series positions are capped at 1000
    # in ``extract_series_info`` and injected TV seasons at ``MAX_SEASONS`` in
    # ``inject_seasons_watched_tracking`` — so the slot set never grows without bound.
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
    """Used by the engine to substitute a later series entry (e.g., FF XII) with
    the earliest playable entry (e.g., FF X) when ``series_in_order`` is enabled.
    """
    series_candidates: list[tuple[float, ContentItem]] = []
    for item in unconsumed_items:
        series_info = extract_series_info(item.title, item.metadata, item.content_type)
        if series_info and series_info[0] == series_name:
            series_candidates.append((series_info[1], item))

    if not series_candidates:
        return None

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
    """The first book of an *unstarted* series and standalone items return False —
    beginning a brand-new series is not a continuation and should not be
    shielded from the variety penalty.
    """
    series_info = extract_series_info(item.title, item.metadata, item.content_type)
    if series_info is None:
        return False
    series_name = series_info[0]
    if not series_tracking.get(series_name):
        return False
    return should_recommend_item(item, series_tracking, unconsumed_items)


_SERIES_MARKER = re.compile(
    r"\s*\(([^()]*?)(?:(?:,\s*|\s+)#\s*|,\s*Book\s+)"
    r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*\d+(?:\.\d+)?)?\)",
    re.IGNORECASE,
)


class SeriesFields(TypedDict, total=False):
    series: str
    series_index: float


def split_series_from_title(title: str) -> tuple[str, SeriesFields]:
    """The work's own title, and the series a marker in it states."""
    match = _SERIES_MARKER.search(title)
    if match is None:
        return title, {}
    bare = f"{title[: match.start()].strip()} {title[match.end() :].strip()}".strip()
    series = match.group(1).strip()
    if not bare or not series:
        return title, {}
    return bare, {"series": series, "series_index": float(match.group(2))}


def strip_series_suffix_from_title(title: str) -> str:
    cleaned = re.sub(r"\s*\([^)]*#\d+[^)]*\)\s*$", "", title)
    cleaned = re.sub(r"\s*\([^)]*Book\s+\d+[^)]*\)\s*$", "", cleaned, flags=re.I)
    return cleaned.strip()
