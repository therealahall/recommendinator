"""Tests for context assembly functionality."""

import tempfile
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.conversation.context import (
    ContextAssembler,
    _extract_contributing_items,
    _format_item_detail,
    _format_recommendation_brief,
    build_user_context_block,
)
from src.conversation.memory import MemoryManager
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import (
    ConversationContext,
    ConversationMessage,
    CoreMemory,
    PreferenceProfile,
    RecommendationBrief,
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
        assert "5/5" in block
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

    def test_omits_missing_optional_fields(self) -> None:
        """No author, rating, genres, or review when not set."""
        item = ContentItem(
            title="Mystery Item",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        result = _format_item_detail(item)

        assert result == "- [Movie] Mystery Item"


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

        mock_engine = MagicMock()
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

        mock_engine = MagicMock()
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

        mock_engine = MagicMock()
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

    def test_format_brief_includes_match_percent(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Match percentage is rendered from score."""
        result = _format_recommendation_brief(sample_brief)
        assert "Match: 87%" in result

    def test_format_brief_includes_title_and_type(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Title and content type appear in the output."""
        result = _format_recommendation_brief(sample_brief)
        assert "[Video Game]" in result
        assert "Outer Wilds" in result

    def test_format_brief_includes_reasoning(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Pipeline reasoning is rendered."""
        result = _format_recommendation_brief(sample_brief)
        assert "Why: Recommended because you liked Firewatch, Subnautica" in result

    def test_format_brief_includes_top_strengths(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Top scoring dimensions are shown."""
        result = _format_recommendation_brief(sample_brief)
        assert "Strengths:" in result
        assert "genre_match: 92%" in result

    def test_format_brief_includes_cross_media(
        self, sample_brief: RecommendationBrief
    ) -> None:
        """Cross-media adaptations are shown."""
        result = _format_recommendation_brief(sample_brief)
        assert "Cross-media:" in result
        assert "The Martian" in result

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
        assert "Match: 50%" in result
        # No reasoning, strengths, or cross-media sections
        assert "Why:" not in result
        assert "Strengths:" not in result
        assert "Cross-media:" not in result

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
        assert "Recommended From Backlog (Pre-Scored)" in block
        assert "Match: 87%" in block
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
        assert "Available in Backlog" in block
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

        mock_engine = MagicMock()
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

        mock_ollama = MagicMock()

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

    def test_embedding_call_used_when_no_pipeline(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
    ) -> None:
        """generate_embedding IS called when there is no pipeline."""
        mock_ollama = MagicMock()
        mock_ollama.generate_embedding.return_value = [0.1] * 384

        assembler = ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=memory_manager,
            ollama_client=mock_ollama,
            # No recommendation_engine
        )

        # Need vector_db to trigger the embedding path
        storage_manager.vector_db = MagicMock()
        storage_manager.vector_db.search_similar = MagicMock(return_value=[])
        storage_manager.search_similar = MagicMock(return_value=[])

        assembler.assemble_context(
            user_id=1,
            user_query="What should I read?",
            content_type=ContentType.BOOK,
        )

        mock_ollama.generate_embedding.assert_called_once()
