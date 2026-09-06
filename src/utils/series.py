from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from enum import Enum
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


#: The one name key and the one position key every writer stores. ``series`` and
#: ``series_index`` are template column headers only — see
#: :mod:`src.models.detail_fields` — and schema 23 folded the stored ones in.
SERIES_NAME_KEY = "series_name"
SERIES_POSITION_KEY = "series_position"

MAX_SERIES_POSITION = 1000


def valid_series_position(position: float) -> bool:
    """Zero is Wikidata's convention for a prequel, and one every reader here
    understands. ``float()`` takes "inf"/"nan", which poison every comparison.
    """
    return math.isfinite(position) and 0 <= position <= MAX_SERIES_POSITION


_POSITION_KEYS_BY_TYPE: dict[ContentType, tuple[str, ...]] = {
    ContentType.TV_SHOW: (
        SERIES_POSITION_KEY,
        "season",
        "season_number",
        "season_num",
    ),
    ContentType.MOVIE: (
        SERIES_POSITION_KEY,
        "part",
        "part_number",
        "episode",
        "episode_number",
    ),
}

_DEFAULT_POSITION_KEYS: tuple[str, ...] = (
    SERIES_POSITION_KEY,
    "series_number",
    "series_num",
    "book_number",
    "book_num",
    "part",
    "part_number",
)


def _stated_series_position(
    metadata: Mapping[str, Any], content_type: ContentType | None = None
) -> float | None:
    keys = (
        _DEFAULT_POSITION_KEYS
        if content_type is None
        else _POSITION_KEYS_BY_TYPE.get(content_type, _DEFAULT_POSITION_KEYS)
    )
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            position = float(value)
        except (ValueError, TypeError):
            continue
        return position if valid_series_position(position) else None
    return None


def _extract_from_metadata(
    metadata: dict[str, Any], content_type: ContentType | None = None
) -> tuple[str, float] | None:
    series_name = get_series_name_from_metadata(metadata)
    if not series_name:
        return None
    position = _stated_series_position(metadata, content_type)
    return (series_name, position) if position is not None else None


class SeriesAuthority(str, Enum):
    """How well founded a series ordinal is, weakest first. A strictly higher
    authority replaces a stored ordinal; an equal one leaves it, so two sources
    of the same standing keep the first answer.
    """

    #: A marker in the title, or a number parsed out of a game's title.
    STATED = "stated"
    #: An external provider stating the ordinal as a fact about the work.
    AUTHORED = "authored"
    #: The operator's own library: a Calibre series index, an import column.
    LIBRARY = "library"
    #: Set by hand in the app.
    MANUAL = "manual"

    def outranks(self, other: SeriesAuthority | None) -> bool:
        order = list(SeriesAuthority)
        return other is None or order.index(self) > order.index(other)


#: Where the ordinal's authority is recorded, beside the ordinal itself.
SERIES_AUTHORITY_KEY = "series_position_authority"

#: Every key :func:`reconcile_series` decides, so no other rule may touch them.
SERIES_RECONCILED_KEYS = frozenset(
    (SERIES_NAME_KEY, SERIES_POSITION_KEY, SERIES_AUTHORITY_KEY)
)


def stored_series_authority(
    metadata: Mapping[str, Any] | None,
) -> SeriesAuthority | None:
    """None when no ordinal is stored. One written before the ladder existed
    reads as ``stated``, so a better-founded source can still correct it.
    """
    if not metadata or get_series_position_from_metadata(metadata) is None:
        return None
    try:
        return SeriesAuthority(str(metadata.get(SERIES_AUTHORITY_KEY)))
    except ValueError:
        return SeriesAuthority.STATED


