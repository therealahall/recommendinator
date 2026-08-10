"""Prompt templates for LLM-based preference interpretation."""

from src.models.user_preferences import UserPreferenceConfig
from src.utils.text import sanitize_rule_text

PREFERENCE_INTERPRETATION_SYSTEM_PROMPT = """You are a preference interpretation assistant for a media recommendation system.
Your task is to parse natural language preference rules into structured scoring adjustments.

You must output valid JSON with these fields:
- genre_boosts: dict of genre name -> boost factor (0.0-1.0), genres to prioritize
- genre_penalties: dict of genre name -> penalty factor (0.0-1.0), genres to avoid
- content_type_filters: list of content types to include (book, movie, tv_show, video_game)
- content_type_exclusions: list of content types to exclude
- length_preferences: dict of content type -> length (short, medium, long)
- confidence: your confidence in the interpretation (high, medium, low)
- notes: brief explanation of your interpretation

Rules for interpretation:
1. "avoid X", "no X", "hate X", "tired of X" -> add X to genre_penalties with factor 1.0
2. "prefer X", "love X", "more X" -> add X to genre_boosts with factor 1.0
3. "only books/movies/etc" -> add to content_type_filters
4. "no books/movies/etc" -> add to content_type_exclusions
5. "short/medium/long X" -> add to length_preferences
6. Normalize genre aliases (sci-fi -> science fiction, etc.)
7. Handle negations and complex phrases

Examples of valid genres: horror, science fiction, fantasy, romance, mystery, thriller, comedy, drama, action, adventure, etc.
For video games: rpg, fps, strategy, simulation, puzzle, platformer, roguelike, survival, etc."""


def _sanitize_rule(rule: str) -> str:
    """Cap a sanitized rule at the length its storage accepts."""
    return sanitize_rule_text(rule)[: UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH]


def build_preference_interpretation_prompt(rule: str) -> str:
    """Build a prompt for interpreting a natural language preference rule.

    Args:
        rule: The natural language rule to interpret.

    Returns:
        Formatted prompt string with the rule sanitized.
    """
    return f"""Parse this preference rule into structured adjustments:

"{_sanitize_rule(rule)}"

Return a JSON object with exactly these fields:
{{
  "genre_boosts": {{}},
  "genre_penalties": {{}},
  "content_type_filters": [],
  "content_type_exclusions": [],
  "length_preferences": {{}},
  "confidence": "high|medium|low",
  "notes": "explanation"
}}

Only include fields that apply to the rule. Use empty dict/list for unused fields.
Confidence should be "high" for clear rules, "medium" for ambiguous ones, "low" for guesses."""


def build_batch_interpretation_prompt(rules: list[str]) -> str:
    """Build a prompt for interpreting multiple preference rules.

    Args:
        rules: List of natural language rules to interpret.

    Returns:
        Formatted prompt string with every rule sanitized.
    """
    rules_text = "\n".join(
        f'{index + 1}. "{_sanitize_rule(rule)}"' for index, rule in enumerate(rules)
    )

    return f"""Parse these preference rules into a single combined result:

{rules_text}

Return a single JSON object that merges all rules:
{{
  "genre_boosts": {{}},
  "genre_penalties": {{}},
  "content_type_filters": [],
  "content_type_exclusions": [],
  "length_preferences": {{}},
  "confidence": "high|medium|low",
  "notes": "explanation of all rules"
}}

If rules conflict (e.g., one says prefer horror, another says avoid horror), the later rule takes precedence.
Only include fields that apply. Use empty dict/list for unused fields."""
