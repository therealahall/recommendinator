"""Tests for shared AI tone constants."""

from src.conversation.engine import COMPACT_SYSTEM_PROMPT, FULL_SYSTEM_PROMPT
from src.llm.prompts import build_recommendation_system_prompt
from src.llm.tone import (
    ADVISOR_IDENTITY,
    PERSONALITY_COMPACT,
    PERSONALITY_TRAITS,
    STYLE_RULES,
)
from src.models.content import ContentType


class TestToneConstants:
    """Tests for the shared tone constants in src/llm/tone.py."""

    def test_advisor_identity_is_nonempty(self) -> None:
        assert ADVISOR_IDENTITY

    def test_advisor_identity_has_domain_placeholder(self) -> None:
        assert "{domain}" in ADVISOR_IDENTITY

    def test_advisor_identity_format_with_content_type(self) -> None:
        result = ADVISOR_IDENTITY.format(domain="video game")
        assert "video game" in result
        assert "{domain}" not in result

    def test_advisor_identity_format_with_personal(self) -> None:
        result = ADVISOR_IDENTITY.format(domain="personal")
        assert "personal" in result

    def test_personality_traits_is_nonempty(self) -> None:
        assert PERSONALITY_TRAITS

    def test_personality_traits_contains_honesty(self) -> None:
        assert "honest" in PERSONALITY_TRAITS.lower()

    def test_personality_traits_references_history_and_ratings(self) -> None:
        assert "history and ratings" in PERSONALITY_TRAITS

    def test_personality_traits_no_speech_pattern_suggestions(self) -> None:
        """Personality traits must not contain hype-machine or speech-pattern language."""
        banned_phrases = [
            "hype machine",
            "talk like",
            "sprinkle",
            "sound like",
            "speak as",
            "tastemaker",
            "exclamation marks",
        ]
        traits_lower = PERSONALITY_TRAITS.lower()
        for phrase in banned_phrases:
            assert phrase not in traits_lower, f"Found banned phrase: {phrase!r}"

    def test_style_rules_is_nonempty(self) -> None:
        assert STYLE_RULES

    def test_style_rules_contains_bold_emphasis(self) -> None:
        assert "bold" in STYLE_RULES

    def test_style_rules_addresses_as_you(self) -> None:
        assert '"you"' in STYLE_RULES

    def test_style_rules_bans_filler_words(self) -> None:
        assert "filler" in STYLE_RULES.lower()

    def test_style_rules_has_anti_spoiler(self) -> None:
        assert "NEVER reveal plot twists" in STYLE_RULES

    def test_style_rules_has_anti_sentiment_inference(self) -> None:
        assert "NEVER interpret them as emotions" in STYLE_RULES

    def test_style_rules_has_anti_misattribution(self) -> None:
        assert "belong to THAT item only" in STYLE_RULES

    def test_style_rules_has_author_accuracy(self) -> None:
        assert "claim two items share an author" in STYLE_RULES.lower()


class TestToneInRecommendationPrompt:
    """Tests that build_recommendation_system_prompt includes shared tone."""

    def test_includes_personality_traits(self) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "genuinely thrilled" in prompt

    def test_includes_style_rules(self) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "NEVER put words in their mouth" in prompt

    def test_includes_identity_with_content_type(self) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "book recommendation advisor" in prompt.lower()

    def test_content_type_varies_identity(self) -> None:
        book_prompt = build_recommendation_system_prompt(ContentType.BOOK)
        game_prompt = build_recommendation_system_prompt(ContentType.VIDEO_GAME)
        assert "book recommendation advisor" in book_prompt.lower()
        assert "video game recommendation advisor" in game_prompt.lower()

    def test_keeps_recommendation_specific_rules(self) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "candidate list" in prompt.lower()
        assert "concise" in prompt.lower()


class TestToneInConversationPrompt:
    """Tests that FULL_SYSTEM_PROMPT includes shared tone."""

    def test_includes_personality_traits(self) -> None:
        assert "genuinely thrilled" in FULL_SYSTEM_PROMPT

    def test_includes_style_rules(self) -> None:
        assert "NEVER put words in their mouth" in FULL_SYSTEM_PROMPT

    def test_includes_identity(self) -> None:
        assert "personal recommendation advisor" in FULL_SYSTEM_PROMPT.lower()

    def test_retains_format_placeholders(self) -> None:
        assert "{tool_descriptions}" in FULL_SYSTEM_PROMPT
        assert "{user_context}" in FULL_SYSTEM_PROMPT

    def test_format_placeholders_work(self) -> None:
        formatted = FULL_SYSTEM_PROMPT.format(
            tool_descriptions="test tools here",
            user_context="test context here",
        )
        assert "test tools here" in formatted
        assert "test context here" in formatted
        assert "{tool_descriptions}" not in formatted
        assert "{user_context}" not in formatted

    def test_keeps_conversation_specific_sections(self) -> None:
        assert "Data Accuracy Rules" in FULL_SYSTEM_PROMPT
        assert "Prediction Rules" in FULL_SYSTEM_PROMPT
        assert "What NOT To Do" in FULL_SYSTEM_PROMPT
        assert "Pre-Scored Recommendations" in FULL_SYSTEM_PROMPT

    def test_has_anti_misattribution(self) -> None:
        assert "belong to THAT item only" in FULL_SYSTEM_PROMPT

    def test_has_author_accuracy(self) -> None:
        assert "Do NOT claim items share the same author" in FULL_SYSTEM_PROMPT


class TestPersonalityCompact:
    """Tests for PERSONALITY_COMPACT used in 3B model prompts."""

    def test_personality_compact_is_nonempty(self) -> None:
        assert PERSONALITY_COMPACT

    def test_personality_compact_no_hype_machine(self) -> None:
        assert "hype machine" not in PERSONALITY_COMPACT.lower()

    def test_personality_compact_has_anti_fabrication_instruction(self) -> None:
        assert "NEVER put words in their mouth" in PERSONALITY_COMPACT

    def test_personality_compact_has_anti_emotion_interpretation(self) -> None:
        assert "NEVER interpret them as emotions" in PERSONALITY_COMPACT


class TestCompactSystemPromptGuardrails:
    """Tests that COMPACT_SYSTEM_PROMPT includes essential guardrails."""

    def test_has_anti_misattribution(self) -> None:
        assert "belong to THAT item only" in COMPACT_SYSTEM_PROMPT

    def test_has_anti_spoiler(self) -> None:
        assert "NEVER reveal plot twists" in COMPACT_SYSTEM_PROMPT
