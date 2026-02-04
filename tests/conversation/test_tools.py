"""Tests for conversation tool system."""

import tempfile
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest

from src.conversation.tools import (
    CONVERSATION_TOOLS,
    ToolExecutor,
    get_tool_descriptions,
    parse_tool_call_from_text,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def tool_executor(storage_manager: StorageManager) -> ToolExecutor:
    """Create a tool executor for testing."""
    return ToolExecutor(storage_manager)


@pytest.fixture
def sample_items(storage_manager: StorageManager) -> list[int]:
    """Create sample content items and return their db_ids."""
    items = [
        ContentItem(
            id="book1",
            title="The Martian",
            author="Andy Weir",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="game1",
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="game2",
            title="Red Dead Redemption 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
    ]
    db_ids = []
    for item in items:
        db_id = storage_manager.save_content_item(item, user_id=1)
        db_ids.append(db_id)
    return db_ids


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_conversation_tools_defined(self) -> None:
        """Test that conversation tools are properly defined."""
        assert len(CONVERSATION_TOOLS) > 0
        required_tools = [
            "mark_completed",
            "update_rating",
            "add_to_wishlist",
            "clarify_item",
            "save_memory",
            "search_items",
        ]
        tool_names = [t["name"] for t in CONVERSATION_TOOLS]
        for tool in required_tools:
            assert tool in tool_names

    def test_tools_have_required_fields(self) -> None:
        """Test that each tool has required fields."""
        for tool in CONVERSATION_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool

    def test_get_tool_descriptions(self) -> None:
        """Test getting formatted tool descriptions."""
        descriptions = get_tool_descriptions()
        assert "mark_completed" in descriptions
        assert "update_rating" in descriptions
        assert isinstance(descriptions, str)


class TestMarkCompleted:
    """Tests for mark_completed tool."""

    def test_mark_item_completed_with_rating(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test marking an item as completed with a rating."""
        item_id = sample_items[0]  # The Martian

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": item_id, "rating": 5},
            user_id=1,
        )

        assert result.success
        assert "The Martian" in result.message
        assert "completed" in result.message

        # Verify in database
        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.rating == 5

    def test_mark_item_completed_with_review(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test marking an item as completed with a review."""
        item_id = sample_items[1]  # Outer Wilds

        result = tool_executor.execute(
            "mark_completed",
            {
                "item_id": item_id,
                "rating": 5,
                "review": "Amazing exploration game!",
            },
            user_id=1,
        )

        assert result.success

        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.review == "Amazing exploration game!"

    def test_mark_item_completed_with_date(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test marking an item as completed with a specific date."""
        item_id = sample_items[0]

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": item_id, "date_completed": "2024-01-15"},
            user_id=1,
        )

        assert result.success

        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.date_completed == date(2024, 1, 15)

    def test_mark_item_completed_nonexistent(self, tool_executor: ToolExecutor) -> None:
        """Test marking a non-existent item."""
        result = tool_executor.execute(
            "mark_completed",
            {"item_id": 99999},
            user_id=1,
        )

        assert not result.success
        assert "not found" in result.message.lower()

    def test_mark_item_invalid_rating(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test marking an item with invalid rating."""
        result = tool_executor.execute(
            "mark_completed",
            {"item_id": sample_items[0], "rating": 10},
            user_id=1,
        )

        assert not result.success
        assert "1-5" in result.message

    def test_mark_item_missing_id(self, tool_executor: ToolExecutor) -> None:
        """Test marking without item_id."""
        result = tool_executor.execute("mark_completed", {}, user_id=1)

        assert not result.success
        assert "required" in result.message.lower()


class TestUpdateRating:
    """Tests for update_rating tool."""

    def test_update_rating_success(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test updating the rating of an item."""
        item_id = sample_items[2]  # RDR2, rated 5

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 4},
            user_id=1,
        )

        assert result.success
        assert "4/5" in result.message
        assert result.data is not None
        assert result.data["old_rating"] == 5
        assert result.data["new_rating"] == 4

    def test_update_rating_with_review(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test updating rating with a new review."""
        item_id = sample_items[2]

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 5, "review": "Updated review"},
            user_id=1,
        )

        assert result.success

        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.review == "Updated review"

    def test_update_rating_missing_rating(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test updating without rating value."""
        result = tool_executor.execute(
            "update_rating",
            {"item_id": sample_items[0]},
            user_id=1,
        )

        assert not result.success
        assert "required" in result.message.lower()


class TestAddToWishlist:
    """Tests for add_to_wishlist tool."""

    def test_add_to_wishlist_book(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Test adding a book to wishlist."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {
                "title": "Project Hail Mary",
                "content_type": "book",
                "author": "Andy Weir",
            },
            user_id=1,
        )

        assert result.success
        assert "Project Hail Mary" in result.message
        assert result.data is not None
        assert "item_id" in result.data

        # Verify in database
        items = storage_manager.get_unconsumed_items(
            user_id=1, content_type=ContentType.BOOK
        )
        assert any(item.title == "Project Hail Mary" for item in items)

    def test_add_to_wishlist_game(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Test adding a game to wishlist."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {"title": "Disco Elysium", "content_type": "video_game"},
            user_id=1,
        )

        assert result.success
        assert "video game" in result.message.lower()

    def test_add_to_wishlist_invalid_type(self, tool_executor: ToolExecutor) -> None:
        """Test adding with invalid content type."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {"title": "Something", "content_type": "podcast"},
            user_id=1,
        )

        assert not result.success
        assert "invalid" in result.message.lower()

    def test_add_to_wishlist_missing_title(self, tool_executor: ToolExecutor) -> None:
        """Test adding without title."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {"content_type": "book"},
            user_id=1,
        )

        assert not result.success


class TestClarifyItem:
    """Tests for clarify_item tool."""

    def test_clarify_item_success(self, tool_executor: ToolExecutor) -> None:
        """Test clarification request."""
        matches = [
            {
                "id": 1,
                "title": "Dune",
                "author": "Frank Herbert",
                "content_type": "book",
            },
            {"id": 2, "title": "Dune", "content_type": "movie"},
        ]

        result = tool_executor.execute(
            "clarify_item",
            {"query": "dune", "matches": matches},
            user_id=1,
        )

        assert result.success
        assert result.needs_clarification
        assert result.clarification_options == matches

    def test_clarify_item_empty_matches(self, tool_executor: ToolExecutor) -> None:
        """Test clarification with no matches."""
        result = tool_executor.execute(
            "clarify_item",
            {"query": "dune", "matches": []},
            user_id=1,
        )

        assert not result.success


class TestSaveMemory:
    """Tests for save_memory tool."""

    def test_save_memory_success(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Test saving a user preference."""
        result = tool_executor.execute(
            "save_memory",
            {"memory_text": "I prefer shorter games during weekdays"},
            user_id=1,
        )

        assert result.success
        assert "Noted" in result.message
        assert result.data is not None
        assert "memory_id" in result.data

        # Verify in database
        memories = storage_manager.get_core_memories(user_id=1)
        assert any(
            m["memory_text"] == "I prefer shorter games during weekdays"
            for m in memories
        )

    def test_save_memory_missing_text(self, tool_executor: ToolExecutor) -> None:
        """Test saving without memory text."""
        result = tool_executor.execute(
            "save_memory",
            {},
            user_id=1,
        )

        assert not result.success


class TestSearchItems:
    """Tests for search_items tool."""

    def test_search_items_single_match(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with a single match."""
        result = tool_executor.execute(
            "search_items",
            {"query": "Martian"},
            user_id=1,
        )

        assert result.success
        assert "The Martian" in result.message
        assert result.data is not None
        assert len(result.data["matches"]) == 1

    def test_search_items_multiple_matches(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with multiple matches."""
        result = tool_executor.execute(
            "search_items",
            {"query": "d"},  # Matches "Red Dead", "Outer Wilds" (has 'd')
            user_id=1,
        )

        assert result.success
        # Should indicate multiple matches
        if result.data and len(result.data.get("matches", [])) > 1:
            assert result.needs_clarification

    def test_search_items_no_matches(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with no matches."""
        result = tool_executor.execute(
            "search_items",
            {"query": "nonexistent title xyz"},
            user_id=1,
        )

        assert result.success
        assert "No items found" in result.message

    def test_search_items_with_content_type(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with content type filter."""
        result = tool_executor.execute(
            "search_items",
            {"query": "Outer", "content_type": "video_game"},
            user_id=1,
        )

        assert result.success
        assert result.data is not None
        matches = result.data.get("matches", [])
        assert all(m["content_type"] == "video_game" for m in matches)


class TestUnknownTool:
    """Tests for unknown tool handling."""

    def test_unknown_tool_returns_error(self, tool_executor: ToolExecutor) -> None:
        """Test that unknown tools return an error."""
        result = tool_executor.execute(
            "unknown_tool",
            {},
            user_id=1,
        )

        assert not result.success
        assert "Unknown tool" in result.message


class TestFindMatchingItems:
    """Tests for find_matching_items helper."""

    def test_find_matching_items(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test finding items by title."""
        matches = tool_executor.find_matching_items("Outer", user_id=1)

        assert len(matches) == 1
        assert matches[0].title == "Outer Wilds"

    def test_find_matching_items_with_filter(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test finding items with content type filter."""
        matches = tool_executor.find_matching_items(
            "Outer", user_id=1, content_type=ContentType.BOOK
        )

        # Should not find Outer Wilds since it's a game
        assert len(matches) == 0


class TestParseToolCall:
    """Tests for parsing tool calls from text."""

    def test_parse_json_tool_call(self) -> None:
        """Test parsing a JSON tool call."""
        text = '{"tool": "mark_completed", "params": {"item_id": 1, "rating": 5}}'
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name == "mark_completed"
        assert params == {"item_id": 1, "rating": 5}

    def test_parse_function_format(self) -> None:
        """Test parsing alternative function format."""
        text = '{"function": "save_memory", "arguments": {"memory_text": "test"}}'
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name == "save_memory"
        assert params == {"memory_text": "test"}

    def test_parse_no_tool_call(self) -> None:
        """Test that non-tool text returns None."""
        text = "This is just a regular response with no tool call."
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name is None
        assert params is None

    def test_parse_invalid_json(self) -> None:
        """Test that invalid JSON returns None."""
        text = "Here's some text with {invalid json"
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name is None
        assert params is None