def reconcile_series(
    existing: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    """Authority alone decides the ordinal, and a matched source's series name
    follows it. A name arriving beside an ordinal too weak to replace anything
    only fills a gap.
    """
    offered = stored_series_authority(incoming)
    replacing = (
        offered
        if offered is not None and offered.outranks(stored_series_authority(existing))
        else None
    )

    fields: dict[str, Any] = {}
    # Narrow on what arrives, broad on what is stored: a provider owns the
    # alias it writes, and one already naming the series is a name to keep.
    name = str(incoming.get(SERIES_NAME_KEY) or "").strip()
    stored_name = get_series_name_from_metadata(existing)
    if name and (stored_name is None or replacing is not None):
        fields[SERIES_NAME_KEY] = name
    if replacing is not None:
        fields[SERIES_POSITION_KEY] = get_series_position_from_metadata(incoming)
        fields[SERIES_AUTHORITY_KEY] = replacing.value
    return fields


def get_series_name_from_metadata(metadata: Mapping[str, Any] | None) -> str | None:
    """The trailing two are read aliases a provider owns and only it writes."""
    if not metadata:
        return None
    for key in (SERIES_NAME_KEY, "series_title", "franchise"):
        val = metadata.get(key)
        if val is not None:
            stripped = str(val).strip()
            if stripped:
                return stripped
    return None


def get_series_position_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> float | None:
    if not metadata:
        return None
    try:
        position = float(metadata[SERIES_POSITION_KEY])
    except (KeyError, ValueError, TypeError):
        return None
    return position if valid_series_position(position) else None


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


def series_entry(item: ContentItem) -> tuple[str, float | None] | None:
    """A name with no position falls through to the title: RAWG names a
    franchise for every game, and 'Halo 3' states its own number.
    """
    name = get_series_name_from_metadata(item.metadata)
    position = _stated_series_position(item.metadata, item.content_type)
    if name is not None and position is not None:
        return name, position
    parsed = extract_series_info(item.title, item.metadata, item.content_type)
    if parsed is not None:
        return parsed
    return None if name is None else (name, None)


#: No ``year_published``: an edition's year is not the work's (detail_fields.py).
_RELEASE_YEAR_KEYS: tuple[str, ...] = ("release_year", "year")


def _release_year(item: ContentItem) -> int | None:
    for key in _RELEASE_YEAR_KEYS:
        try:
            return int(item.metadata[key])
        except (KeyError, ValueError, TypeError):
            continue
    return None


class _Placed(NamedTuple):
    """Keyed by id, so a collection holding both Lion Kings ranks them apart."""

    key: str
    year: int | None
    title: str


def _rank_key(placed: _Placed) -> tuple[bool, int, str]:
    return (placed.year is None, placed.year or 0, placed.title)


def _dense_ranks(placed: Iterable[_Placed]) -> dict[str, float]:
    """An undated entry ranks last, by title — which is every book in a series."""
    ordered = sorted(placed, key=_rank_key)
    return {entry.key: float(rank) for rank, entry in enumerate(ordered, start=1)}


def _placement_key(item: ContentItem) -> str:
    return item.id or item.title


class SeriesOrder:
    """One entry stating no ordinal drops its whole series to release-year ranks:
    ranking a date against an ordinal is the disorder this prevents (#195). Ranks
    start at 1, the scale an authored series already reaches every rule on.
    """

    def __init__(self, items: Iterable[ContentItem] = ()) -> None:
        stated: dict[str, list[float | None]] = defaultdict(list)
        placed: dict[str, dict[str, _Placed]] = defaultdict(dict)
        for item in items:
            entry = series_entry(item)
            if entry is None:
                continue
            name, ordinal = entry
            stated[name].append(ordinal)
            key = _placement_key(item)
            placed[name][key] = _Placed(key, _release_year(item), item.title)

        self._ranks: dict[str, dict[str, float]] = {
            name: _dense_ranks(placed[name].values())
            for name, ordinals in stated.items()
            if None in ordinals
        }

    def locate(self, item: ContentItem) -> tuple[str, float] | None:
        entry = series_entry(item)
        if entry is None:
            return None
        name, ordinal = entry
        ranks = self._ranks.get(name)
        if ranks is None:
            return (name, ordinal) if ordinal is not None else None
        rank = ranks.get(_placement_key(item))
        return None if rank is None else (name, rank)


def _order_over(
    series_order: SeriesOrder | None, items: Iterable[ContentItem]
) -> SeriesOrder:
    return SeriesOrder(items) if series_order is None else series_order


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


_SOURCE_REPORTED_SEASON_COUNTS: tuple[str, ...] = (
    "season_episode_counts",
    "plex_season_episode_counts",
    "episodes_watched_by_season",
)


def _season_number(key: Any) -> int | None:
    try:
        season = int(key)
    except (TypeError, ValueError):
        return None
    return season if 1 <= season <= MAX_SEASONS else None


def _season_counts(raw: Any) -> dict[int, int]:
    if not isinstance(raw, dict):
        return {}
    return {
        season: value
        for key, value in raw.items()
        if (season := _season_number(key)) is not None and isinstance(value, int)
    }


def _largest_season_counts(existing: Any, incoming: Any) -> dict[str, int] | None:
    if not isinstance(existing, dict) and not isinstance(incoming, dict):
        return None
    merged = _season_counts(existing)
    for season, count in _season_counts(incoming).items():
        merged[season] = max(merged.get(season, 0), count)
    return {str(season): count for season, count in sorted(merged.items())}


def seasons_finished(episodes_watched: Any, *season_sizes: Any) -> set[int]:
    """The largest count a source states is the season's size: Sonarr's misses
    the episodes it does not monitor and TMDB's counts episodes yet to air, and
    only the low reading can tick a season the operator has not finished.
    """
    sizes: dict[int, int] = {}
    for stated in season_sizes:
        for season, size in _season_counts(stated).items():
            sizes[season] = max(sizes.get(season, 0), size)

    return {
        season
        for season, watched in _season_counts(episodes_watched).items()
        if sizes.get(season, 0) >= 1 and watched >= sizes[season]
    }


def reconcile_seasons(
    existing: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in _SOURCE_REPORTED_SEASON_COUNTS:
        counts = _largest_season_counts(existing.get(key), incoming.get(key))
        if counts is not None:
            fields[key] = counts

    reported = merge_seasons_watched(
        existing.get("seasons_watched"), incoming.get("seasons_watched")
    )
    finished = seasons_finished(
        fields.get("episodes_watched_by_season"),
        fields.get("season_episode_counts"),
        fields.get("plex_season_episode_counts"),
    )
    watched = set(reported or ()) | finished
    if reported is not None or watched:
        fields["seasons_watched"] = sorted(watched)
    return fields


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
            # The show's ordinal places it in a franchise, not this season in
            # the show, and it is read ahead of ``season``.
            season_metadata.pop(SERIES_POSITION_KEY, None)
            season_metadata.pop(SERIES_AUTHORITY_KEY, None)
            season_metadata[SERIES_NAME_KEY] = show_title
            season_metadata["season"] = season_num
            season_metadata["season_number"] = season_num

            # Copied from the show rather than rebuilt field by field: a season
            # carries everything the show has, so a field added to ContentItem
            # reaches recommendations without a line here.
            expanded.append(
                item.model_copy(
                    update={
                        "id": season_id,
                        "title": season_title,
                        "parent_id": item.id,
                        "metadata": season_metadata,
                    }
                )
            )

    return expanded


def build_series_tracking(
    items: list[ContentItem],
    series_order: SeriesOrder | None = None,
) -> dict[str, set[float]]:
    order = _order_over(series_order, items)
    series_tracking: dict[str, set[float]] = defaultdict(set)

    for item in items:
        located = order.locate(item)
        if located is not None:
            series_tracking[located[0]].add(located[1])

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
    series_order: SeriesOrder | None = None,
) -> bool:
    order = _order_over(series_order, [item, *(unconsumed_items or ())])
    located = order.locate(item)
    if located is None:
        return True

    series_name, item_num = located
    consumed_numbers = series_tracking.get(series_name, set())

    unconsumed_item_nums: set[float] = set()
    for unconsumed in unconsumed_items or ():
        other = order.locate(unconsumed)
        if other is not None and other[0] == series_name:
            unconsumed_item_nums.add(other[1])

    if not consumed_numbers:
        if item_num == 1 or item_num == 0:
            return True
        if unconsumed_items is None:
            return False
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
    series_order: SeriesOrder | None = None,
) -> ContentItem | None:
    """Used by the engine to substitute a later series entry (e.g., FF XII) with
    the earliest playable entry (e.g., FF X) when ``series_in_order`` is enabled.
    """
    order = _order_over(series_order, unconsumed_items)
    series_candidates: list[tuple[float, ContentItem]] = []
    for item in unconsumed_items:
        located = order.locate(item)
        if located is not None and located[0] == series_name:
            series_candidates.append((located[1], item))

    if not series_candidates:
        return None

    series_candidates.sort(key=lambda pair: pair[0])

    for _item_number, candidate in series_candidates:
        if should_recommend_item(
            candidate,
            series_tracking,
            unconsumed_items=unconsumed_items,
            series_order=order,
        ):
            return candidate

    return None


