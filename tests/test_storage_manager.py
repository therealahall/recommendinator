"""Tests for unified storage manager."""

from pathlib import Path

import pytest

from src.storage.manager import StorageManager
from src.models.content import ContentItem, ContentType, ConsumptionStatus


@pytest.fixture
def temp_storage_manager(tmp_path: Path) -> StorageManager:
    """Create a temporary storage manager for testing."""
    sqlite_path = tmp_path / "test.db"
    vector_db_path = tmp_path / "vector_db"
    return StorageManager(sqlite_path, vector_db_path)


def test_save_content_item_without_embedding(temp_storage_manager: StorageManager) -> None:
    """Test saving content item without embedding."""
    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
    )

    db_id = temp_storage_manager.save_content_item(item)
    assert db_id > 0

    retrieved = temp_storage_manager.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"


def test_save_content_item_with_embedding(temp_storage_manager: StorageManager) -> None:
    """Test saving content item with embedding."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    embedding = [0.1, 0.2, 0.3, 0.4, 0.5]

    db_id = temp_storage_manager.save_content_item(item, embedding)
    assert db_id > 0

    # Check SQLite
    retrieved = temp_storage_manager.get_content_item(db_id)
    assert retrieved is not None

    # Check vector DB
    retrieved_embedding = temp_storage_manager.get_embedding("123")
    assert retrieved_embedding is not None
    # Use approximate equality for floating-point comparison
    assert len(retrieved_embedding) == len(embedding)
    for r, e in zip(retrieved_embedding, embedding):
        assert abs(r - e) < 1e-6


def test_get_unconsumed_items(temp_storage_manager: StorageManager) -> None:
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
        temp_storage_manager.save_content_item(item)

    unconsumed = temp_storage_manager.get_unconsumed_items()
    assert len(unconsumed) == 3
    assert all(item.status == ConsumptionStatus.UNREAD for item in unconsumed)


def test_get_completed_items(temp_storage_manager: StorageManager) -> None:
    """Test getting completed items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3 + i,
        )
        for i in range(3)
    ]

    for item in items:
        temp_storage_manager.save_content_item(item)

    completed = temp_storage_manager.get_completed_items(min_rating=4)
    assert len(completed) == 2


def test_search_similar(temp_storage_manager: StorageManager) -> None:
    """Test searching for similar content."""
    # Add some items with embeddings
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        for i in range(3)
    ]

    embeddings = [
        [0.1, 0.2, 0.3],
        [0.2, 0.3, 0.4],
        [0.9, 0.8, 0.7],
    ]

    for item, embedding in zip(items, embeddings):
        temp_storage_manager.save_content_item(item, embedding)

    # Search for similar
    query_embedding = [0.15, 0.25, 0.35]
    results = temp_storage_manager.search_similar(query_embedding, n_results=2)

    assert len(results) <= 2


def test_delete_content_item(temp_storage_manager: StorageManager) -> None:
    """Test deleting content item from both databases."""
    item = ContentItem(
        id="123",
        title="Test Book",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    embedding = [0.1, 0.2, 0.3]

    db_id = temp_storage_manager.save_content_item(item, embedding)
    assert temp_storage_manager.get_content_item(db_id) is not None
    assert temp_storage_manager.get_embedding("123") is not None

    deleted = temp_storage_manager.delete_content_item(db_id)
    assert deleted is True

    assert temp_storage_manager.get_content_item(db_id) is None
    assert temp_storage_manager.get_embedding("123") is None


def test_count_items(temp_storage_manager: StorageManager) -> None:
    """Test counting items."""
    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK if i < 3 else ContentType.MOVIE,
            status=ConsumptionStatus.COMPLETED if i % 2 == 0 else ConsumptionStatus.UNREAD,
        )
        for i in range(5)
    ]

    for item in items:
        temp_storage_manager.save_content_item(item)

    assert temp_storage_manager.count_items() == 5
    assert temp_storage_manager.count_items(content_type=ContentType.BOOK) == 3
