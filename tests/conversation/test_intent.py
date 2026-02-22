"""Tests for pre-LLM intent detection."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from src.conversation.intent import (
    IntentResult,
    build_confirmation_message,
    detect_intent,
)
from src.conversation.tools import ToolExecutor
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
            id="game1",
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="book1",
            title="The Martian",
            author="Andy Weir",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="game2",
            title="Red Dead Redemption 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
    ]
    db_ids = []
    for item in items:
        db_id = storage_manager.save_content_item(item, user_id=1)
        db_ids.append(db_id)
    return db_ids


class TestDetectCompletedIntent:
    """Tests for detecting 'mark completed' intents."""

    def test_detects_finished_pattern(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'I finished Outer Wilds' should detect mark_completed."""
        result = detect_intent(
            "I finished Outer Wilds", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"
        assert result.tool_params is not None
        assert result.tool_params["item_id"] == sample_items[0]
        assert result.confidence >= 0.8

    def test_detects_just_completed_pattern(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'just completed Outer Wilds' should detect mark_completed."""
        result = detect_intent(
            "just completed Outer Wilds", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"

    def test_detects_beat_pattern(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'I beat Outer Wilds' should detect mark_completed."""
        result = detect_intent(
            "I beat Outer Wilds", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"

    def test_detects_done_with_pattern(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'done with Outer Wilds' should detect mark_completed."""
        result = detect_intent(
            "done with Outer Wilds", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"

    def test_no_match_for_unknown_title(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Unknown title should fall back to conversation."""
        result = detect_intent(
            "I finished Nonexistent Game", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "conversation"


class TestDetectCompletedWithRating:
    """Tests for detecting 'completed with rating' intents."""

    def test_detects_finished_with_rating(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'I finished Outer Wilds, 5/5' should detect with rating."""
        result = detect_intent(
            "I finished Outer Wilds, 5/5", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"
        assert result.tool_params is not None
        assert result.tool_params["rating"] == 5
        assert result.confidence >= 0.9

    def test_detects_finished_with_stars(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'beat Outer Wilds 4 stars' should detect with rating."""
        result = detect_intent(
            "beat Outer Wilds 4 stars", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"
        assert result.tool_params is not None
        assert result.tool_params["rating"] == 4


class TestDetectRatingIntent:
    """Tests for detecting 'update rating' intents."""

    def test_detects_rate_pattern(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'rate The Martian 4/5' should detect update_rating."""
        result = detect_intent(
            "rate The Martian 4/5", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "update_rating"
        assert result.tool_params is not None
        assert result.tool_params["rating"] == 4

    def test_detects_was_pattern(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'The Martian was a 5/5' should detect update_rating."""
        result = detect_intent(
            "The Martian was a 5/5", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "update_rating"
        assert result.tool_params is not None
        assert result.tool_params["rating"] == 5


class TestDetectWishlistIntent:
    """Tests for detecting 'add to wishlist' intents."""

    def test_wishlist_falls_through_to_llm(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Wishlist patterns fall through to conversation for LLM handling.

        The LLM has access to add_to_wishlist tool and can infer content_type
        from context, which pre-LLM detection cannot determine.
        """
        for message in [
            "add Hades to my list",
            "put Disco Elysium on my backlog",
            "I want to try Hades",
        ]:
            result = detect_intent(message, user_id=1, tool_executor=tool_executor)
            assert result.intent_type == "conversation"


class TestDetectPreferenceIntent:
    """Tests for detecting 'save preference' intents."""

    def test_detects_i_love(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'I love roguelikes' should detect save_memory."""
        result = detect_intent(
            "I love roguelikes", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "save_memory"
        assert result.tool_params is not None
        assert "roguelikes" in result.tool_params["memory_text"]

    def test_detects_i_hate(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'I hate grinding' should detect save_memory."""
        result = detect_intent(
            "I hate grinding", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "save_memory"
        assert result.tool_params is not None
        assert "grinding" in result.tool_params["memory_text"]

    def test_detects_i_prefer(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """'I prefer short games' should detect save_memory."""
        result = detect_intent(
            "I prefer short games", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "save_memory"


class TestConversationFallback:
    """Tests for messages that should NOT trigger intent detection."""

    def test_recommendation_question_falls_through(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Questions about recommendations should go to conversation."""
        result = detect_intent(
            "What game should I play next?", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "conversation"

    def test_general_chat_falls_through(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """General chat should go to conversation."""
        result = detect_intent(
            "Tell me about Outer Wilds", user_id=1, tool_executor=tool_executor
        )

        assert result.intent_type == "conversation"

    def test_ambiguous_title_falls_through(
        self,
        storage_manager: StorageManager,
        sample_items: list[int],
    ) -> None:
        """Multiple matches for a title should fall back to conversation."""
        # Add a second item with similar name
        storage_manager.save_content_item(
            ContentItem(
                id="game_dupe",
                title="Outer Wilds: Echoes of the Eye",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        executor = ToolExecutor(storage_manager)
        result = detect_intent(
            "I finished Outer Wilds", user_id=1, tool_executor=executor
        )

        # Multiple matches → can't resolve unambiguously → conversation
        assert result.intent_type == "conversation"


class TestBuildConfirmationMessage:
    """Tests for building confirmation messages."""

    def test_completed_with_rating(self) -> None:
        """Completed item with rating gives specific confirmation."""
        item = ContentItem(
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        message = build_confirmation_message(
            tool_name="mark_completed",
            tool_params={"item_id": 1, "rating": 5},
            matched_item=item,
        )

        assert "Outer Wilds" in message
        assert "5/5" in message
        assert "completed" in message.lower()

    def test_completed_without_rating(self) -> None:
        """Completed item without rating omits rating text."""
        item = ContentItem(
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
        )
        message = build_confirmation_message(
            tool_name="mark_completed",
            tool_params={"item_id": 1},
            matched_item=item,
        )

        assert "Outer Wilds" in message
        assert "completed" in message.lower()
        assert "/5" not in message

    def test_rating_update(self) -> None:
        """Rating update confirmation includes new rating."""
        item = ContentItem(
            title="The Martian",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
        )
        message = build_confirmation_message(
            tool_name="update_rating",
            tool_params={"item_id": 1, "rating": 4},
            matched_item=item,
        )

        assert "The Martian" in message
        assert "4/5" in message

    def test_wishlist_add(self) -> None:
        """Wishlist add confirmation includes title."""
        message = build_confirmation_message(
            tool_name="add_to_wishlist",
            tool_params={"title": "Hades", "content_type": "video_game"},
        )

        assert "Hades" in message
        assert "backlog" in message.lower()

    def test_save_memory(self) -> None:
        """Save memory confirmation includes preference text."""
        message = build_confirmation_message(
            tool_name="save_memory",
            tool_params={"memory_text": "I love roguelikes"},
        )

        assert "roguelikes" in message


class TestRatingBoundaryEdgeCases:
    """Tests for rating boundary values and degenerate input."""

    def test_rating_zero_rejected(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Rating 0 should not be accepted — falls back to conversation.

        The regex captures the digit 0, but validation rejects it because
        the valid range is 1 <= rating <= 5.
        """
        result = detect_intent("rate Dune 0/5", user_id=1, tool_executor=tool_executor)

        assert result.intent_type == "conversation"

    def test_rating_six_rejected(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Rating 6 should not be accepted — falls back to conversation.

        The regex captures the digit 6, but validation rejects it because
        the valid range is 1 <= rating <= 5.
        """
        result = detect_intent("rate Dune 6/5", user_id=1, tool_executor=tool_executor)

        assert result.intent_type == "conversation"

    def test_empty_string_message(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Empty string message should fall back to conversation."""
        result = detect_intent("", user_id=1, tool_executor=tool_executor)

        assert result.intent_type == "conversation"

    def test_whitespace_only_message(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
    ) -> None:
        """Whitespace-only message should fall back to conversation."""
        result = detect_intent("   \t\n  ", user_id=1, tool_executor=tool_executor)

        assert result.intent_type == "conversation"


class TestIntentResult:
    """Tests for IntentResult dataclass."""

    def test_conversation_default(self) -> None:
        """Default IntentResult has conversation type."""
        result = IntentResult(intent_type="conversation")

        assert result.intent_type == "conversation"
        assert result.tool_name is None
        assert result.tool_params is None
        assert result.confidence == 0.0
        assert result.matched_item is None

    def test_tool_action_result(self) -> None:
        """Tool action IntentResult carries all fields."""
        item = ContentItem(
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        result = IntentResult(
            intent_type="tool_action",
            tool_name="mark_completed",
            tool_params={"item_id": 1},
            confidence=0.95,
            matched_item=item,
        )

        assert result.intent_type == "tool_action"
        assert result.tool_name == "mark_completed"
        assert result.confidence == 0.95
        assert result.matched_item is item
