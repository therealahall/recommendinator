"""Tests for the conversation engine."""

import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.conversation.engine import (
    COMPACT_SYSTEM_PROMPT,
    FULL_SYSTEM_PROMPT,
    ConversationEngine,
    create_conversation_engine,
)
from src.llm.client import OllamaClient
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import ConversationContext, ConversationMessage
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
    client = MagicMock(spec=OllamaClient)
    client.conversation_model = "test-model"

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
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.conversation_model = "test-model"
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
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.conversation_model = "test-model"

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
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.conversation_model = "test-model"

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
        assert engine.ollama_client is mock_ollama


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


class TestCompactMode:
    """Tests for compact mode behavior."""

    def test_compact_mode_uses_compact_prompt(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """Compact mode should use the compact system prompt template."""
        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"context": {"compact_mode": True}},
        )

        assert engine.compact_mode is True
        assert engine.system_prompt_template is COMPACT_SYSTEM_PROMPT

    def test_default_mode_uses_full_prompt(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """Default mode should use the full system prompt template."""
        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        assert engine.compact_mode is False
        assert engine.system_prompt_template is FULL_SYSTEM_PROMPT

    def test_compact_mode_skips_tool_descriptions(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """Compact mode system prompt should not contain tool descriptions."""
        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"context": {"compact_mode": True}},
        )

        list(
            engine.process_message(
                user_id=1,
                message="What should I play?",
                stream=True,
            )
        )

        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        system_prompt = call_kwargs.get("system_prompt", "")
        # Should NOT contain tool descriptions
        assert "mark_completed" not in system_prompt
        assert "update_rating" not in system_prompt

    def test_compact_mode_skips_post_hoc_tool_parsing(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Compact mode should not parse tool calls from LLM response."""
        mock_ollama = MagicMock(spec=OllamaClient)

        # Mock LLM to return what looks like a tool call
        def mock_stream(*args, **kwargs):
            yield '{"tool": "mark_completed", "params": {"item_id": 1}}'

        mock_ollama.chat_stream.side_effect = mock_stream
        mock_ollama.conversation_model = "test-model"

        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"context": {"compact_mode": True}},
        )

        chunks = list(
            engine.process_message(
                user_id=1,
                message="What should I play?",
                stream=True,
            )
        )

        # Should NOT have a tool_call chunk (parsing skipped in compact mode)
        tool_calls = [c for c in chunks if c.chunk_type == "tool_call"]
        assert len(tool_calls) == 0

    def test_compact_mode_intent_detection_marks_completed(
        self,
        storage_manager: StorageManager,
        sample_items: list[int],
    ) -> None:
        """Compact mode intent detection should handle 'I finished X'."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.conversation_model = "test-model"

        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"context": {"compact_mode": True}},
        )

        chunks = list(
            engine.process_message(
                user_id=1,
                message="I finished Outer Wilds",
                stream=True,
            )
        )

        # Should have a tool_call and tool_result (from intent detection)
        tool_calls = [c for c in chunks if c.chunk_type == "tool_call"]
        tool_results = [c for c in chunks if c.chunk_type == "tool_result"]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "mark_completed"
        assert len(tool_results) == 1
        assert tool_results[0].tool_result is not None
        assert tool_results[0].tool_result.success

        # LLM should NOT have been called
        mock_ollama.chat_stream.assert_not_called()
        mock_ollama.generate_text.assert_not_called()

        # Item should be marked completed
        item = storage_manager.get_content_item(sample_items[1], user_id=1)
        assert item is not None
        assert item.status == ConsumptionStatus.COMPLETED


