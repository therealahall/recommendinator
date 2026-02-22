"""Tests for LLM preference prompt templates."""

import pytest

from src.llm.preference_prompts import (
    PREFERENCE_INTERPRETATION_SYSTEM_PROMPT,
    build_batch_interpretation_prompt,
    build_preference_interpretation_prompt,
)


class TestPreferenceInterpretationSystemPrompt:
    """Tests for the PREFERENCE_INTERPRETATION_SYSTEM_PROMPT constant."""

    def test_is_non_empty_string(self) -> None:
        """System prompt should be a non-empty string."""
        assert isinstance(PREFERENCE_INTERPRETATION_SYSTEM_PROMPT, str)
        assert len(PREFERENCE_INTERPRETATION_SYSTEM_PROMPT) > 0

    def test_contains_json_output_instruction(self) -> None:
        """System prompt should instruct the LLM to output JSON."""
        assert "JSON" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT

    @pytest.mark.parametrize(
        "field_name",
        [
            "genre_boosts",
            "genre_penalties",
            "content_type_filters",
            "content_type_exclusions",
            "length_preferences",
            "confidence",
            "notes",
        ],
    )
    def test_contains_expected_field_name(self, field_name: str) -> None:
        """System prompt should document all expected JSON fields."""
        assert field_name in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT

    def test_contains_interpretation_rules(self) -> None:
        """System prompt should contain rules for interpreting preferences."""
        assert "avoid" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT.lower()
        assert "prefer" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT.lower()

    def test_contains_genre_examples(self) -> None:
        """System prompt should list example genres for guidance."""
        assert "horror" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "science fiction" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "fantasy" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT

    def test_contains_content_type_examples(self) -> None:
        """System prompt should list valid content types."""
        assert "book" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "movie" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "tv_show" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "video_game" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT

    def test_contains_confidence_levels(self) -> None:
        """System prompt should document confidence level values."""
        assert "high" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "medium" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT
        assert "low" in PREFERENCE_INTERPRETATION_SYSTEM_PROMPT


class TestBuildPreferenceInterpretationPrompt:
    """Tests for build_preference_interpretation_prompt."""

    def test_returns_string(self) -> None:
        """Function should return a string."""
        result = build_preference_interpretation_prompt("avoid horror")
        assert isinstance(result, str)

    def test_contains_rule_text(self) -> None:
        """Returned prompt should contain the provided rule."""
        rule = "avoid horror"
        result = build_preference_interpretation_prompt(rule)
        assert rule in result

    def test_contains_json_field_names(self) -> None:
        """Returned prompt should include all expected JSON field names."""
        result = build_preference_interpretation_prompt("prefer comedy")
        assert "genre_boosts" in result
        assert "genre_penalties" in result
        assert "content_type_filters" in result
        assert "content_type_exclusions" in result
        assert "length_preferences" in result
        assert "confidence" in result
        assert "notes" in result

    def test_contains_confidence_guidance(self) -> None:
        """Returned prompt should include guidance on confidence values."""
        result = build_preference_interpretation_prompt("prefer comedy")
        assert "high" in result
        assert "medium" in result
        assert "low" in result

    def test_empty_rule(self) -> None:
        """Function should handle an empty rule without error."""
        result = build_preference_interpretation_prompt("")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_rule_with_special_characters(self) -> None:
        """Function should handle rules with special characters."""
        rule_with_quotes = 'avoid "dark" horror'
        result = build_preference_interpretation_prompt(rule_with_quotes)
        assert rule_with_quotes in result

    def test_rule_with_unicode(self) -> None:
        """Function should handle rules with unicode characters."""
        unicode_rule = "prefer sci-fi like Lem's Solaris"
        result = build_preference_interpretation_prompt(unicode_rule)
        assert unicode_rule in result

    def test_rule_with_newlines(self) -> None:
        """Function should handle rules containing newline characters."""
        multiline_rule = "avoid horror\nprefer comedy"
        result = build_preference_interpretation_prompt(multiline_rule)
        assert multiline_rule in result

    def test_rule_is_quoted_in_prompt(self) -> None:
        """The rule should appear within quotes in the prompt."""
        rule = "prefer fantasy"
        result = build_preference_interpretation_prompt(rule)
        assert f'"{rule}"' in result

    @pytest.mark.parametrize(
        "rule",
        [
            "avoid horror",
            "prefer science fiction",
            "only books",
            "no movies",
            "short books",
            "I love RPGs",
        ],
    )
    def test_various_rule_types(self, rule: str) -> None:
        """Function should produce a valid prompt for various rule types."""
        result = build_preference_interpretation_prompt(rule)
        assert rule in result
        assert "genre_boosts" in result


