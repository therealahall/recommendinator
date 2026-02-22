"""Tests for shared AI tone constants."""

from src.conversation.engine import FULL_SYSTEM_PROMPT
from src.llm.prompts import build_recommendation_system_prompt
from src.llm.tone import ADVISOR_IDENTITY, PERSONALITY_TRAITS, STYLE_RULES
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

    def test_personality_traits_contains_hype_machine(self) -> None:
        assert "HYPE MACHINE" in PERSONALITY_TRAITS

    def test_personality_traits_contains_honesty(self) -> None:
        assert "honest" in PERSONALITY_TRAITS.lower()

    def test_personality_traits_contains_tastemaker(self) -> None:
        assert "tastemaker" in PERSONALITY_TRAITS

    def test_style_rules_is_nonempty(self) -> None:
        assert STYLE_RULES

    def test_style_rules_contains_bold_emphasis(self) -> None:
        assert "bold" in STYLE_RULES

    def test_style_rules_addresses_as_you(self) -> None:
        assert '"you"' in STYLE_RULES

    def test_style_rules_bans_filler_words(self) -> None:
        assert "filler" in STYLE_RULES.lower()


class TestToneInRecommendationPrompt:
    """Tests that build_recommendation_system_prompt includes shared tone."""

    def test_includes_personality_traits(self) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "HYPE MACHINE" in prompt

    def test_includes_style_rules(self) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "mirror that language back" in prompt

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
        assert "HYPE MACHINE" in FULL_SYSTEM_PROMPT

    def test_includes_style_rules(self) -> None:
        assert "mirror that language back" in FULL_SYSTEM_PROMPT

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
