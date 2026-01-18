"""Series detection and parsing utilities."""

import re
from typing import Optional, Tuple, Dict, Set
from collections import defaultdict

from src.models.content import ContentItem


def extract_series_info(title: str) -> Optional[Tuple[str, int]]:
    """Extract series name and book number from title.

    Handles patterns like:
    - "Title (Series Name, #1)"
    - "Title (Series Name #1)"
    - "Title (Series Name, Book 1)"
    - "Title (Series Name 1)"

    Args:
        title: Book title

    Returns:
        Tuple of (series_name, book_number) if found, None otherwise
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
        title: Book title

    Returns:
        Series name if found, None otherwise
    """
    series_info = extract_series_info(title)
    return series_info[0] if series_info else None


def get_series_book_number(title: str) -> Optional[int]:
    """Get book number in series from title.

    Args:
        title: Book title

    Returns:
        Book number if found, None otherwise
    """
    series_info = extract_series_info(title)
    return series_info[1] if series_info else None


def build_series_tracking(consumed_items: list[ContentItem]) -> Dict[str, Set[int]]:
    """Build a map of series names to book numbers the user has consumed.

    Args:
        consumed_items: List of consumed ContentItem objects

    Returns:
        Dictionary mapping series names to sets of book numbers
    """
    series_tracking: Dict[str, Set[int]] = defaultdict(set)

    for item in consumed_items:
        series_info = extract_series_info(item.title)
        if series_info:
            series_name, book_num = series_info
            series_tracking[series_name].add(book_num)

    return dict(series_tracking)


def is_series_started(series_name: str, series_tracking: Dict[str, Set[int]]) -> bool:
    """Check if user has started a series.

    Args:
        series_name: Series name
        series_tracking: Series tracking dictionary

    Returns:
        True if user has at least one book from the series
    """
    return series_name in series_tracking and len(series_tracking[series_name]) > 0


def is_first_book_in_series(title: str) -> bool:
    """Check if this is the first book in a series.

    Args:
        title: Book title

    Returns:
        True if this is book #1 in a series
    """
    series_info = extract_series_info(title)
    return series_info is not None and series_info[1] == 1


def should_recommend_book(
    item: ContentItem, series_tracking: Dict[str, Set[int]]
) -> bool:
    """Determine if a book should be recommended based on series rules.

    Rules:
    - If not in a series: recommend
    - If first book (#1) in unstarted series: recommend
    - If next book in started series: recommend
    - If later book in unstarted series: don't recommend
    - Special case: If user has read book #0 (prequel), recommend book #1

    Args:
        item: ContentItem to check
        series_tracking: Series tracking dictionary

    Returns:
        True if book should be recommended
    """
    series_info = extract_series_info(item.title)
    if not series_info:
        # Not in a series, always recommend
        return True

    series_name, book_num = series_info
    user_books = series_tracking.get(series_name, set())

    if not user_books:
        # User hasn't started this series
        # Only recommend if it's the first book (#1) or prequel (#0)
        return book_num == 1 or book_num == 0
    else:
        # User has started this series
        # Find the highest book number they've read
        max_read = max(user_books)
        # Recommend if it's the next book they haven't read
        # Special case: if they've read #0, recommend #1
        if max_read == 0 and book_num == 1:
            return True
        return book_num == max_read + 1