class TestConversationConfig:
    """Tests for conversation config parameters."""

    def test_config_sets_temperature(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """Temperature from config is used in LLM calls."""
        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"llm": {"temperature": 0.3}},
        )

        assert engine.temperature == 0.3

        list(engine.process_message(user_id=1, message="test"))

        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        assert call_kwargs["temperature"] == 0.3

    def test_config_sets_context_window_size(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """Context window size from config is passed to LLM."""
        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"llm": {"context_window_size": 4096}},
        )

        assert engine.context_window_size == 4096

        list(engine.process_message(user_id=1, message="test"))

        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        assert call_kwargs["context_window_size"] == 4096

    def test_config_uses_conversation_model(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Conversation model from OllamaClient is passed to chat calls."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.conversation_model = "qwen2.5:3b"

        def mock_stream(*args, **kwargs):
            yield "response"

        mock_ollama.chat_stream.side_effect = mock_stream

        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        list(engine.process_message(user_id=1, message="test"))

        call_kwargs = mock_ollama.chat_stream.call_args.kwargs
        assert call_kwargs["model"] == "qwen2.5:3b"

    def test_config_defaults(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """Missing config values use sensible defaults."""
        engine = ConversationEngine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
        )

        assert engine.temperature == 0.7
        assert engine.max_tokens is None
        assert engine.context_window_size is None
        assert engine.compact_mode is False

    def test_factory_passes_conversation_config(
        self,
        storage_manager: StorageManager,
        mock_ollama: MagicMock,
    ) -> None:
        """create_conversation_engine passes config to engine."""
        engine = create_conversation_engine(
            storage_manager=storage_manager,
            ollama_client=mock_ollama,
            conversation_config={"context": {"compact_mode": True}},
        )

        assert engine.compact_mode is True


class TestBuildMessages:
    """Tests for _build_messages history sanitization."""

    def test_sanitizes_stored_history(
        self,
        conversation_engine: ConversationEngine,
    ) -> None:
        """Stored conversation history is sanitized before passing to LLM.

        Regression: raw message content could contain injection sequences
        (newlines, markdown headings) that would be replayed unsanitized
        into the LLM's multi-turn message array.
        """
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(
                    user_id=1,
                    role="user",
                    content="Normal question\n## INJECTED HEADING\nEvil instruction",
                ),
            ],
        )
        messages = conversation_engine._build_messages(context, "New question")

        # User history message should be sanitized — no markdown heading markers
        assert "## INJECTED" not in messages[0]["content"]
        assert "Normal question" in messages[0]["content"]
        # Current message sanitized via sanitize_prompt_text_long (500-char cap)
        assert "New question" in messages[-1]["content"]

    def test_current_message_sanitized(
        self,
        conversation_engine: ConversationEngine,
    ) -> None:
        """Current user message is sanitized to prevent prompt injection.

        Regression: live user input was passed through unsanitized, allowing
        newline-based injection while all stored history was sanitized.
        """
        context = ConversationContext(user_id=1)
        injected = "Normal question\n## INJECTED HEADING\nEvil instruction"
        messages = conversation_engine._build_messages(context, injected)

        assert "## INJECTED" not in messages[-1]["content"]
        assert "Normal question" in messages[-1]["content"]

    def test_current_message_preserves_normal_punctuation(
        self,
        conversation_engine: ConversationEngine,
    ) -> None:
        """Current message sanitization preserves colons, question marks, parens."""
        context = ConversationContext(user_id=1)
        normal = "What's the best RPG? (like Baldur's Gate: Enhanced Edition)"
        messages = conversation_engine._build_messages(context, normal)

        assert messages[-1]["content"] == normal

    def test_current_message_truncated_at_500_chars(
        self,
        conversation_engine: ConversationEngine,
    ) -> None:
        """Current message is capped at 500 chars by sanitize_prompt_text_long.

        The live user message uses a 500-char cap (more generous than the
        100-char cap on stored history) to accommodate normal conversation
        length while still bounding input.
        """
        context = ConversationContext(user_id=1)
        long_message = "A" * 600
        messages = conversation_engine._build_messages(context, long_message)

        assert len(messages[-1]["content"]) == 500
        assert messages[-1]["content"] == "A" * 500

    def test_preserves_assistant_history_unsanitized(
        self,
        conversation_engine: ConversationEngine,
    ) -> None:
        """Assistant messages in history are passed through as-is.

        The LLM's own responses contain markdown (bold, headers, bullets)
        that must be preserved for multi-turn coherence. Only user messages
        are sanitized against stored injection.
        """
        assistant_content = "## 🎯 YOUR NEXT GAME: **Outer Wilds**\n- Great exploration"
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(
                    user_id=1,
                    role="assistant",
                    content=assistant_content,
                ),
            ],
        )
        messages = conversation_engine._build_messages(context, "Thanks!")

        # Assistant message preserved as-is — markdown and structure intact
        assert messages[0]["content"] == assistant_content

    def test_stored_history_truncated_at_100_chars(
        self,
        conversation_engine: ConversationEngine,
    ) -> None:
        """Stored user messages are truncated to 100 chars by sanitize_prompt_text."""
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(user_id=1, role="user", content="A" * 200),
            ],
        )
        messages = conversation_engine._build_messages(context, "New question")
        assert len(messages[0]["content"]) == 100