def is_active_series_continuation(
    item: ContentItem,
    series_tracking: dict[str, set[float]],
    unconsumed_items: list[ContentItem] | None = None,
    series_order: SeriesOrder | None = None,
) -> bool:
    """The first book of an *unstarted* series and standalone items return False —
    beginning a brand-new series is not a continuation and should not be
    shielded from the variety penalty.
    """
    order = _order_over(series_order, [item, *(unconsumed_items or ())])
    located = order.locate(item)
    if located is None:
        return False
    if not series_tracking.get(located[0]):
        return False
    return should_recommend_item(item, series_tracking, unconsumed_items, order)


_SERIES_MARKER = re.compile(
    r"\s*\(([^()]*?)(?:(?:,\s*|\s+)#\s*|,\s*Book\s+)"
    r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*\d+(?:\.\d+)?)?\)",
    re.IGNORECASE,
)


class SeriesFields(TypedDict, total=False):
    series_name: str
    series_position: float
    series_position_authority: str


def split_series_from_title(title: str) -> tuple[str, SeriesFields]:
    """The work's own title, and the series a marker in it states."""
    match = _SERIES_MARKER.search(title)
    if match is None:
        return title, {}
    bare = f"{title[: match.start()].strip()} {title[match.end() :].strip()}".strip()
    series = match.group(1).strip()
    if not bare or not series:
        return title, {}
    return bare, {
        "series_name": series,
        "series_position": float(match.group(2)),
        "series_position_authority": SeriesAuthority.STATED.value,
    }
