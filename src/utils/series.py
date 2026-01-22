"""Series detection and parsing utilities for all content types."""

import re
from typing import Optional, Tuple, Dict, Set, List
from collections import defaultdict

from src.models.content import ContentItem


def extract_series_info(title: str) -> Optional[Tuple[str, int]]:
    """Extract series name and item number from title.

    Works for all content types (books, games, TV shows, movies, etc.).

    Handles patterns like:
    - "Title (Series Name, #1)"
    - "Title (Series Name #1)"
    - "Title (Series Name, Book 1)"
    - "Title (Series Name 1)"

    Args:
        title: Content title

    Returns:
        Tuple of (series_name, item_number) if found, None otherwise
    """
    # Pattern 1: (Series Name, #N) or (Series Name #N)
    pattern1 = r"\(([^,]+?)(?:,\s*)?#\s*(\d+)\)"
    match = re.search(pattern1, title)
    if match:
        series_name = match.group(1).strip()
        book_num = int(match.group(2))
        return (series_name, book_num)

    # Pattern 2: (Series Name, Book N)
    pattern2 = r"\(([^,]+?),\s*Book\s+(\d+)\)"
    match = re.search(pattern2, title, re.IGNORECASE)
    if match:
        series_name = match.group(1).strip()
        book_num = int(match.group(2))
        return (series_name, book_num)

    # Pattern 3: (Series Name N) - less common
    pattern3 = r"\(([^,]+?)\s+(\d+)\)"
    match = re.search(pattern3, title)
    if match:
        # Check if the number is at the end and looks like a book number
        series_name = match.group(1).strip()
        book_num = int(match.group(2))
        # Only accept if it's a reasonable book number (1-100)
        if 1 <= book_num <= 100:
            return (series_name, book_num)

    return None


def get_series_name(title: str) -> Optional[str]:
    """Get series name from title if it's part of a series.

    Args:
        title: Content title

    Returns:
        Series name if found, None otherwise
    """
    series_info = extract_series_info(title)
    return series_info[0] if series_info else None


def get_series_item_number(title: str) -> Optional[int]:
    """Get item number in series from title.

    Args:
        title: Content title

    Returns:
        Item number if found, None otherwise
    """
    series_info = extract_series_info(title)
    return series_info[1] if series_info else None


# Backward compatibility alias
get_series_book_number = get_series_item_number


def build_series_tracking(consumed_items: list[ContentItem]) -> Dict[str, Set[int]]:
    """Build a map of series names to item numbers the user has consumed.

    Works for all content types.

    Args:
        consumed_items: List of consumed ContentItem objects

    Returns:
        Dictionary mapping series names to sets of item numbers
    """
    series_tracking: Dict[str, Set[int]] = defaultdict(set)

    for item in consumed_items:
        series_info = extract_series_info(item.title)
        if series_info:
            series_name, item_num = series_info
            series_tracking[series_name].add(item_num)

    return dict(series_tracking)


def is_series_started(series_name: str, series_tracking: Dict[str, Set[int]]) -> bool:
    """Check if user has started a series.

    Args:
        series_name: Series name
        series_tracking: Series tracking dictionary

    Returns:
        True if user has at least one item from the series
    """
    return series_name in series_tracking and len(series_tracking[series_name]) > 0


def is_first_item_in_series(title: str) -> bool:
    """Check if this is the first item in a series.

    Args:
        title: Content title

    Returns:
        True if this is item #1 in a series
    """
    series_info = extract_series_info(title)
    return series_info is not None and series_info[1] == 1


# Backward compatibility alias
is_first_book_in_series = is_first_item_in_series


def should_recommend_item(
    item: ContentItem,
    series_tracking: Dict[str, Set[int]],
    unconsumed_items: Optional[List[ContentItem]] = None,
) -> bool:
    """Determine if an item should be recommended based on series rules.

    Works for all content types. Rules:
    - If not in a series: recommend
    - If first item (#1) in unstarted series: recommend
    - If user has completed all previous items: recommend
    - If previous items exist in unconsumed data but aren't completed: don't recommend
    - If previous items don't exist in unconsumed data: recommend (assume they don't exist)
    - Special case: If user has consumed item #0 (prequel), recommend item #1

    Args:
        item: ContentItem to check
        series_tracking: Series tracking dictionary (consumed items)
        unconsumed_items: Optional list of unconsumed items to check if previous
                         items exist in the data

    Returns:
        True if item should be recommended
    """
    series_info = extract_series_info(item.title)
    if not series_info:
        # Not in a series, always recommend
        return True

    series_name, item_num = series_info
    consumed_items = series_tracking.get(series_name, set())

    # Build set of unconsumed item numbers for this series
    unconsumed_item_nums: Set[int] = set()
    if unconsumed_items:
        for unconsumed in unconsumed_items:
            unconsumed_series_info = extract_series_info(unconsumed.title)
            if unconsumed_series_info:
                unconsumed_series_name, unconsumed_item_num = unconsumed_series_info
                if unconsumed_series_name == series_name:
                    unconsumed_item_nums.add(unconsumed_item_num)

    if not consumed_items:
        # User hasn't started this series
        # Only recommend if it's the first item (#1) or prequel (#0)
        if item_num == 1 or item_num == 0:
            return True
        # If it's a later item, check if previous items exist in unconsumed data
        # If unconsumed_items is None, be conservative and don't recommend
        # (we can't verify if previous items exist)
        if unconsumed_items is None:
            return False
        # If previous items exist in unconsumed data but aren't completed, don't recommend
        for prev_num in range(1, item_num):
            if prev_num in unconsumed_item_nums:
                # Previous item exists in unconsumed data but isn't completed
                return False
        # Previous items don't exist in unconsumed data, so recommend
        return True
    else:
        # User has started this series
        # Find the highest item number they've consumed
        max_consumed = max(consumed_items)

        # Special case: if they've consumed #0, recommend #1
        if max_consumed == 0 and item_num == 1:
            return True

        # Check if user has completed all previous items
        # Need to check all items from 1 to (item_num - 1)
        for prev_num in range(1, item_num):
            if prev_num not in consumed_items:
                # Previous item not consumed - check if it exists in unconsumed data
                if prev_num in unconsumed_item_nums:
                    # Previous item exists but isn't completed - don't recommend
                    return False
                # Previous item doesn't exist in unconsumed data - assume OK
                # (might be a gap in the data or user can start anywhere)

        # User has completed all previous items (or they don't exist in data)
        # Recommend if it's the next item
        return item_num == max_consumed + 1


# Backward compatibility alias
def should_recommend_book(
    item: ContentItem, series_tracking: Dict[str, Set[int]]
) -> bool:
    """Determine if a book should be recommended based on series rules.

    Deprecated: Use should_recommend_item instead for all content types.

    Args:
        item: ContentItem to check
        series_tracking: Series tracking dictionary

    Returns:
        True if book should be recommended
    """
    return should_recommend_item(item, series_tracking, unconsumed_items=None)
