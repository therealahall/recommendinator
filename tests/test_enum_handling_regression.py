"""Regression tests for enum handling with Pydantic use_enum_values=True.

This test suite covers the fix for the bug where code was trying to access
.value on enum fields that were already converted to strings by Pydantic's
use_enum_values=True configuration.

Bug: 'str' object has no attribute 'value'
Fixed in: src/llm/prompts.py, src/storage/manager.py
"""

from pathlib import Path

from src.llm.prompts import build_content_description, build_recommendation_prompt
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


def test_build_content_description_with_string_enum():
    """Test that build_content_description works with ContentItem having string enums.

    Regression test for: 'str' object has no attribute 'value' when accessing
    item.content_type.value in build_content_description.

    With use_enum_values=True, ContentItem.content_type is a string, not an enum.
    """
    # Create ContentItem - Pydantic converts enums to strings due to use_enum_values=True
    item = ContentItem(
        id="test-123",
        title="The Hitchhiker's Guide to the Galaxy",
        author="Douglas Adams",
        content_type=ContentType.BOOK,  # Will be converted to "book" string
        status=ConsumptionStatus.COMPLETED,  # Will be converted to "completed" string
        rating=5,
        review="Absolutely hilarious!",
        metadata={"pages": 193, "genre": "Science Fiction"},
    )

    # Verify that content_type is actually a string (due to use_enum_values=True)
    assert isinstance(
        item.content_type, str
    ), "content_type should be string with use_enum_values=True"
    assert item.content_type == "book"

    # This should not raise AttributeError: 'str' object has no attribute 'value'
    description = build_content_description(item)

    # Verify the description was built correctly
    assert "The Hitchhiker's Guide to the Galaxy" in description
    assert "Douglas Adams" in description
    assert "Absolutely hilarious!" in description
    assert "Science Fiction" in description
    assert "193" in description  # Pages should be included for books


def test_build_content_description_with_different_content_types():
    """Test build_content_description with different content types as strings."""
    test_cases = [
        (ContentType.BOOK, {"pages": 300}, True),  # Pages should be included
        (ContentType.MOVIE, {"pages": 300}, False),  # Pages should NOT be included
        (ContentType.TV_SHOW, {"pages": 300}, False),  # Pages should NOT be included
        (ContentType.VIDEO_GAME, {"pages": 300}, False),  # Pages should NOT be included
    ]

    for content_type, metadata, should_include_pages in test_cases:
        item = ContentItem(
            id=f"test-{content_type}",
            title="Test Item",
            content_type=content_type,
            status=ConsumptionStatus.COMPLETED,
            metadata=metadata,
        )

        # Verify content_type is string
        assert isinstance(item.content_type, str)

        # Should not raise AttributeError
        description = build_content_description(item)

        if should_include_pages:
            assert "Pages: 300" in description
        else:
            assert "Pages: 300" not in description


def test_build_recommendation_prompt_with_string_enum():
    """Test that build_recommendation_prompt works with ContentType enum/string.

    Regression test for: 'str' object has no attribute 'value' when accessing
    content_type.value in build_recommendation_prompt.
    """
    # Test with enum (as passed from calling code)
    consumed_items = [
        ContentItem(
            id=f"book-{i}",
            title=f"Book {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        for i in range(3)
    ]

    unconsumed_items = [
        ContentItem(
            id=f"book-{i}",
            title=f"Unread Book {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        for i in range(5)
    ]

    # Should work with enum ContentType
    prompt = build_recommendation_prompt(
        content_type=ContentType.BOOK,
        consumed_items=consumed_items,
        unconsumed_items=unconsumed_items,
        count=3,
    )

    assert "book" in prompt.lower()
    assert "Book 0" in prompt
    assert "Unread Book 0" in prompt


def test_storage_manager_search_similar_with_string_enum(tmp_path: Path):
    """Test that search_similar works when ContentItem has string enums.

    Regression test for: 'str' object has no attribute 'value' when accessing
    content_type.value in StorageManager.search_similar.
    """
    storage_manager = StorageManager(
        sqlite_path=tmp_path / "test.db",
        vector_db_path=tmp_path / "vector_db",
        ai_enabled=True,
    )

    # Create and save items - these will have string enums after saving/retrieving
    items = [
        ContentItem(
            id=f"item-{i}",
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

    for item, item_embedding in zip(items, embeddings, strict=True):
        storage_manager.save_content_item(item, embedding=item_embedding)

    # Retrieve items - these will have string enums due to use_enum_values=True
    retrieved_items = storage_manager.get_unconsumed_items()
    assert len(retrieved_items) > 0

    # Verify retrieved items have string enums
    for item in retrieved_items:
        assert isinstance(
            item.content_type, str
        ), "Retrieved item should have string content_type"
        assert isinstance(item.status, str), "Retrieved item should have string status"

    # Test search_similar with ContentType enum (as passed from calling code)
    # This should not raise AttributeError: 'str' object has no attribute 'value'
    query_embedding = [0.15, 0.25, 0.35]
    results = storage_manager.search_similar(
        query_embedding=query_embedding,
        n_results=2,
        content_type=ContentType.BOOK,  # Pass enum, should handle correctly
        exclude_consumed=True,
    )

    # Should return results without error
    assert isinstance(results, list)


def test_storage_manager_save_with_string_enum(tmp_path: Path):
    """Test that saving ContentItem with string enums works correctly.

    This ensures the entire save/retrieve cycle works with string enums.
    """
    storage_manager = StorageManager(
        sqlite_path=tmp_path / "test.db",
        vector_db_path=tmp_path / "vector_db",
    )

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

    # Save with embedding - should not raise AttributeError
    item_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    db_id = storage_manager.save_content_item(item, embedding=item_embedding)
    assert db_id > 0

    # Retrieve - should still work
    retrieved = storage_manager.get_content_item(db_id)
    assert retrieved is not None
    assert retrieved.title == "Test Book"
    assert isinstance(retrieved.content_type, str)
    assert isinstance(retrieved.status, str)
