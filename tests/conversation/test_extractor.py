"""Tests for memory extraction from conversations."""

from unittest.mock import MagicMock

import pytest

from src.conversation.extractor import (
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
    MemoryExtractor,
)
from src.models.conversation import ConversationMessage


@pytest.fixture
def mock_ollama() -> MagicMock:
    """Create a mock Ollama client."""
    return MagicMock()


@pytest.fixture
def extractor(mock_ollama: MagicMock) -> MemoryExtractor:
    """Create a memory extractor for testing."""
    return MemoryExtractor(ollama_client=mock_ollama)


class TestMemoryExtraction:
    """Tests for extract_from_conversation."""

    def test_extract_user_stated_preference(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test extracting an explicitly stated user preference."""
        mock_ollama.generate_text.return_value = """[
            {
                "memory_text": "Prefers sci-fi games over fantasy",
                "memory_type": "user_stated",
                "confidence": 1.0
            }
        ]"""

        messages = [
            ConversationMessage(
                user_id=1,
                role="user",
                content="I generally prefer sci-fi games over fantasy ones.",
            ),
        ]

        memories = extractor.extract_from_conversation(messages, user_id=1)

        assert len(memories) == 1
        assert memories[0].memory_text == "Prefers sci-fi games over fantasy"
        assert memories[0].memory_type == "user_stated"
        assert memories[0].confidence == 1.0
        assert memories[0].source == "conversation"
        assert memories[0].user_id == 1

    def test_extract_inferred_preference(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test extracting an inferred preference."""
        mock_ollama.generate_text.return_value = """[
            {
                "memory_text": "Tends to enjoy story-driven games",
                "memory_type": "inferred",
                "confidence": 0.75
            }
        ]"""

        messages = [
            ConversationMessage(
                user_id=1,
                role="user",
                content="I loved Outer Wilds and What Remains of Edith Finch.",
            ),
        ]

        memories = extractor.extract_from_conversation(messages, user_id=1)

        assert len(memories) == 1
        assert memories[0].memory_type == "inferred"
        assert memories[0].confidence == 0.75

    def test_extract_multiple_preferences(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test extracting multiple preferences from a conversation."""
        mock_ollama.generate_text.return_value = """[
            {
                "memory_text": "Enjoys exploration games",
                "memory_type": "user_stated",
                "confidence": 1.0
            },
            {
                "memory_text": "Dislikes grinding mechanics",
                "memory_type": "user_stated",
                "confidence": 1.0
            },
            {
                "memory_text": "Prefers shorter gaming sessions",
                "memory_type": "inferred",
                "confidence": 0.6
            }
        ]"""

        messages = [
            ConversationMessage(
                user_id=1,
                role="user",
                content="I love exploration games but hate grinding. "
                "I usually only have an hour to play.",
            ),
        ]

        memories = extractor.extract_from_conversation(messages, user_id=1)

        assert len(memories) == 3
        assert any(m.memory_text == "Enjoys exploration games" for m in memories)
        assert any(m.memory_text == "Dislikes grinding mechanics" for m in memories)
        assert any(m.memory_text == "Prefers shorter gaming sessions" for m in memories)

    def test_extract_empty_conversation(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test extraction from empty conversation returns empty list."""
        memories = extractor.extract_from_conversation([], user_id=1)

        assert len(memories) == 0
        mock_ollama.generate_text.assert_not_called()

    def test_extract_no_preferences_found(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test extraction when no preferences are found."""
        mock_ollama.generate_text.return_value = "[]"

        messages = [
            ConversationMessage(user_id=1, role="user", content="Hello!"),
            ConversationMessage(
                user_id=1, role="assistant", content="Hi! How can I help?"
            ),
        ]

        memories = extractor.extract_from_conversation(messages, user_id=1)

        assert len(memories) == 0

    def test_extract_handles_llm_error(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test graceful handling of LLM errors."""
        mock_ollama.generate_text.side_effect = Exception("LLM error")

        messages = [
            ConversationMessage(user_id=1, role="user", content="I love games!"),
        ]

        memories = extractor.extract_from_conversation(messages, user_id=1)

        assert len(memories) == 0  # Should return empty list, not raise


class TestSingleMessageExtraction:
    """Tests for extract_from_single_message."""

    def test_extract_from_single_message(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test extracting from a single message."""
        mock_ollama.generate_text.return_value = """[
            {
                "memory_text": "Interested in cozy games",
                "memory_type": "user_stated",
                "confidence": 1.0
            }
        ]"""

        memories = extractor.extract_from_single_message(
            message="I'm looking for cozy games to relax with.",
            user_id=1,
        )

        assert len(memories) == 1
        assert memories[0].memory_text == "Interested in cozy games"


class TestPromptBuilding:
    """Tests for prompt construction."""

    def test_build_extraction_prompt(self, extractor: MemoryExtractor) -> None:
        """Test that extraction prompt is properly formatted."""
        messages = [
            ConversationMessage(
                user_id=1, role="user", content="What game should I play?"
            ),
            ConversationMessage(
                user_id=1,
                role="assistant",
                content="Based on your love of exploration, try Outer Wilds!",
            ),
            ConversationMessage(
                user_id=1, role="user", content="I prefer shorter games actually."
            ),
        ]

        prompt = extractor._build_extraction_prompt(messages)

        assert "User: What game should I play?" in prompt
        assert "Assistant: Based on your love of exploration" in prompt
        assert "User: I prefer shorter games actually." in prompt
        assert "Analyze this conversation" in prompt

    def test_extraction_uses_correct_system_prompt(
        self,
        extractor: MemoryExtractor,
        mock_ollama: MagicMock,
    ) -> None:
        """Test that extraction uses the memory extraction system prompt."""
        mock_ollama.generate_text.return_value = "[]"

        messages = [
            ConversationMessage(user_id=1, role="user", content="Hello"),
        ]

        extractor.extract_from_conversation(messages, user_id=1)

        mock_ollama.generate_text.assert_called_once()
        call_kwargs = mock_ollama.generate_text.call_args.kwargs
        assert call_kwargs["system_prompt"] == MEMORY_EXTRACTION_SYSTEM_PROMPT
        assert call_kwargs["temperature"] == 0.3


class TestResponseParsing:
    """Tests for parsing extraction responses."""

    def test_parse_clean_json(self, extractor: MemoryExtractor) -> None:
        """Test parsing clean JSON response."""
        response = """[
            {"memory_text": "Likes action games", "memory_type": "user_stated", "confidence": 1.0}
        ]"""

        result = extractor._parse_extraction_response(response)

        assert len(result) == 1
        assert result[0]["memory_text"] == "Likes action games"

    def test_parse_json_with_extra_text(self, extractor: MemoryExtractor) -> None:
        """Test parsing JSON with surrounding text."""
        response = """Here are the extracted preferences:
        [
            {"memory_text": "Enjoys RPGs", "memory_type": "inferred", "confidence": 0.8}
        ]
        Let me know if you need more."""

        result = extractor._parse_extraction_response(response)

        assert len(result) == 1
        assert result[0]["memory_text"] == "Enjoys RPGs"

    def test_parse_invalid_json(self, extractor: MemoryExtractor) -> None:
        """Test handling of invalid JSON."""
        response = "This is not valid JSON at all"

        result = extractor._parse_extraction_response(response)

        assert len(result) == 0

    def test_parse_empty_array(self, extractor: MemoryExtractor) -> None:
        """Test parsing empty array."""
        response = "[]"

        result = extractor._parse_extraction_response(response)

        assert len(result) == 0


class TestMemoryValidation:
    """Tests for memory validation."""

    def test_validate_filters_invalid_items(self, extractor: MemoryExtractor) -> None:
        """Test that invalid items are filtered out."""
        items = [
            {"memory_text": "Valid memory", "memory_type": "user_stated"},
            {"memory_type": "user_stated"},  # Missing memory_text
            {"memory_text": "", "memory_type": "user_stated"},  # Empty memory_text
            "not a dict",  # Wrong type
            {"memory_text": "Another valid", "memory_type": "inferred"},
        ]

        result = extractor._validate_memories(items)

        assert len(result) == 2
        assert result[0]["memory_text"] == "Valid memory"
        assert result[1]["memory_text"] == "Another valid"

    def test_validate_corrects_invalid_memory_type(
        self, extractor: MemoryExtractor
    ) -> None:
        """Test that invalid memory_type defaults to inferred."""
        items = [
            {"memory_text": "Some memory", "memory_type": "invalid_type"},
        ]

        result = extractor._validate_memories(items)

        assert len(result) == 1
        assert result[0]["memory_type"] == "inferred"

    def test_validate_clamps_confidence(self, extractor: MemoryExtractor) -> None:
        """Test that confidence values are clamped to 0-1."""
        items = [
            {"memory_text": "Memory 1", "memory_type": "inferred", "confidence": 1.5},
            {"memory_text": "Memory 2", "memory_type": "inferred", "confidence": -0.5},
        ]

        result = extractor._validate_memories(items)

        assert len(result) == 2
        assert result[0]["confidence"] == 1.0  # Clamped from 1.5
        assert result[1]["confidence"] == 0.0  # Clamped from -0.5

    def test_validate_handles_missing_confidence(
        self, extractor: MemoryExtractor
    ) -> None:
        """Test default confidence values."""
        items = [
            {"memory_text": "Stated preference", "memory_type": "user_stated"},
            {"memory_text": "Inferred preference", "memory_type": "inferred"},
        ]

        result = extractor._validate_memories(items)

        assert result[0]["confidence"] == 1.0  # Default for user_stated
        assert result[1]["confidence"] == 1.0  # Default when key missing

    def test_validate_handles_invalid_confidence_type(
        self, extractor: MemoryExtractor
    ) -> None:
        """Test handling of non-numeric confidence values."""
        items = [
            {
                "memory_text": "Memory",
                "memory_type": "inferred",
                "confidence": "high",
            },
        ]

        result = extractor._validate_memories(items)

        assert len(result) == 1
        assert result[0]["confidence"] == 0.5  # Default for inferred when invalid


class TestCustomModel:
    """Tests for custom model usage."""

    def test_uses_custom_model(self, mock_ollama: MagicMock) -> None:
        """Test that custom model is used when specified."""
        extractor = MemoryExtractor(
            ollama_client=mock_ollama,
            model="custom-model:latest",
        )
        mock_ollama.generate_text.return_value = "[]"

        messages = [
            ConversationMessage(user_id=1, role="user", content="Hello"),
        ]

        extractor.extract_from_conversation(messages, user_id=1)

        call_kwargs = mock_ollama.generate_text.call_args.kwargs
        assert call_kwargs["model"] == "custom-model:latest"

    def test_uses_default_model(self, mock_ollama: MagicMock) -> None:
        """Test that None is passed when no custom model specified."""
        extractor = MemoryExtractor(ollama_client=mock_ollama)
        mock_ollama.generate_text.return_value = "[]"

        messages = [
            ConversationMessage(user_id=1, role="user", content="Hello"),
        ]

        extractor.extract_from_conversation(messages, user_id=1)

        call_kwargs = mock_ollama.generate_text.call_args.kwargs
        assert call_kwargs["model"] is None  # Client will use its default
