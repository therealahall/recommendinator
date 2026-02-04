"""Tests for context assembly functionality."""

import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.conversation.context import ContextAssembler, build_user_context_block
from src.conversation.memory import MemoryManager
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import (
    ConversationContext,
    ConversationMessage,
    CoreMemory,
    PreferenceProfile,
)
from src.storage.manager import StorageManager


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def memory_manager(storage_manager: StorageManager) -> MemoryManager:
    """Create a memory manager for testing."""
    return MemoryManager(storage_manager)


@pytest.fixture
def context_assembler(
    storage_manager: StorageManager, memory_manager: MemoryManager
) -> ContextAssembler:
    """Create a context assembler for testing."""
    return ContextAssembler(
        storage_manager=storage_manager,
        memory_manager=memory_manager,
        ollama_client=None,  # No LLM for unit tests
    )


@pytest.fixture
def sample_items(storage_manager: StorageManager) -> list[ContentItem]:
    """Create sample content items for testing."""
    items = [
        ContentItem(
            id="book1",
            title="The Martian",
            author="Andy Weir",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="book2",
            title="Project Hail Mary",
            author="Andy Weir",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
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
        ContentItem(
            id="book3",
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]
    for item in items:
        storage_manager.save_content_item(item, user_id=1)
    return items


class TestContextAssembler:
    """Tests for the ContextAssembler class."""

    def test_assemble_empty_context(self, context_assembler: ContextAssembler) -> None:
        """Test assembling context when no data exists."""
        context = context_assembler.assemble_context(
            user_id=1, user_query="What should I read next?"
        )

        assert context.user_id == 1
        assert context.core_memories == []
        assert context.recent_messages == []
        assert context.relevant_completed == []
        assert context.relevant_unconsumed == []

    def test_assemble_context_includes_memories(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Test that context includes core memories."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="I prefer sci-fi books",
            memory_type="user_stated",
            source="conversation",
        )
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Tends to enjoy exploration games",
            memory_type="inferred",
            source="rating_pattern",
            confidence=0.8,
        )

        context = context_assembler.assemble_context(
            user_id=1, user_query="What should I play?"
        )

        assert len(context.core_memories) == 2
        assert any(
            m.memory_text == "I prefer sci-fi books" for m in context.core_memories
        )

    def test_assemble_context_includes_conversation_history(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Test that context includes recent conversation history."""
        memory_manager.save_conversation_message(
            user_id=1, role="user", content="Hi there"
        )
        memory_manager.save_conversation_message(
            user_id=1, role="assistant", content="Hello! How can I help?"
        )

        context = context_assembler.assemble_context(
            user_id=1, user_query="What game next?"
        )

        assert len(context.recent_messages) == 2

    def test_assemble_context_includes_completed_items(
        self, context_assembler: ContextAssembler, sample_items: list[ContentItem]
    ) -> None:
        """Test that context includes high-rated completed items."""
        context = context_assembler.assemble_context(
            user_id=1, user_query="What book should I read?"
        )

        # Should include completed items with high ratings
        assert len(context.relevant_completed) > 0
        assert all(
            item.status == ConsumptionStatus.COMPLETED
            for item in context.relevant_completed
        )

    def test_assemble_context_includes_unconsumed_items(
        self, context_assembler: ContextAssembler, sample_items: list[ContentItem]
    ) -> None:
        """Test that context includes unconsumed items from backlog."""
        context = context_assembler.assemble_context(
            user_id=1, user_query="What game should I try?"
        )

        assert len(context.relevant_unconsumed) > 0
        assert all(
            item.status == ConsumptionStatus.UNREAD
            for item in context.relevant_unconsumed
        )

    def test_assemble_context_filters_by_content_type(
        self, context_assembler: ContextAssembler, sample_items: list[ContentItem]
    ) -> None:
        """Test that context can filter by content type."""
        context = context_assembler.assemble_context(
            user_id=1,
            user_query="What should I read?",
            content_type=ContentType.BOOK,
        )

        # All unconsumed should be books
        for item in context.relevant_unconsumed:
            assert item.content_type == ContentType.BOOK

    def test_assemble_context_respects_limits(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Test that context respects maximum limits."""
        # Create many memories
        for i in range(25):
            memory_manager.save_core_memory(
                user_id=1,
                memory_text=f"Memory {i}",
                memory_type="user_stated",
                source="manual",
            )

        context = context_assembler.assemble_context(
            user_id=1, user_query="test", max_memories=10
        )

        assert len(context.core_memories) <= 10

    def test_assemble_context_excludes_ignored_items(
        self,
        context_assembler: ContextAssembler,
        storage_manager: StorageManager,
    ) -> None:
        """Test that ignored items are excluded from unconsumed."""
        # Create an unread item
        item = ContentItem(
            id="ignored_book",
            title="Ignored Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = storage_manager.save_content_item(item, user_id=1)

        # Mark it as ignored
        storage_manager.set_item_ignored(db_id, ignored=True, user_id=1)

        context = context_assembler.assemble_context(user_id=1, user_query="What book?")

        # Should not include ignored item
        assert not any(
            item.title == "Ignored Book" for item in context.relevant_unconsumed
        )


class TestBuildProfileSummary:
    """Tests for profile summary building."""

    def test_build_summary_no_profile(
        self, context_assembler: ContextAssembler
    ) -> None:
        """Test building summary when no profile exists."""
        summary = context_assembler._build_profile_summary(user_id=1)
        assert "No preference profile available" in summary

    def test_build_summary_from_memories(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Test building summary from core memories."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Loves sci-fi",
            memory_type="user_stated",
            source="conversation",
        )

        summary = context_assembler._build_profile_summary(user_id=1)

        assert "Loves sci-fi" in summary
        assert "User preferences" in summary

    def test_build_summary_from_profile(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Test building summary from saved profile."""
        profile = PreferenceProfile(
            user_id=1,
            genre_affinities={"sci-fi": 0.9, "fantasy": 0.7},
            theme_preferences=["exploration", "narrative"],
            anti_preferences=["grinding"],
        )
        memory_manager.save_preference_profile(profile)

        summary = context_assembler._build_profile_summary(user_id=1)

        assert "sci-fi" in summary
        assert "exploration" in summary


class TestBuildUserContextBlock:
    """Tests for the build_user_context_block function."""

    def test_build_context_block_empty(self) -> None:
        """Test building context block with empty context."""
        context = ConversationContext(user_id=1)
        block = build_user_context_block(context)
        # Should not crash, may have minimal content
        assert isinstance(block, str)

    def test_build_context_block_with_memories(self) -> None:
        """Test building context block with memories."""
        context = ConversationContext(
            user_id=1,
            core_memories=[
                CoreMemory(
                    user_id=1,
                    memory_text="Prefers short games",
                    memory_type="user_stated",
                    source="conversation",
                ),
                CoreMemory(
                    user_id=1,
                    memory_text="Abandons grinding games",
                    memory_type="inferred",
                    source="rating_pattern",
                    confidence=0.8,
                ),
            ],
        )

        block = build_user_context_block(context)

        assert "Key Preferences" in block
        assert "[stated]" in block
        assert "[observed]" in block
        assert "Prefers short games" in block

    def test_build_context_block_with_items(self) -> None:
        """Test building context block with content items."""
        context = ConversationContext(
            user_id=1,
            relevant_completed=[
                ContentItem(
                    title="The Martian",
                    author="Andy Weir",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                )
            ],
            relevant_unconsumed=[
                ContentItem(
                    title="Outer Wilds",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.UNREAD,
                )
            ],
        )

        block = build_user_context_block(context)

        assert "The Martian" in block
        assert "Andy Weir" in block
        assert "(5/5)" in block
        assert "Outer Wilds" in block
        assert "Backlog" in block

    def test_build_context_block_with_conversation_history(self) -> None:
        """Test building context block with conversation history."""
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(
                    user_id=1, role="user", content="What should I play?"
                ),
                ConversationMessage(
                    user_id=1, role="assistant", content="Based on your taste..."
                ),
            ],
        )

        block = build_user_context_block(context)

        assert "Recent Conversation" in block
        assert "User:" in block
        assert "Assistant:" in block

    def test_build_context_block_truncates_long_messages(self) -> None:
        """Test that long messages are truncated."""
        long_message = "A" * 500
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(user_id=1, role="user", content=long_message)
            ],
        )

        block = build_user_context_block(context)

        # Should be truncated to ~200 chars + "..."
        assert "..." in block
        assert long_message not in block  # Full message should not be present

    def test_build_context_block_with_preference_summary(self) -> None:
        """Test building context block with preference summary."""
        context = ConversationContext(
            user_id=1,
            preference_summary="Top genres: sci-fi (90%), fantasy (70%)\nDislikes: grinding",
        )

        block = build_user_context_block(context)

        assert "User Profile" in block
        assert "sci-fi" in block
        assert "grinding" in block


class TestRAGRetrieval:
    """Tests for RAG retrieval functionality."""

    def test_fallback_to_high_rated_without_ollama(
        self, context_assembler: ContextAssembler, sample_items: list[ContentItem]
    ) -> None:
        """Test that retrieval falls back to high-rated items without Ollama."""
        # Without ollama_client, should use fallback
        relevant = context_assembler._retrieve_relevant_items(
            query="space exploration", user_id=1, limit=5
        )

        # Should still get completed items
        assert len(relevant) > 0
        assert all(item.rating and item.rating >= 4 for item in relevant)

    def test_rag_with_mocked_ollama(
        self, storage_manager: StorageManager, memory_manager: MemoryManager
    ) -> None:
        """Test RAG retrieval with mocked Ollama client."""
        # Create mock Ollama client
        mock_ollama = MagicMock()
        mock_ollama.generate_embedding.return_value = [0.1] * 384

        # Create assembler with mock
        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            ollama_client=mock_ollama,
        )

        # Add a completed item
        item = ContentItem(
            id="test_item",
            title="Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        storage_manager.save_content_item(item, user_id=1)

        # Should call generate_embedding but fall back since no vector_db
        relevant = assembler._retrieve_relevant_items(
            query="test query", user_id=1, limit=5
        )

        # Without vector_db, will use fallback
        assert isinstance(relevant, list)
