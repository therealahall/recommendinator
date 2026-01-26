"""Tests for SQLite database manager."""

from datetime import date
from pathlib import Path

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import SQLiteDB


@pytest.fixture
def temp_db(tmp_path: Path) -> SQLiteDB:
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    return SQLiteDB(db_path)


def test_save_and_get_content_item(temp_db: SQLiteDB) -> None:
    """Test saving and retrieving a content item."""
    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        review="Great book!",
        date_completed=date(2025, 1, 15),
        metadata={"pages": 300},
    )

    db_id = temp_db.save_content_item(item)
    assert db_id > 0

    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"
    assert retrieved.author == "Test Author"
    assert retrieved.rating == 4
    assert retrieved.status == ConsumptionStatus.COMPLETED
    assert retrieved.date_completed == date(2025, 1, 15)
    assert retrieved.metadata == {"pages": 300}


def test_update_content_item(temp_db: SQLiteDB) -> None:
    """Test updating an existing content item."""
    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_db.save_content_item(item)

    # Update the item
    item.rating = 5
    item.status = ConsumptionStatus.COMPLETED
    item.review = "Updated review"

    temp_db.save_content_item(item)

    retrieved = temp_db.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.rating == 5
    assert retrieved.status == ConsumptionStatus.COMPLETED
    assert retrieved.review == "Updated review"


def test_get_content_items_with_filters(temp_db: SQLiteDB) -> None:
    """Test getting content items with filters."""
    # Create test items
    items = [
        ContentItem(
            id=f"book_{i}",
            title=f"Book {i}",
            author="Author",
            content_type=ContentType.BOOK,
            status=(
                ConsumptionStatus.COMPLETED if i % 2 == 0 else ConsumptionStatus.UNREAD
            ),
            rating=4 if i % 2 == 0 else None,
        )
        for i in range(5)
    ]

    for item in items:
        temp_db.save_content_item(item)

    # Test filters
    completed = temp_db.get_content_items(status=ConsumptionStatus.COMPLETED)
    assert len(completed) == 3

    unread = temp_db.get_content_items(status=ConsumptionStatus.UNREAD)
    assert len(unread) == 2

    high_rated = temp_db.get_content_items(min_rating=4)
    assert len(high_rated) == 3

    books = temp_db.get_content_items(content_type=ContentType.BOOK)
    assert len(books) == 5


def test_get_unconsumed_items(temp_db: SQLiteDB) -> None:
    """Test getting unconsumed items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD if i < 3 else ConsumptionStatus.COMPLETED,
        )
        for i in range(5)
    ]

    for item in items:
        temp_db.save_content_item(item)

    unconsumed = temp_db.get_unconsumed_items()
    assert len(unconsumed) == 3
    assert all(item.status == ConsumptionStatus.UNREAD for item in unconsumed)


def test_get_completed_items(temp_db: SQLiteDB) -> None:
    """Test getting completed items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3 + i,  # Ratings 3, 4, 5
        )
        for i in range(3)
    ]

    for item in items:
        temp_db.save_content_item(item)

    completed = temp_db.get_completed_items(min_rating=4)
    assert len(completed) == 2
    assert all(item.rating >= 4 for item in completed)


def test_delete_content_item(temp_db: SQLiteDB) -> None:
    """Test deleting a content item."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    db_id = temp_db.save_content_item(item)
    assert temp_db.get_content_item(db_id) is not None

    deleted = temp_db.delete_content_item(db_id)
    assert deleted is True

    assert temp_db.get_content_item(db_id) is None


def test_count_items(temp_db: SQLiteDB) -> None:
    """Test counting items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK if i < 3 else ContentType.MOVIE,
            status=(
                ConsumptionStatus.COMPLETED if i % 2 == 0 else ConsumptionStatus.UNREAD
            ),
        )
        for i in range(5)
    ]

    for item in items:
        temp_db.save_content_item(item)

    assert temp_db.count_items() == 5
    assert temp_db.count_items(content_type=ContentType.BOOK) == 3
    assert temp_db.count_items(status=ConsumptionStatus.COMPLETED) == 3