class TestBuildBatchInterpretationPrompt:
    """Tests for build_batch_interpretation_prompt."""

    def test_returns_string(self) -> None:
        """Function should return a string."""
        result = build_batch_interpretation_prompt(["avoid horror", "prefer comedy"])
        assert isinstance(result, str)

    def test_contains_all_rules(self) -> None:
        """Returned prompt should contain all provided rules."""
        rules = ["avoid horror", "prefer comedy", "only books"]
        result = build_batch_interpretation_prompt(rules)
        for rule in rules:
            assert rule in result

    def test_rules_are_numbered(self) -> None:
        """Rules should appear as a numbered list in the prompt."""
        rules = ["avoid horror", "prefer comedy", "only books"]
        result = build_batch_interpretation_prompt(rules)
        assert '1. "avoid horror"' in result
        assert '2. "prefer comedy"' in result
        assert '3. "only books"' in result

    def test_contains_json_field_names(self) -> None:
        """Returned prompt should include all expected JSON field names."""
        result = build_batch_interpretation_prompt(["avoid horror"])
        assert "genre_boosts" in result
        assert "genre_penalties" in result
        assert "content_type_filters" in result
        assert "content_type_exclusions" in result
        assert "length_preferences" in result
        assert "confidence" in result
        assert "notes" in result

    def test_contains_conflict_resolution_guidance(self) -> None:
        """Returned prompt should mention how to handle conflicting rules."""
        result = build_batch_interpretation_prompt(["avoid horror", "prefer horror"])
        assert "conflict" in result.lower()
        assert "precedence" in result.lower()

    def test_empty_rules_list(self) -> None:
        """Function should handle an empty rules list without error."""
        result = build_batch_interpretation_prompt([])
        assert isinstance(result, str)
        assert len(result) > 0

    def test_single_rule(self) -> None:
        """Function should handle a single rule in the list."""
        result = build_batch_interpretation_prompt(["avoid horror"])
        assert '1. "avoid horror"' in result

    def test_rules_with_special_characters(self) -> None:
        """Function should handle rules containing special characters."""
        rules = ["avoid 'dark' horror", "prefer sci-fi & fantasy"]
        result = build_batch_interpretation_prompt(rules)
        for rule in rules:
            assert rule in result

    def test_rules_with_empty_string(self) -> None:
        """Function should handle a rule that is an empty string."""
        rules = ["avoid horror", "", "prefer comedy"]
        result = build_batch_interpretation_prompt(rules)
        assert "avoid horror" in result
        assert "prefer comedy" in result

    def test_many_rules(self) -> None:
        """Function should handle a large number of rules."""
        rules = [f"rule number {index}" for index in range(20)]
        result = build_batch_interpretation_prompt(rules)
        for index, rule in enumerate(rules):
            assert f'{index + 1}. "{rule}"' in result

    def test_mentions_merging(self) -> None:
        """Returned prompt should instruct the LLM to merge rules."""
        result = build_batch_interpretation_prompt(["avoid horror", "prefer comedy"])
        assert "merge" in result.lower() or "combined" in result.lower()

    @pytest.mark.parametrize(
        "rules",
        [
            ["avoid horror"],
            ["prefer comedy", "only books"],
            ["no movies", "short books", "love RPGs"],
        ],
    )
    def test_various_rule_list_sizes(self, rules: list[str]) -> None:
        """Function should produce valid prompts for various list sizes."""
        result = build_batch_interpretation_prompt(rules)
        for rule in rules:
            assert rule in result
        assert "genre_boosts" in result
