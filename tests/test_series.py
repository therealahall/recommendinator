"""Tests for series detection and filtering utilities."""

import pytest

from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.utils.series import (
    extract_series_info,
    get_series_name,
    get_series_book_number,
    build_series_tracking,
    is_first_book_in_series,
    should_recommend_book,
)


def test_extract_series_info():
    """Test series information extraction from titles."""
    # Pattern 1: (Series Name, #N)
    assert extract_series_info("Book Title (The Witcher, #4)") == ("The Witcher", 4)
    assert extract_series_info("Book (Series, #1)") == ("Series", 1)

    # Pattern 2: (Series Name #N)
    assert extract_series_info("Book (Series #2)") == ("Series", 2)

    # Pattern 3: (Series Name, Book N)
    assert extract_series_info("Book (Series, Book 3)") == ("Series", 3)

    # No series
    assert extract_series_info("Standalone Book") is None
    assert extract_series_info("Book (Not a Series)") is None


def test_get_series_name():
    """Test getting series name from title."""
    assert get_series_name("Book (The Witcher, #4)") == "The Witcher"
    assert get_series_name("Standalone Book") is None


def test_get_series_book_number():
    """Test getting book number from title."""
    assert get_series_book_number("Book (The Witcher, #4)") == 4
    assert get_series_book_number("Standalone Book") is None


def test_build_series_tracking():
    """Test building series tracking from consumed items."""
    items = [
        ContentItem(
            id="1",
            title="Book 1 (Series A, #1)",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="2",
            title="Book 2 (Series A, #2)",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
        ContentItem(
            id="3",
            title="Book 3 (Series B, #1)",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="4",
            title="Standalone Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
    ]

    tracking = build_series_tracking(items)
    assert "Series A" in tracking
    assert tracking["Series A"] == {1, 2}
    assert "Series B" in tracking
    assert tracking["Series B"] == {1}
    assert "Standalone Book" not in tracking


def test_is_first_book_in_series():
    """Test checking if book is first in series."""
    assert is_first_book_in_series("Book (Series, #1)") is True
    assert is_first_book_in_series("Book (Series, #2)") is False
    assert is_first_book_in_series("Standalone Book") is False


def test_should_recommend_book_not_in_series():
    """Test recommendation for books not in a series."""
    item = ContentItem(
        id="1",
        title="Standalone Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_book(item, {}) is True


def test_should_recommend_first_book_unstarted_series():
    """Test recommendation for first book in unstarted series."""
    item = ContentItem(
        id="1",
        title="Book (New Series, #1)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_book(item, {}) is True


def test_should_not_recommend_later_book_unstarted_series():
    """Test that later books in unstarted series are not recommended."""
    item = ContentItem(
        id="1",
        title="Book (New Series, #4)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    assert should_recommend_book(item, {}) is False


def test_should_recommend_next_book_started_series():
    """Test recommendation for next book in started series."""
    item = ContentItem(
        id="1",
        title="Book (Series A, #3)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    # User has read books 1 and 2
    series_tracking = {"Series A": {1, 2}}
    assert should_recommend_book(item, series_tracking) is True


def test_should_not_recommend_skipped_book_started_series():
    """Test that skipping ahead in a series is not recommended."""
    item = ContentItem(
        id="1",
        title="Book (Series A, #5)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    # User has read books 1 and 2, but not 3 or 4
    series_tracking = {"Series A": {1, 2}}
    assert should_recommend_book(item, series_tracking) is False


def test_should_recommend_book_zero_prequel():
    """Test recommendation when user has read book #0 (prequel)."""
    item = ContentItem(
        id="1",
        title="Book (Series A, #1)",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    # User has read book #0 (prequel)
    series_tracking = {"Series A": {0}}
    assert should_recommend_book(item, series_tracking) is True
