"""Regression tests for enum handling with Pydantic use_enum_values=True.

Bug: 'str' object has no attribute 'value' — code read ``.value`` on enum
fields Pydantic's ``use_enum_values=True`` had already turned into strings.
Fixed in: src/storage/manager.py
"""

from pathlib import Path

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


def test_storage_manager_save_with_string_enum(tmp_path: Path):
    """Test that saving ContentItem with string enums works correctly.

    This ensures the entire save/retrieve cycle works with string enums.
    """
    storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")

    # Create item - enums will be converted to strings
    item = ContentItem(
        id="test-book",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=5,
        review="Great book!",
        metadata={"pages": 300},
    )

    # Verify it's a string before saving
    assert isinstance(item.content_type, str)
    assert isinstance(item.status, str)

    db_id = storage_manager.save_content_item(item)
    assert db_id > 0

    # Retrieve - should still work
    retrieved = storage_manager.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"
    assert isinstance(retrieved.content_type, str)
    assert isinstance(retrieved.status, str)
