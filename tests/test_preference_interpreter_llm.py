"""Tests for the LLM-powered preference interpreter."""

import json
from unittest.mock import MagicMock

import pytest

from src.llm.client import OllamaClient
from src.recommendations.preference_interpreter import (
    InterpretedPreference,
    LLMPreferenceInterpreter,
    PatternConfidence,
)
from src.storage.manager import StorageManager


@pytest.fixture
def mock_ollama_client() -> MagicMock:
    """Create a mock OllamaClient."""
    return MagicMock(spec=OllamaClient)


@pytest.fixture
def mock_storage_manager() -> MagicMock:
    """Create a mock StorageManager with cache methods."""
    manager = MagicMock(spec=StorageManager)
    manager.get_cached_preference_interpretation.return_value = None
    return manager


@pytest.fixture
def llm_interpreter(
    mock_ollama_client: MagicMock, mock_storage_manager: MagicMock
) -> LLMPreferenceInterpreter:
    """Create an LLMPreferenceInterpreter with mocks."""
    return LLMPreferenceInterpreter(
        ollama_client=mock_ollama_client,
        storage_manager=mock_storage_manager,
        model="test-model",
    )


class TestLLMPreferenceInterpreter:
    """Tests for LLMPreferenceInterpreter."""

    def test_uses_cache_when_available(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_storage_manager: MagicMock,
    ) -> None:
        """Should return cached result without calling LLM."""
        cached_data = {
            "genre_boosts": {"horror": 1.0},
            "genre_penalties": {},
            "content_type_filters": [],
            "content_type_exclusions": [],
            "length_preferences": {},
            "confidence": "high",
            "original_rule": "prefer horror",
            "interpretation_notes": "cached",
        }
        mock_storage_manager.get_cached_preference_interpretation.return_value = (
            json.dumps(cached_data)
        )

        result = llm_interpreter.interpret_all(["prefer horror"])

        assert "horror" in result.genre_boosts
        assert result.confidence == PatternConfidence.HIGH
        # LLM should not be called
        llm_interpreter.client.generate_text.assert_not_called()

    def test_falls_back_to_pattern_on_llm_error(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_ollama_client: MagicMock,
    ) -> None:
        """Should fall back to pattern interpreter when LLM fails."""
        mock_ollama_client.generate_text.side_effect = Exception("LLM unavailable")

        result = llm_interpreter.interpret_all(["avoid horror"])

        # Pattern interpreter should have handled it
        assert "horror" in result.genre_penalties
        # Result should not be empty (pattern fallback worked)
        assert not result.is_empty()

    def test_falls_back_to_pattern_on_invalid_json(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_ollama_client: MagicMock,
    ) -> None:
        """Should fall back when LLM returns invalid JSON."""
        mock_ollama_client.generate_text.return_value = "This is not JSON"

        result = llm_interpreter.interpret_all(["prefer sci-fi"])

        # Pattern interpreter should have handled it
        assert "science fiction" in result.genre_boosts

    def test_parses_valid_llm_response(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_ollama_client: MagicMock,
        mock_storage_manager: MagicMock,
    ) -> None:
        """Should correctly parse valid LLM JSON response."""
        llm_response = json.dumps(
            {
                "genre_boosts": {"dark fantasy": 0.8},
                "genre_penalties": {"romance": 1.0},
                "content_type_filters": [],
                "content_type_exclusions": [],
                "length_preferences": {"book": "long"},
                "confidence": "high",
                "notes": "User wants dark fantasy, no romance, long books",
            }
        )
        mock_ollama_client.generate_text.return_value = llm_response

        result = llm_interpreter.interpret_all(
            ["I love dark fantasy", "avoid romance", "long books only"]
        )

        assert "dark fantasy" in result.genre_boosts
        assert "romance" in result.genre_penalties
        assert result.length_preferences.get("book") == "long"
        assert result.confidence == PatternConfidence.HIGH
        # Should save to cache
        mock_storage_manager.save_cached_preference_interpretation.assert_called_once()

    def test_parses_llm_response_in_code_block(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_ollama_client: MagicMock,
    ) -> None:
        """Should extract JSON from markdown code blocks."""
        llm_response = """Here's the interpretation:

```json
{
    "genre_boosts": {"mystery": 1.0},
    "genre_penalties": {},
    "content_type_filters": [],
    "content_type_exclusions": [],
    "length_preferences": {},
    "confidence": "high",
    "notes": "User prefers mystery"
}
```

This captures the user's preference for mystery content."""
        mock_ollama_client.generate_text.return_value = llm_response

        result = llm_interpreter.interpret_all(["love mystery"])

        assert "mystery" in result.genre_boosts

    def test_empty_rules_returns_empty_result(
        self, llm_interpreter: LLMPreferenceInterpreter
    ) -> None:
        """Empty rules list should return empty result."""
        result = llm_interpreter.interpret_all([])

        assert result.is_empty()
        assert result.confidence == PatternConfidence.NONE

    def test_interpret_single_rule(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_ollama_client: MagicMock,
    ) -> None:
        """interpret() should work for single rules."""
        mock_ollama_client.generate_text.side_effect = Exception("LLM unavailable")

        result = llm_interpreter.interpret("avoid horror")

        assert "horror" in result.genre_penalties

    def test_cache_key_is_deterministic(
        self, llm_interpreter: LLMPreferenceInterpreter
    ) -> None:
        """Same rules should produce same cache key regardless of order."""
        key1 = llm_interpreter._compute_cache_key(["avoid horror", "prefer comedy"])
        key2 = llm_interpreter._compute_cache_key(["prefer comedy", "avoid horror"])
        key3 = llm_interpreter._compute_cache_key(
            ["AVOID HORROR", "PREFER COMEDY"]
        )  # Case insensitive

        assert key1 == key2
        assert key1 == key3

    def test_clear_cache(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_storage_manager: MagicMock,
    ) -> None:
        """clear_cache should call storage manager."""
        llm_interpreter.clear_cache()

        mock_storage_manager.clear_cached_preference_interpretations.assert_called_once()

    def test_clear_cache_handles_error(
        self,
        llm_interpreter: LLMPreferenceInterpreter,
        mock_storage_manager: MagicMock,
    ) -> None:
        """clear_cache should handle storage errors gracefully."""
        mock_storage_manager.clear_cached_preference_interpretations.side_effect = (
            Exception("DB error")
        )

        # Should not raise
        llm_interpreter.clear_cache()

    def test_works_without_storage_manager(self, mock_ollama_client: MagicMock) -> None:
        """Should work without storage manager (no caching)."""
        interpreter = LLMPreferenceInterpreter(
            ollama_client=mock_ollama_client,
            storage_manager=None,
        )
        mock_ollama_client.generate_text.side_effect = Exception("LLM unavailable")

        result = interpreter.interpret_all(["prefer horror"])

        # Should fall back to pattern interpreter
        assert "horror" in result.genre_boosts


class TestCacheSerialization:
    """Tests for cache serialization/deserialization."""

    def test_interpreted_to_json_and_back(
        self, llm_interpreter: LLMPreferenceInterpreter
    ) -> None:
        """Should round-trip InterpretedPreference through JSON."""
        original = InterpretedPreference(
            genre_boosts={"horror": 0.8, "comedy": 1.0},
            genre_penalties={"romance": 0.5},
            content_type_filters={"book", "movie"},
            content_type_exclusions={"video_game"},
            length_preferences={"book": "short"},
            confidence=PatternConfidence.HIGH,
            original_rule="test rule",
            interpretation_notes="test notes",
        )

        json_str = llm_interpreter._interpreted_to_json(original)
        restored = llm_interpreter._json_to_interpreted(json_str)

        assert restored.genre_boosts == original.genre_boosts
        assert restored.genre_penalties == original.genre_penalties
        assert restored.content_type_filters == original.content_type_filters
        assert restored.content_type_exclusions == original.content_type_exclusions
        assert restored.length_preferences == original.length_preferences
        assert restored.confidence == original.confidence
        assert restored.original_rule == original.original_rule
        assert restored.interpretation_notes == original.interpretation_notes
