"""Tests for context assembly functionality."""

import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.conversation.context import (
    ContextAssembler,
    _extract_contributing_items,
    _format_item_compact,
    _format_item_detail,
    _format_recommendation_brief,
    _format_recommendation_brief_compact,
    _score_to_qualitative,
    build_user_context_block,
    build_user_context_block_compact,
)
from src.conversation.memory import MemoryManager
from src.llm.client import OllamaClient
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import (
    ConversationContext,
    ConversationMessage,
    CoreMemory,
    PreferenceProfile,
    RecommendationBrief,
)
from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.storage.vector_db import VectorDB


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

    def test_build_summary_from_inferred_memories_sanitizes(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Inferred memory text is sanitized in the observed patterns section."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Pattern\n## INJECTED",
            memory_type="inferred",
            source="rating_pattern",
            confidence=0.7,
        )

        summary = context_assembler._build_profile_summary(user_id=1)

        assert "Pattern" in summary
        assert "## INJECTED" not in summary
        assert "Observed patterns" in summary

    def test_build_summary_from_user_stated_memories_sanitizes(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """User-stated memory text is sanitized in the user preferences section."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Loves sci-fi\n## INJECTED",
            memory_type="user_stated",
            source="conversation",
        )

        summary = context_assembler._build_profile_summary(user_id=1)

        assert "Loves sci-fi" in summary
        assert "## INJECTED" not in summary
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

    def test_build_summary_from_profile_sanitizes_fields(
        self, context_assembler: ContextAssembler, memory_manager: MemoryManager
    ) -> None:
        """Profile fields are sanitized to prevent injection.

        Genre keys, themes, anti-preferences, and cross-media patterns
        all pass through sanitize_prompt_text before prompt injection.
        """
        profile = PreferenceProfile(
            user_id=1,
            genre_affinities={"sci-fi\n## INJECTED": 0.9},
            theme_preferences=["exploration\n## EVIL"],
            anti_preferences=["grinding\n## HACK"],
            cross_media_patterns=["pattern\n## PAYLOAD"],
        )
        memory_manager.save_preference_profile(profile)

        summary = context_assembler._build_profile_summary(user_id=1)

        assert "sci-fi" in summary
        assert "exploration" in summary
        assert "grinding" in summary
        assert "pattern" in summary
        assert "## INJECTED" not in summary
        assert "## EVIL" not in summary
        assert "## HACK" not in summary
        assert "## PAYLOAD" not in summary


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
        assert "5/5" in block
        assert "Recently Completed (High-Rated)" in block
        assert "ONLY reference items from THIS list" in block
        assert "Outer Wilds" in block
        assert "Available in Backlog — NOT YET CONSUMED" in block
        assert "Do NOT claim they enjoyed or experienced any of these" in block
        assert "[NOT YET CONSUMED]" in block

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

        # Should be truncated (sanitize_prompt_text_long caps at 200 chars)
        assert long_message not in block  # Full 500-char message should not be present
        assert "A" * 201 not in block  # Strictly capped — no 201-char run
        # But 200 chars should be present
        assert "A" * 200 in block

    def test_build_context_block_sanitizes_conversation_history(self) -> None:
        """Conversation history is sanitized to prevent prompt injection.

        Regression: message.content was injected verbatim into the system
        prompt, allowing multi-turn prompt injection via crafted messages.
        Sanitization strips structural markers (##, newlines) that enable
        injection.
        """
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(
                    user_id=1,
                    role="user",
                    content="Normal question\n## INJECTED HEADING",
                )
            ],
        )
        block = build_user_context_block(context)
        # Markdown heading markers stripped — no structural injection
        assert "## INJECTED" not in block
        assert "Normal question" in block

    def test_build_context_block_does_not_sanitize_assistant_messages(self) -> None:
        """Assistant messages skip sanitization but are truncated for token budget.

        The LLM's own responses contain markdown that must survive (no
        character stripping). Length is capped at 200 chars in full mode.
        """
        assistant_content = "## 🎯 **Outer Wilds** — great pick!"
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
        block = build_user_context_block(context)
        assert assistant_content in block

    def test_build_context_block_truncates_long_assistant_messages(self) -> None:
        """Assistant messages in full block are truncated to 200 chars for token budget."""
        assistant_content = "## Great pick! " + "A" * 200
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
        block = build_user_context_block(context)
        assert assistant_content not in block
        assert assistant_content[:200] in block

    def test_build_context_block_sanitizes_memory_text(self) -> None:
        """Memory text is sanitized to prevent prompt injection.

        Structural markers like newlines and markdown headings are stripped
        to prevent injecting new sections into the prompt.
        """
        context = ConversationContext(
            user_id=1,
            core_memories=[
                CoreMemory(
                    user_id=1,
                    memory_text="Loves sci-fi\n## INJECTED SECTION",
                    memory_type="user_stated",
                    source="conversation",
                ),
            ],
        )
        block = build_user_context_block(context)
        assert "Loves sci-fi" in block
        assert "## INJECTED" not in block

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

    def test_build_context_block_preserves_long_preference_summary(self) -> None:
        """Multi-field preference summaries are not truncated by the context block.

        Regression: sanitize_prompt_text (100-char cap) was applied to the
        assembled preference_summary, silently truncating multi-field profiles.
        Individual fields are sanitized at construction time; the assembled
        output must be passed through without re-sanitization.
        """
        long_summary = (
            "Top genres: sci-fi (4.5★), fantasy (4.2★)\n"
            "Preferred themes: exploration, mystery, survival\n"
            "Dislikes: grinding, microtransactions\n"
            "Cross-media patterns: enjoys book-to-game adaptations"
        )
        context = ConversationContext(user_id=1, preference_summary=long_summary)
        block = build_user_context_block(context)
        # All parts of the summary must survive — no 100-char truncation
        assert "sci-fi" in block
        assert "Cross-media patterns" in block
        assert "microtransactions" in block


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
        mock_ollama = MagicMock(spec=OllamaClient)
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


class TestSeriesOrderingInContext:
    """Tests for series ordering filtering in context assembly."""

    def test_excludes_later_series_entry_when_earlier_unread(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Book 3 should not appear in backlog when user has only read book 1.

        This prevents the LLM from recommending e.g. Abaddon's Gate (#3)
        when the user has only completed Leviathan Wakes (#1).
        """
        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
        )

        # User completed book 1
        storage_manager.save_content_item(
            ContentItem(
                id="expanse1",
                title="Leviathan Wakes (The Expanse, #1)",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=4,
            ),
            user_id=1,
        )

        # Books 2 and 3 are in the backlog
        storage_manager.save_content_item(
            ContentItem(
                id="expanse2",
                title="Caliban's War (The Expanse, #2)",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )
        storage_manager.save_content_item(
            ContentItem(
                id="expanse3",
                title="Abaddon's Gate (The Expanse, #3)",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What book should I read next?",
            content_type=ContentType.BOOK,
        )

        backlog_titles = [item.title for item in context.relevant_unconsumed]

        # Book 2 should be in backlog (next in sequence)
        assert any("Caliban" in title for title in backlog_titles)
        # Book 3 should NOT be in backlog (book 2 not read yet)
        assert not any("Abaddon" in title for title in backlog_titles)

    def test_includes_next_series_entry_after_completing_previous(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Book 2 should appear when user has completed book 1."""
        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
        )

        # User completed book 1
        storage_manager.save_content_item(
            ContentItem(
                id="series1",
                title="Fantasy Epic (The Saga, #1)",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            ),
            user_id=1,
        )

        # Book 2 in backlog
        storage_manager.save_content_item(
            ContentItem(
                id="series2",
                title="Fantasy Epic Returns (The Saga, #2)",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What next?",
            content_type=ContentType.BOOK,
        )

        backlog_titles = [item.title for item in context.relevant_unconsumed]
        assert any("Saga, #2" in title for title in backlog_titles)

    def test_non_series_items_unaffected_by_series_filtering(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Standalone items should always appear in backlog."""
        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
        )

        storage_manager.save_content_item(
            ContentItem(
                id="standalone",
                title="A Standalone Novel",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="Recommend a book",
            content_type=ContentType.BOOK,
        )

        backlog_titles = [item.title for item in context.relevant_unconsumed]
        assert "A Standalone Novel" in backlog_titles


class TestFormatItemDetail:
    """Tests for the _format_item_detail helper."""

    def test_includes_content_type_and_title(self) -> None:
        """Basic formatting includes content type label and title."""
        item = ContentItem(
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_detail(item)

        assert "[Video Game]" in result
        assert "Outer Wilds" in result

    def test_includes_author_and_rating(self) -> None:
        """Shows author and rating when available."""
        item = ContentItem(
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        result = _format_item_detail(item)

        assert "Frank Herbert" in result
        assert "5/5" in result

    def test_includes_genres_from_metadata(self) -> None:
        """Genres from metadata appear in brackets."""
        item = ContentItem(
            title="Hades",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["roguelike", "action", "indie"]},
        )
        result = _format_item_detail(item)

        assert "[roguelike, action, indie]" in result

    def test_truncates_long_genres_list(self) -> None:
        """Only first 4 genres are shown."""
        item = ContentItem(
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["a", "b", "c", "d", "e", "f"]},
        )
        result = _format_item_detail(item)

        assert "[a, b, c, d]" in result
        assert "e, f" not in result

    def test_includes_review_snippet(self) -> None:
        """Review text is included when available."""
        item = ContentItem(
            title="Firewatch",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            review="Beautiful storytelling in a gorgeous setting.",
        )
        result = _format_item_detail(item)

        assert 'Review: "Beautiful storytelling' in result

    def test_truncates_long_review(self) -> None:
        """Long reviews are truncated with ellipsis."""
        long_review = "A" * 200
        item = ContentItem(
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review=long_review,
        )
        result = _format_item_detail(item)

        assert "..." in result
        assert "A" * 200 not in result

    def test_no_ellipsis_when_sanitizer_strips_to_short(self) -> None:
        """Ellipsis is based on sanitized length, not raw length.

        Regression: old code compared raw review length, so a review with
        many stripped characters could show a false ellipsis even though
        the sanitized text was complete.
        """
        # 120 chars raw but ~50 after stripping special chars
        review = "Great" + "🎮" * 115
        item = ContentItem(
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review=review,
        )
        result = _format_item_detail(item)

        # Sanitized text is short, so no ellipsis should appear
        assert "..." not in result

    def test_no_ellipsis_when_review_is_exactly_100_chars(self) -> None:
        """A review that is exactly 100 chars after sanitization gets no ellipsis.

        Regression: using >= instead of checking actual truncation would
        falsely add ellipsis to naturally-100-char reviews.
        """
        review = "A" * 100  # Exactly 100 chars, no truncation needed
        item = ContentItem(
            title="Test",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review=review,
        )
        result = _format_item_detail(item)
        assert "..." not in result

    def test_omits_missing_optional_fields(self) -> None:
        """No author, rating, genres, or review when not set."""
        item = ContentItem(
            title="Mystery Item",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_detail(item)

        assert result == "- [Movie] Mystery Item"

    def test_backlog_tag_added_when_backlog_true(self) -> None:
        """[NOT YET CONSUMED] is prepended when backlog=True."""
        item = ContentItem(
            title="Mystery Item",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_detail(item, backlog=True)
        assert result == "- [NOT YET CONSUMED] [Movie] Mystery Item"

    def test_no_backlog_tag_by_default(self) -> None:
        """[NOT YET CONSUMED] is absent when backlog=False (default)."""
        item = ContentItem(
            title="Mystery Item",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_detail(item)
        assert "[NOT YET CONSUMED]" not in result


class TestPipelineBacklogIntegration:
    """Tests for using the recommendation pipeline to populate the backlog."""

    def test_uses_pipeline_when_engine_and_content_type_provided(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Backlog comes from the recommendation engine when available."""
        pipeline_item = ContentItem(
            title="Pipeline Pick",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        contributing_item = ContentItem(
            title="Firewatch",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )

        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            {
                "item": pipeline_item,
                "score": 0.9,
                "reasoning": "great match",
                "score_breakdown": {"genre_match": 0.85},
                "contributing_items": [contributing_item],
                "adaptations": [],
                "similarity_score": 0.8,
                "preference_score": 0.7,
            },
        ]

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            recommendation_engine=mock_engine,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What game?",
            content_type=ContentType.VIDEO_GAME,
        )

        backlog_titles = [item.title for item in context.relevant_unconsumed]
        assert "Pipeline Pick" in backlog_titles
        mock_engine.generate_recommendations.assert_called_once()

        # Briefs should be populated
        assert context.recommendation_briefs is not None
        assert len(context.recommendation_briefs) == 1
        assert context.recommendation_briefs[0].score == 0.9
        assert context.recommendation_briefs[0].reasoning == "great match"

        # Contributing items should populate relevant_completed (skipping RAG)
        completed_titles = [item.title for item in context.relevant_completed]
        assert "Firewatch" in completed_titles

    def test_pipeline_returns_single_top_pick(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Pipeline is called with limit=1 and the single result is used.

        Regression: sending multiple backlog items to the LLM causes it to
        reference alternatives as if the user consumed them. The fix passes
        limit=1 to the pipeline so only the top pick is scored and returned.
        """
        top_pick = ContentItem(
            title="Top Pick",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            {
                "item": top_pick,
                "score": 0.9,
                "reasoning": "best match",
                "score_breakdown": {},
                "contributing_items": [],
                "adaptations": [],
                "similarity_score": 0.7,
                "preference_score": 0.6,
            },
        ]

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            recommendation_engine=mock_engine,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What game?",
            content_type=ContentType.VIDEO_GAME,
        )

        assert context.recommendation_briefs is not None
        assert len(context.recommendation_briefs) == 1
        assert context.recommendation_briefs[0].item.title == "Top Pick"
        assert len(context.relevant_unconsumed) == 1

        # Verify pipeline was called with count=1 (single top pick)
        mock_engine.generate_recommendations.assert_called_once()
        assert mock_engine.generate_recommendations.call_args.kwargs["count"] == 1

    def test_pipeline_empty_results_yields_empty_context(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """When pipeline returns no candidates, both briefs and unconsumed are empty.

        Regression: ensure no IndexError or fallback to storage when pipeline
        returns [] (not None) — empty list is valid 'pipeline ran but nothing
        to recommend'.
        """
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            recommendation_engine=mock_engine,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What game?",
            content_type=ContentType.VIDEO_GAME,
        )

        assert context.recommendation_briefs == []
        assert context.relevant_unconsumed == []

    def test_falls_back_to_storage_when_no_engine(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Without a recommendation engine, raw storage query is used."""
        storage_manager.save_content_item(
            ContentItem(
                id="fallback1",
                title="Storage Item",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            # No recommendation_engine
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What book?",
            content_type=ContentType.BOOK,
        )

        backlog_titles = [item.title for item in context.relevant_unconsumed]
        assert "Storage Item" in backlog_titles

    def test_falls_back_to_storage_when_no_content_type(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """Pipeline requires a content_type; falls back without one."""
        storage_manager.save_content_item(
            ContentItem(
                id="any1",
                title="Any Type Item",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        mock_engine = MagicMock(spec=RecommendationEngine)
        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            recommendation_engine=mock_engine,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="Recommend anything",
            content_type=None,  # No content type
        )

        backlog_titles = [item.title for item in context.relevant_unconsumed]
        assert "Any Type Item" in backlog_titles
        # Pipeline should NOT have been called
        mock_engine.generate_recommendations.assert_not_called()

    def test_falls_back_to_storage_when_pipeline_errors(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """If the pipeline raises, falls back gracefully to storage."""
        storage_manager.save_content_item(
            ContentItem(
                id="safe1",
                title="Fallback Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.side_effect = RuntimeError("boom")

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            recommendation_engine=mock_engine,
        )

        context = assembler.assemble_context(
            user_id=1,
            user_query="What game?",
            content_type=ContentType.VIDEO_GAME,
        )

        # Should still return items via fallback
        backlog_titles = [item.title for item in context.relevant_unconsumed]
        assert "Fallback Game" in backlog_titles
        # Briefs should be None on pipeline failure
        assert context.recommendation_briefs is None


class TestScoreToQualitative:
    """Tests for _score_to_qualitative threshold boundaries."""

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (1.0, "Excellent fit"),
            (0.85, "Excellent fit"),
            (0.84, "Strong fit"),
            (0.70, "Strong fit"),
            (0.69, "Good fit"),
            (0.55, "Good fit"),
            (0.54, "Decent fit"),
            (0.40, "Decent fit"),
            (0.39, "Worth considering"),
            (0.0, "Worth considering"),
        ],
    )
    def test_threshold_boundaries(self, score: float, expected: str) -> None:
        """Each threshold boundary maps to the correct qualitative label."""
        assert _score_to_qualitative(score) == expected


class TestRecommendationBriefFormatting:
    """Tests for _format_recommendation_brief and enriched context blocks."""

    @pytest.fixture
    def sample_brief(self) -> RecommendationBrief:
        """Create a sample recommendation brief for testing."""
        return RecommendationBrief(
            item=ContentItem(
                title="Outer Wilds",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                metadata={"genres": ["exploration", "puzzle", "space"]},
            ),
            score=0.87,
            reasoning="Recommended because you liked Firewatch, Subnautica",
            score_breakdown={
                "genre_match": 0.92,
                "tag_overlap": 0.85,
                "rating_pattern": 0.7,
            },
            contributing_items=[
                ContentItem(
                    title="Firewatch",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.COMPLETED,
                    rating=4,
                ),
            ],
            adaptations=[
                ContentItem(
                    title="The Martian",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                ),
            ],
            similarity_score=0.8,
            preference_score=0.75,
        )

    def test_format_brief_includes_qualitative_fit(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Qualitative fit label is rendered from score (no raw percentage)."""
        result = _format_recommendation_brief(sample_brief)
        assert "Fit: Excellent fit" in result
        assert "87%" not in result

    def test_format_brief_includes_title_and_type(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Title, content type tag, and NOT YET CONSUMED prefix are present."""
        result = _format_recommendation_brief(sample_brief)
        assert "[Video Game]" in result
        assert "Outer Wilds" in result
        assert result.startswith("- [NOT YET CONSUMED]")

    def test_format_brief_includes_reasoning(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Pipeline reasoning is rendered."""
        result = _format_recommendation_brief(sample_brief)
        assert "Why: Recommended because you liked Firewatch, Subnautica" in result

    def test_format_brief_excludes_raw_scores(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Raw score dimensions are NOT shown (replaced by qualitative labels)."""
        result = _format_recommendation_brief(sample_brief)
        assert "Strengths:" not in result
        assert "genre_match" not in result
        assert "92%" not in result

    def test_format_brief_includes_cross_media(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Cross-media adaptations are shown."""
        result = _format_recommendation_brief(sample_brief)
        assert "Cross-media:" in result
        assert "The Martian" in result

    def test_format_brief_sanitizes_adaptation_title(self) -> None:
        """Adaptation titles are sanitized to prevent injection."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.8,
            reasoning="",
            score_breakdown={},
            contributing_items=[],
            adaptations=[
                ContentItem(
                    title="Adaptation\n## INJECTED",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            ],
        )
        result = _format_recommendation_brief(brief)
        assert "Cross-media:" in result
        assert "Adaptation" in result
        assert "## INJECTED" not in result

    def test_format_brief_includes_genres(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Genres from item metadata appear in the output."""
        result = _format_recommendation_brief(sample_brief)
        assert "[exploration, puzzle, space]" in result

    def test_format_brief_minimal(self) -> None:
        """Brief with minimal data still formats cleanly."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Minimal Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.5,
            reasoning="",
            score_breakdown={},
            contributing_items=[],
            adaptations=[],
        )
        result = _format_recommendation_brief(brief)
        assert "Minimal Game" in result
        assert "Fit: Decent fit" in result
        assert "50%" not in result
        # No reasoning or cross-media sections
        assert "Why:" not in result
        assert "Cross-media:" not in result

    def test_format_brief_sanitizes_reasoning(self) -> None:
        """Reasoning text is sanitized before inclusion in prompt.

        Regression: unsanitized reasoning could contain newlines or injection
        sequences that break the prompt structure.
        """
        brief = RecommendationBrief(
            item=ContentItem(
                title="Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.8,
            reasoning="Great game\n## INJECTED HEADING\nMore text",
            score_breakdown={},
            contributing_items=[],
            adaptations=[],
        )
        result = _format_recommendation_brief(brief)
        # Structural markers stripped — newlines and markdown headings removed
        assert "## INJECTED" not in result
        assert "Great game" in result

    def test_format_brief_compact_sanitizes_reasoning(self) -> None:
        """Compact reasoning text is sanitized before inclusion in prompt."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.8,
            reasoning="Great game\n## INJECTED HEADING",
            score_breakdown={},
            contributing_items=[],
            adaptations=[],
        )
        result = _format_recommendation_brief_compact(brief)
        assert "## INJECTED" not in result
        assert "Great game" in result

    def test_context_block_renders_enriched_section_with_briefs(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """build_user_context_block uses enriched format when briefs present."""
        context = ConversationContext(
            user_id=1,
            recommendation_briefs=[sample_brief],
            relevant_unconsumed=[sample_brief.item],
        )
        block = build_user_context_block(context)
        assert "YOUR RECOMMENDATION" in block
        assert "NOT YET CONSUMED" in block
        assert "Fit: Excellent fit" in block
        # Should NOT show the plain backlog header
        assert "Available in Backlog" not in block

    def test_context_block_falls_back_to_plain_without_briefs(self) -> None:
        """build_user_context_block uses plain format when no briefs."""
        context = ConversationContext(
            user_id=1,
            relevant_unconsumed=[
                ContentItem(
                    title="Plain Item",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
            ],
        )
        block = build_user_context_block(context)
        assert "Available in Backlog — NOT YET CONSUMED" in block
        assert "Do NOT claim they enjoyed or experienced any of these" in block
        assert "[NOT YET CONSUMED]" in block
        assert "Recommended From Backlog" not in block


class TestExtractContributingItems:
    """Tests for _extract_contributing_items helper."""

    def test_deduplicates_across_briefs(self) -> None:
        """Contributing items shared across briefs are deduplicated."""
        shared_item = ContentItem(
            id="shared1",
            title="Shared Item",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        unique_item = ContentItem(
            id="unique1",
            title="Unique Item",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        )

        briefs = [
            RecommendationBrief(
                item=ContentItem(
                    title="Rec A",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
                score=0.9,
                reasoning="",
                score_breakdown={},
                contributing_items=[shared_item, unique_item],
                adaptations=[],
            ),
            RecommendationBrief(
                item=ContentItem(
                    title="Rec B",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
                score=0.8,
                reasoning="",
                score_breakdown={},
                contributing_items=[shared_item],  # Duplicate
                adaptations=[],
            ),
        ]

        result = _extract_contributing_items(briefs)
        assert len(result) == 2
        titles = [item.title for item in result]
        assert "Shared Item" in titles
        assert "Unique Item" in titles

    def test_respects_limit(self) -> None:
        """Returned list is capped at the limit."""
        items = [
            ContentItem(
                id=f"item{index}",
                title=f"Item {index}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
            for index in range(20)
        ]
        brief = RecommendationBrief(
            item=ContentItem(
                title="Rec",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.9,
            reasoning="",
            score_breakdown={},
            contributing_items=items,
            adaptations=[],
        )
        result = _extract_contributing_items([brief], limit=5)
        assert len(result) == 5

    def test_empty_briefs(self) -> None:
        """Empty brief list returns empty list."""
        result = _extract_contributing_items([])
        assert result == []

    def test_excludes_non_completed_contributing_items(self) -> None:
        """Contributing items that are not COMPLETED are excluded.

        Regression: pipeline contributing_items can include backlog/in-progress
        items. Including them in relevant_completed would let the LLM claim the
        user enjoyed them — they haven't finished them yet.
        """
        completed_item = ContentItem(
            id="completed1",
            title="Finished Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        unread_item = ContentItem(
            id="unread1",
            title="Backlog Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        brief = RecommendationBrief(
            item=ContentItem(
                title="Rec",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.9,
            reasoning="",
            score_breakdown={},
            contributing_items=[completed_item, unread_item],
            adaptations=[],
        )
        result = _extract_contributing_items([brief])
        assert len(result) == 1
        assert result[0].title == "Finished Game"
        assert all(item.status == ConsumptionStatus.COMPLETED for item in result)


class TestRAGBypassWithPipeline:
    """Tests verifying that RAG embedding call is skipped when pipeline is active."""

    def test_no_embedding_call_when_pipeline_provides_briefs(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """generate_embedding should NOT be called when pipeline succeeds.

        The pipeline's contributing_items replace the RAG lookup, saving
        the 1-3s embedding generation.
        """
        pipeline_item = ContentItem(
            title="Pipeline Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            {
                "item": pipeline_item,
                "score": 0.85,
                "reasoning": "matches your taste",
                "score_breakdown": {},
                "contributing_items": [],
                "adaptations": [],
                "similarity_score": 0.7,
                "preference_score": 0.6,
            },
        ]

        mock_ollama = MagicMock(spec=OllamaClient)

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            ollama_client=mock_ollama,
            recommendation_engine=mock_engine,
        )

        assembler.assemble_context(
            user_id=1,
            user_query="What game should I play?",
            content_type=ContentType.VIDEO_GAME,
        )

        # The key assertion: generate_embedding must NOT have been called
        mock_ollama.generate_embedding.assert_not_called()


class TestCompactFormatting:
    """Tests for compact formatting functions."""

    def test_format_item_compact_basic(self) -> None:
        """Compact item format includes type, title, author, rating."""
        item = ContentItem(
            title="The Martian",
            author="Andy Weir",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        result = _format_item_compact(item)

        assert "[Book]" in result
        assert "The Martian" in result
        assert "Andy Weir" in result
        assert "5/5" in result

    def test_format_item_compact_no_genres_or_review(self) -> None:
        """Compact format omits genres and review even when present."""
        item = ContentItem(
            title="Hades",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            review="Amazing roguelike",
            metadata={"genres": ["roguelike", "action"]},
        )
        result = _format_item_compact(item)

        assert "roguelike" not in result
        assert "Amazing" not in result
        assert "Hades" in result
        assert "5/5" in result

    def test_format_item_compact_minimal(self) -> None:
        """Compact format with no author or rating."""
        item = ContentItem(
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_compact(item)

        assert result == "- [Video Game] Outer Wilds"

    def test_format_item_compact_backlog_tag(self) -> None:
        """Compact format prepends [NOT YET CONSUMED] when backlog=True."""
        item = ContentItem(
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_compact(item, backlog=True)
        assert result == "- [NOT YET CONSUMED] [Video Game] Outer Wilds"

    def test_format_item_compact_no_backlog_tag_by_default(self) -> None:
        """Compact format omits [NOT YET CONSUMED] by default."""
        item = ContentItem(
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_compact(item)
        assert "[NOT YET CONSUMED]" not in result

    def test_format_item_compact_sanitizes_title_and_author(self) -> None:
        """Compact format sanitizes title and author to prevent injection.

        Regression: title and author were interpolated verbatim into the
        prompt, allowing injection via crafted metadata fields.
        """
        item = ContentItem(
            title="Good Game\n## INJECTED HEADING",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            author="Evil Dev\n## MORE INJECTION",
        )
        result = _format_item_compact(item)
        assert "## INJECTED" not in result
        assert "## MORE INJECTION" not in result
        assert "Good Game" in result
        assert "Evil Dev" in result

    def test_format_recommendation_brief_compact(self) -> None:
        """Compact brief includes title, qualitative fit, and short reasoning."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Outer Wilds",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.87,
            reasoning="Recommended because you liked Firewatch and Subnautica",
            score_breakdown={"genre_match": 0.92},
            contributing_items=[],
            adaptations=[],
        )
        result = _format_recommendation_brief_compact(brief)

        assert "Outer Wilds" in result
        assert "Excellent fit" in result
        assert "87%" not in result
        assert "Firewatch" in result
        assert "[NOT YET CONSUMED]" in result
        # Should NOT contain score breakdown
        assert "genre_match" not in result
        assert "92%" not in result

    def test_format_recommendation_brief_compact_no_reasoning(self) -> None:
        """Compact brief without reasoning is single line."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Minimal",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.5,
            reasoning="",
            score_breakdown={},
            contributing_items=[],
            adaptations=[],
        )
        result = _format_recommendation_brief_compact(brief)

        assert "Minimal" in result
        assert "Decent fit" in result
        assert "50%" not in result
        assert "\n" not in result

    def test_format_brief_compact_truncates_long_reasoning(self) -> None:
        """Compact brief reasoning is capped at 100 chars by sanitize_prompt_text."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.8,
            reasoning="A" * 150,
            score_breakdown={},
            contributing_items=[],
            adaptations=[],
        )
        result = _format_recommendation_brief_compact(brief)
        assert "A" * 101 not in result
        assert "A" * 100 in result


class TestBuildUserContextBlockCompact:
    """Tests for the compact user context block builder."""

    def test_compact_block_limits_memories(self) -> None:
        """Compact block caps memories at 5."""
        memories = [
            CoreMemory(
                user_id=1,
                memory_text=f"Memory {index}",
                memory_type="user_stated",
                source="conversation",
            )
            for index in range(10)
        ]
        context = ConversationContext(user_id=1, core_memories=memories)
        block = build_user_context_block_compact(context)

        # Should have exactly 5 memory lines
        memory_lines = [
            line for line in block.split("\n") if line.startswith("- Memory")
        ]
        assert len(memory_lines) == 5

    def test_compact_block_limits_completed_items(self) -> None:
        """Compact block caps completed items at 5."""
        items = [
            ContentItem(
                title=f"Item {index}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
            for index in range(10)
        ]
        context = ConversationContext(user_id=1, relevant_completed=items)
        block = build_user_context_block_compact(context)

        item_lines = [line for line in block.split("\n") if line.startswith("- [Book]")]
        assert len(item_lines) == 5

    def test_compact_block_limits_conversation_history(self) -> None:
        """Compact block shows last 3 messages, truncated to 100 chars."""
        messages = [
            ConversationMessage(
                user_id=1,
                role="user" if index % 2 == 0 else "assistant",
                content=f"Message {index} " + "A" * 200,
            )
            for index in range(6)
        ]
        context = ConversationContext(user_id=1, recent_messages=messages)
        block = build_user_context_block_compact(context)

        # Should have exactly 3 message lines
        message_lines = [
            line
            for line in block.split("\n")
            if line.startswith("User:") or line.startswith("You:")
        ]
        assert len(message_lines) == 3

        # User messages: sanitize_prompt_text (100-char cap)
        # Assistant messages: raw [:100] slice (no sanitization, only length cap)
        for line in message_lines:
            # "User: " (6 chars) or "You: " (5 chars) + 100-char content
            assert len(line) <= 106

    def test_compact_block_uses_compact_section_headers(self) -> None:
        """Compact block uses shorter section headers."""
        context = ConversationContext(
            user_id=1,
            core_memories=[
                CoreMemory(
                    user_id=1,
                    memory_text="Test",
                    memory_type="user_stated",
                    source="conversation",
                )
            ],
            relevant_completed=[
                ContentItem(
                    title="Test Book",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                )
            ],
        )
        block = build_user_context_block_compact(context)

        assert "## Preferences" in block
        assert "## Completed" in block
        # Should NOT use full-mode headers
        assert "Key Preferences & Memories" not in block
        assert "Recently Completed (High-Rated)" not in block

    def test_compact_block_with_briefs_uses_compact_format(self) -> None:
        """Compact block uses compact brief format."""
        brief = RecommendationBrief(
            item=ContentItem(
                title="Test Game",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
            score=0.85,
            reasoning="great match",
            score_breakdown={"genre_match": 0.9},
            contributing_items=[],
            adaptations=[],
        )
        context = ConversationContext(
            user_id=1,
            recommendation_briefs=[brief],
            relevant_unconsumed=[brief.item],
        )
        block = build_user_context_block_compact(context)

        assert "## Your Pick (NOT YET CONSUMED)" in block
        assert "Excellent fit" in block
        assert "85%" not in block

    def test_compact_block_backlog_without_briefs_uses_not_yet_consumed(
        self,
    ) -> None:
        """Fallback backlog path uses 'NOT YET CONSUMED' header and item tags."""
        context = ConversationContext(
            user_id=1,
            relevant_unconsumed=[
                ContentItem(
                    title="Unplayed Game",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.UNREAD,
                )
            ],
        )
        block = build_user_context_block_compact(context)
        assert "## Backlog (NOT YET CONSUMED)" in block
        assert "[NOT YET CONSUMED]" in block
        assert "Unplayed Game" in block

    def test_compact_block_sanitizes_conversation_history(self) -> None:
        """Compact block sanitizes conversation history to prevent injection."""
        context = ConversationContext(
            user_id=1,
            recent_messages=[
                ConversationMessage(
                    user_id=1,
                    role="user",
                    content="Normal question\n## INJECTED HEADING",
                )
            ],
        )
        block = build_user_context_block_compact(context)
        assert "## INJECTED" not in block
        assert "Normal question" in block

    def test_compact_block_does_not_sanitize_assistant_messages(self) -> None:
        """Compact block skips sanitization on assistant messages but caps length.

        The LLM's own output contains markdown that must survive character
        stripping. Length is capped at 100 chars in compact mode.
        """
        assistant_content = "## 🎯 **Outer Wilds** — great pick!"
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
        block = build_user_context_block_compact(context)
        assert assistant_content in block

    def test_compact_block_truncates_long_assistant_messages(self) -> None:
        """Assistant messages in compact block are truncated to 100 chars for token budget."""
        assistant_content = "## Great pick! " + "A" * 200
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
        block = build_user_context_block_compact(context)
        assert assistant_content not in block
        assert assistant_content[:100] in block

    def test_compact_block_sanitizes_memory_text(self) -> None:
        """Compact block sanitizes memory text to prevent injection."""
        context = ConversationContext(
            user_id=1,
            core_memories=[
                CoreMemory(
                    user_id=1,
                    memory_text="Loves sci-fi\n## INJECTED SECTION",
                    memory_type="user_stated",
                    source="conversation",
                ),
            ],
        )
        block = build_user_context_block_compact(context)
        assert "Loves sci-fi" in block
        assert "## INJECTED" not in block

    def test_compact_block_preserves_long_preference_summary(self) -> None:
        """Multi-field preference summaries are not truncated in compact block.

        Regression: sanitize_prompt_text (100-char cap) was applied to the
        assembled summary, silently truncating multi-field profiles. Fields
        are sanitized at construction time; no re-sanitization needed.
        """
        long_summary = (
            "Top genres: sci-fi (4.5★), fantasy (4.2★)\n"
            "Preferred themes: exploration, mystery, survival\n"
            "Dislikes: grinding, microtransactions"
        )
        context = ConversationContext(user_id=1, preference_summary=long_summary)
        block = build_user_context_block_compact(context)
        assert "sci-fi" in block
        assert "microtransactions" in block

    def test_embedding_call_used_when_no_pipeline(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """generate_embedding IS called when there is no pipeline."""
        mock_ollama = MagicMock(spec=OllamaClient)
        mock_ollama.generate_embedding.return_value = [0.1] * 384

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            ollama_client=mock_ollama,
            # No recommendation_engine
        )

        # Need vector_db to trigger the embedding path
        storage_manager.vector_db = MagicMock(spec=VectorDB)
        storage_manager.vector_db.search_similar = MagicMock(return_value=[])
        storage_manager.search_similar = MagicMock(return_value=[])

        assembler.assemble_context(
            user_id=1,
            user_query="What should I read?",
            content_type=ContentType.BOOK,
        )

        mock_ollama.generate_embedding.assert_called_once()
