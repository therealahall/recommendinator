"""Tests for the conversation engine."""

import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.conversation.engine import ConversationEngine, create_conversation_engine
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def mock_ollama() -> MagicMock:
    """Create a mock Ollama client."""
    client = MagicMock()

    # Default streaming response
    def mock_chat_stream(*args, **kwargs) -> Iterator[str]:
        yield "Based on your taste, "
        yield "I recommend "
        yield "Outer Wilds!"

    client.chat_stream.side_effect = mock_chat_stream
    client.generate_text.return_value = "Based on your taste, I recommend Outer Wilds!"

    return client


@pytest.fixture
def conversation_engine(
    storage_manager: StorageManager, mock_ollama: MagicMock
) -> ConversationEngine:
    """Create a conversation engine for testing."""
    return ConversationEngine(
        storage_manager=storage_manager,
        ollama_client=mock_ollama,
    )


@pytest.fixture
def sample_items(storage_manager: StorageManager) -> list[int]:
    """Create sample content items and return their db_ids."""
    items = [
        ContentItem(
            id="game1",
            title="Red Dead Redemption 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="game2",
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
    ]
    db_ids = []
    for item in items:
        db_id = storage_manager.save_content_item(item, user_id=1)
        db_ids.append(db_id)
    return db_ids


class TestConversationEngine:
    """Tests for the ConversationEngine class."""

    def test_process_message_streams_response(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test that process_message yields streaming chunks."""
        chunks = list(
            conversation_engine.process_message(
                user_id=1,
                message="What game should I play next?",
                stream=True,
            )
        )

        # Should have text chunks
        text_chunks = [c for c in chunks if c.chunk_type == "text"]
        assert len(text_chunks) > 0

        # Should end with done chunk
        assert chunks[-1].chunk_type == "done"

    def test_process_message_saves_history(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test that messages are saved to conversation history."""
        # Process a message
        list(
            conversation_engine.process_message(
                user_id=1,
                message="What should I play?",
            )
        )

        # Check history
        history = conversation_engine.memory.get_conversation_history(user_id=1)

        # Should have user message and assistant response
        assert len(history) == 2
        assert history[0].role == "user"
        assert history[0].content == "What should I play?"
        assert history[1].role == "assistant"

    def test_process_message_sync(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test non-streaming message processing."""
        response = conversation_engine.process_message_sync(
            user_id=1,
            message="What game next?",
        )

        assert isinstance(response, str)
        assert len(response) > 0

    def test_reset_conversation(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test conversation reset clears history but preserves memories."""
        # Add some history
        list(
            conversation_engine.process_message(
                user_id=1,
                message="Test message",
            )
        )

        # Add a core memory
        conversation_engine.memory.save_core_memory(
            user_id=1,
            memory_text="I prefer sci-fi games",
            memory_type="user_stated",
            source="conversation",
        )

        # Reset conversation
        deleted = conversation_engine.reset_conversation(user_id=1)
        assert deleted > 0

        # History should be empty
        history = conversation_engine.memory.get_conversation_history(user_id=1)
        assert len(history) == 0

        # Memories should remain
        memories = conversation_engine.memory.get_core_memories(user_id=1)
        assert len(memories) == 1

    def test_process_message_with_content_type(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
        sample_items: list[int],
    ) -> None:
        """Test processing with content type filter."""
        chunks = list(
            conversation_engine.process_message(
                user_id=1,
                message="What game should I play?",
                content_type=ContentType.VIDEO_GAME,
            )
        )

        # Should complete without error
        assert chunks[-1].chunk_type == "done"

    def test_process_message_handles_llm_error(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test graceful handling of LLM errors."""
        # Create mock that raises an error
        mock_ollama = MagicMock()
        mock_ollama.chat_stream.side_effect = Exception("Connection failed")
        mock_ollama.generate_text.side_effect = Exception("Connection failed")

        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        chunks = list(
            engine.process_message(
                user_id=1,
                message="Test message",
            )
        )

        # Should still get some response (error message)
        text_chunks = [c for c in chunks if c.chunk_type == "text" and c.content]
        assert len(text_chunks) > 0

        # Error message should mention connection issue
        combined = "".join(c.content for c in text_chunks if c.content)
        assert "trouble" in combined.lower() or "error" in combined.lower()


class TestToolExecution:
    """Tests for tool execution within conversation."""

    def test_tool_call_detected_and_executed(
        self, storage_manager: StorageManager, sample_items: list[int]
    ) -> None:
        """Test that tool calls in responses are detected and executed."""
        # Mock LLM to return a tool call
        mock_ollama = MagicMock()

        def mock_stream(*args, **kwargs) -> Iterator[str]:
            yield '{"tool": "mark_completed", "params": {"item_id": '
            yield f"{sample_items[1]}, "
            yield '"rating": 5}}'

        mock_ollama.chat_stream.side_effect = mock_stream

        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        chunks = list(
            engine.process_message(
                user_id=1,
                message="I just finished Outer Wilds, 5 stars!",
            )
        )

        # Should have a tool_call chunk
        tool_calls = [c for c in chunks if c.chunk_type == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "mark_completed"

        # Should have a tool_result chunk
        tool_results = [c for c in chunks if c.chunk_type == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0].tool_result is not None
        assert tool_results[0].tool_result.success

        # Item should be marked completed
        item = storage_manager.get_content_item(sample_items[1], user_id=1)
        assert item is not None
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.rating == 5

    def test_clarification_needed(self, storage_manager: StorageManager) -> None:
        """Test handling when clarification is needed."""
        # Create two items with similar names
        storage_manager.save_content_item(
            ContentItem(
                id="dune1",
                title="Dune",
                author="Frank Herbert",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )
        storage_manager.save_content_item(
            ContentItem(
                id="dune2",
                title="Dune",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        # Mock LLM to return a clarify_item call
        mock_ollama = MagicMock()

        def mock_stream(*args, **kwargs) -> Iterator[str]:
            yield '{"tool": "clarify_item", "params": {"query": "dune", "matches": ['
            yield '{"id": 1, "title": "Dune", "content_type": "book"}, '
            yield '{"id": 2, "title": "Dune", "content_type": "movie"}]}}'

        mock_ollama.chat_stream.side_effect = mock_stream

        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        chunks = list(
            engine.process_message(
                user_id=1,
                message="I finished Dune",
            )
        )

        # Should have clarification result
        tool_results = [c for c in chunks if c.chunk_type == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0].tool_result is not None
        assert tool_results[0].tool_result.needs_clarification


class TestContextIntegration:
    """Tests for context integration in conversations."""

    def test_context_includes_memories(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test that core memories are included in context."""
        # Add a memory
        conversation_engine.memory.save_core_memory(
            user_id=1,
            memory_text="I love exploration games",
            memory_type="user_stated",
            source="manual",
        )

        # Process a message (this will trigger context assembly)
        list(
            conversation_engine.process_message(
                user_id=1,
                message="What should I play?",
            )
        )

        # Check that the system prompt was called
        mock_ollama.chat_stream.assert_called()

        # The context should include the memory in the system prompt
        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")
        assert "exploration games" in system_prompt

    def test_context_includes_completed_items(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
        sample_items: list[int],
    ) -> None:
        """Test that completed items are included in context."""
        list(
            conversation_engine.process_message(
                user_id=1,
                message="What game should I play?",
            )
        )

        # Check system prompt includes the completed item
        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")
        assert "Red Dead Redemption 2" in system_prompt

    def test_context_includes_backlog(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
        sample_items: list[int],
    ) -> None:
        """Test that backlog items are included in context."""
        list(
            conversation_engine.process_message(
                user_id=1,
                message="What game should I play?",
            )
        )

        # Check system prompt includes the backlog item
        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")
        assert "Outer Wilds" in system_prompt


class TestFactoryFunction:
    """Tests for the create_conversation_engine factory."""

    def test_create_conversation_engine(
        self, storage_manager: StorageManager, mock_ollama: MagicMock
    ) -> None:
        """Test factory function creates working engine."""
        engine = create_conversation_engine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        assert isinstance(engine, ConversationEngine)
        assert engine.storage is storage_manager
        assert engine.ollama is mock_ollama


class TestStreamingBehavior:
    """Tests for streaming response behavior."""

    def test_streaming_chunks_are_ordered(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test that streaming chunks come in order."""
        chunks = list(
            conversation_engine.process_message(
                user_id=1,
                message="Test",
                stream=True,
            )
        )

        # All text chunks before done
        done_idx = next(i for i, c in enumerate(chunks) if c.chunk_type == "done")
        text_indices = [i for i, c in enumerate(chunks) if c.chunk_type == "text"]

        for idx in text_indices:
            assert idx < done_idx

    def test_streaming_combines_to_full_response(
        self,
        conversation_engine: ConversationEngine,
        mock_ollama: MagicMock,
    ) -> None:
        """Test that streaming chunks combine to full response."""
        chunks = list(
            conversation_engine.process_message(
                user_id=1,
                message="Test",
                stream=True,
            )
        )

        # Combine text chunks
        text_content = "".join(
            c.content for c in chunks if c.chunk_type == "text" and c.content
        )

        # Should match expected mock response
        assert "Based on your taste" in text_content
        assert "Outer Wilds" in text_content
