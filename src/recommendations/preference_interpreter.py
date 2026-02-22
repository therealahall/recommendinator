"""Pattern-based and LLM-powered natural language preference interpreter.

Parses common natural language rules into structured scoring adjustments.
The pattern-based interpreter handles common cases without an LLM.
The LLM interpreter can handle more nuanced rules with fallback to patterns.

Example rules:
- "avoid horror" -> genre penalty for horror
- "prefer sci-fi" -> genre boost for sci-fi
- "only books" -> content type filter
- "short movies" -> length preference for movies
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from src.llm.preference_prompts import (
    PREFERENCE_INTERPRETATION_SYSTEM_PROMPT,
    build_batch_interpretation_prompt,
)

if TYPE_CHECKING:
    from src.llm.client import OllamaClient
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class PatternConfidence(str, Enum):
    """Confidence level of pattern matching."""

    HIGH = "high"  # Exact pattern match
    MEDIUM = "medium"  # Partial or fuzzy match
    LOW = "low"  # Guessed interpretation
    NONE = "none"  # Could not interpret


# Genre aliases: canonical form -> list of aliases
GENRE_ALIASES: dict[str, list[str]] = {
    "science fiction": ["sci-fi", "scifi", "sf"],
    "fantasy": ["fantasia"],
    "horror": ["scary", "terrifying"],
    "mystery": ["mysteries", "detective"],
    "romance": ["romantic", "love story", "love stories"],
    "thriller": ["thrillers", "suspense"],
    "comedy": ["comedies", "funny", "humor", "humour"],
    "drama": ["dramas", "dramatic"],
    "action": ["action-adventure"],
    "adventure": ["adventures"],
    "historical fiction": ["historical", "history fiction"],
    "biography": ["biographies", "bio", "biographical"],
    "memoir": ["memoirs", "autobiography", "autobiographical"],
    "self-help": ["self help", "personal development"],
    "true crime": ["truecrime"],
    "young adult": ["ya", "teen", "teenage"],
    "children": ["kids", "childrens", "children's"],
    "graphic novel": ["graphic novels", "comics", "comic book", "comic books", "manga"],
    "non-fiction": ["nonfiction", "non fiction"],
    "literary fiction": ["literary", "literature"],
    "dystopian": ["dystopia"],
    "post-apocalyptic": ["post apocalyptic", "apocalyptic"],
    "urban fantasy": ["urban"],
    "paranormal": ["paranormal romance"],
    "steampunk": ["steam punk"],
    "cyberpunk": ["cyber punk"],
    "space opera": ["space"],
    "military": ["military fiction", "war"],
    "western": ["westerns"],
    "crime": ["crimes", "criminal"],
    "psychological": ["psych", "psychological thriller"],
    "cozy mystery": ["cozy", "cosy", "cozy mysteries"],
    "rpg": ["role-playing", "role playing"],
    "fps": ["first-person shooter", "first person shooter", "shooter"],
    "mmorpg": ["mmo"],
    "strategy": ["rts", "turn-based", "turn based"],
    "simulation": ["sim", "simulator"],
    "puzzle": ["puzzles", "puzzle game"],
    "platformer": ["platform", "platforming"],
    "roguelike": ["rogue-like", "roguelite", "rogue-lite"],
    "survival": ["survival horror"],
    "open world": ["open-world", "sandbox"],
    "indie": ["independent"],
    "documentary": ["documentaries", "docu"],
    "animated": ["animation", "cartoon", "cartoons"],
    "anime": ["japanese animation"],
    "musical": ["musicals"],
    "noir": ["film noir"],
    "superhero": ["superheroes", "comic book movie"],
}

# Content type aliases
CONTENT_TYPE_ALIASES: dict[str, list[str]] = {
    "book": ["books", "novel", "novels", "reading"],
    "movie": ["movies", "film", "films", "cinema"],
    "tv_show": [
        "tv",
        "tv shows",
        "tv show",
        "television",
        "series",
        "shows",
        "show",
    ],
    "video_game": [
        "video games",
        "game",
        "games",
        "gaming",
        "videogame",
        "videogames",
    ],
}

# Length preference aliases
LENGTH_ALIASES: dict[str, list[str]] = {
    "short": ["quick", "brief", "fast", "small"],
    "medium": ["moderate", "mid-length", "mid length", "average"],
    "long": ["lengthy", "big", "large", "epic", "extended"],
}


def _normalize_genre(genre: str) -> str:
    """Normalize a genre string to its canonical form.

    Args:
        genre: Raw genre string from user input.

    Returns:
        Canonical genre name (lowercased).
    """
    genre_lower = genre.lower().strip()

    # Check if it's already canonical
    if genre_lower in GENRE_ALIASES:
        return genre_lower

    # Check aliases
    for canonical, aliases in GENRE_ALIASES.items():
        if genre_lower in aliases:
            return canonical

    # Return as-is if no alias found
    return genre_lower


def _normalize_content_type(content_type: str) -> str | None:
    """Normalize a content type string to its canonical form.

    Args:
        content_type: Raw content type string from user input.

    Returns:
        Canonical content type name, or None if not recognized.
    """
    content_type_lower = content_type.lower().strip()

    # Check if it's already canonical
    if content_type_lower in CONTENT_TYPE_ALIASES:
        return content_type_lower

    # Check aliases
    for canonical, aliases in CONTENT_TYPE_ALIASES.items():
        if content_type_lower in aliases:
            return canonical

    return None


def _normalize_length(length: str) -> str | None:
    """Normalize a length preference string.

    Args:
        length: Raw length string from user input.

    Returns:
        Canonical length preference, or None if not recognized.
    """
    length_lower = length.lower().strip()

    if length_lower in LENGTH_ALIASES:
        return length_lower

    for canonical, aliases in LENGTH_ALIASES.items():
        if length_lower in aliases:
            return canonical

    return None


@dataclass
class InterpretedPreference:
    """Result of interpreting a natural language preference rule.

    Attributes:
        genre_boosts: Genres to boost (canonical name -> boost factor 0.0-1.0).
        genre_penalties: Genres to penalize (canonical name -> penalty factor 0.0-1.0).
        content_type_filters: Content types to include (if non-empty, only these types).
        content_type_exclusions: Content types to exclude.
        length_preferences: Content type -> preferred length (short/medium/long).
        confidence: How confident the interpreter is in the result.
        original_rule: The original rule text that was interpreted.
        interpretation_notes: Human-readable notes about how the rule was interpreted.
    """

    genre_boosts: dict[str, float] = field(default_factory=dict)
    genre_penalties: dict[str, float] = field(default_factory=dict)
    content_type_filters: set[str] = field(default_factory=set)
    content_type_exclusions: set[str] = field(default_factory=set)
    length_preferences: dict[str, str] = field(default_factory=dict)
    confidence: PatternConfidence = PatternConfidence.NONE
    original_rule: str = ""
    interpretation_notes: str = ""

    def is_empty(self) -> bool:
        """Check if no preferences were extracted."""
        return (
            not self.genre_boosts
            and not self.genre_penalties
            and not self.content_type_filters
            and not self.content_type_exclusions
            and not self.length_preferences
        )

    def merge_with(self, other: InterpretedPreference) -> InterpretedPreference:
        """Merge another interpreted preference into this one.

        Later rules (from other) take precedence for conflicting keys.

        Args:
            other: Another interpreted preference to merge in.

        Returns:
            New merged InterpretedPreference.
        """
        merged_boosts = {**self.genre_boosts, **other.genre_boosts}
        merged_penalties = {**self.genre_penalties, **other.genre_penalties}
        merged_type_filters = self.content_type_filters | other.content_type_filters
        merged_type_exclusions = (
            self.content_type_exclusions | other.content_type_exclusions
        )
        merged_length = {**self.length_preferences, **other.length_preferences}

        # Combine notes
        notes_parts = []
        if self.interpretation_notes:
            notes_parts.append(self.interpretation_notes)
        if other.interpretation_notes:
            notes_parts.append(other.interpretation_notes)

        # Use the lower confidence level
        confidence_order = [
            PatternConfidence.HIGH,
            PatternConfidence.MEDIUM,
            PatternConfidence.LOW,
            PatternConfidence.NONE,
        ]
        self_idx = confidence_order.index(self.confidence)
        other_idx = confidence_order.index(other.confidence)
        merged_confidence = confidence_order[max(self_idx, other_idx)]

        return InterpretedPreference(
            genre_boosts=merged_boosts,
            genre_penalties=merged_penalties,
            content_type_filters=merged_type_filters,
            content_type_exclusions=merged_type_exclusions,
            length_preferences=merged_length,
            confidence=merged_confidence,
            original_rule=f"{self.original_rule}; {other.original_rule}".strip("; "),
            interpretation_notes="; ".join(notes_parts),
        )


class PatternBasedInterpreter:
    """Interprets natural language preference rules using regex patterns.

    This interpreter handles common patterns without requiring an LLM.
    It's designed to be fast and predictable for well-formed rules.
    """

    # Patterns for genre preferences (avoid, no, prefer, more, love, hate, etc.)
    AVOID_PATTERNS = [
        r"(?:avoid|no|skip|exclude|ban|block|hide|remove|filter out|without)\s+(.+)",
        r"(?:don't|do not|dont)\s+(?:want|like|show|recommend|include)\s+(.+)",
        r"(?:i\s+)?(?:hate|dislike|can't stand|cannot stand)\s+(.+)",
        r"(?:tired of|sick of|burnt out on|burned out on|over)\s+(.+)",
        r"not\s+(?:into|interested in)\s+(.+)",
    ]

    PREFER_PATTERNS = [
        # More specific patterns first
        r"(?:want more of|more of)\s+(.+)",
        r"(?:prefer|prioritize|boost|favor|favour|emphasize)\s+(.+)",
        r"(?:want more)\s+(.+)",
        r"(?:i\s+)?(?:love|like|enjoy|adore)\s+(.+)",
        r"(?:give me|show me|recommend|suggest)\s+(?:more\s+)?(.+)",
        r"(?:in the mood for|feeling like|craving)\s+(.+)",
        r"(?<!not )into\s+(.+)",
        r"(?<!not )interested in\s+(.+)",
    ]

    # Patterns for content type filters
    ONLY_TYPE_PATTERNS = [
        r"only\s+(.+)",
        r"just\s+(.+)",
        r"exclusively\s+(.+)",
    ]

    NO_TYPE_PATTERNS = [
        r"no\s+(.+)",
        r"skip\s+(.+)",
        r"exclude\s+(.+)",
        r"hide\s+(.+)",
    ]

    # Patterns for length preferences
    LENGTH_PATTERNS = [
        r"(short|quick|brief|long|lengthy|epic|medium|moderate)\s+(.+)",
        r"(.+)\s+(?:that are|that's|which are)\s+(short|quick|brief|long|lengthy|epic|medium|moderate)",
    ]

    def __init__(self) -> None:
        """Initialize the pattern-based interpreter."""
        # Compile all patterns for efficiency
        self._avoid_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.AVOID_PATTERNS
        ]
        self._prefer_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.PREFER_PATTERNS
        ]
        self._only_type_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.ONLY_TYPE_PATTERNS
        ]
        self._no_type_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.NO_TYPE_PATTERNS
        ]
        self._length_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.LENGTH_PATTERNS
        ]

    def interpret(self, rule: str) -> InterpretedPreference:
        """Interpret a single natural language rule.

        Args:
            rule: The rule text to interpret.

        Returns:
            InterpretedPreference with extracted preferences.
        """
        rule = rule.strip()
        if not rule:
            return InterpretedPreference(
                confidence=PatternConfidence.NONE,
                original_rule=rule,
                interpretation_notes="Empty rule",
            )

        result = InterpretedPreference(original_rule=rule)
        notes: list[str] = []

        # Try length patterns first (they're more specific)
        length_result = self._try_length_patterns(rule)
        if length_result:
            result.length_preferences = length_result["preferences"]
            notes.append(length_result["note"])

        # Try content type "only" patterns
        only_result = self._try_only_type_patterns(rule)
        if only_result:
            result.content_type_filters = only_result["filters"]
            notes.append(only_result["note"])

        # Try avoid patterns (genre penalties)
        avoid_result = self._try_avoid_patterns(rule)
        if avoid_result:
            # Check if it's a content type exclusion
            for genre in list(avoid_result["genres"]):
                content_type = _normalize_content_type(genre)
                if content_type:
                    result.content_type_exclusions.add(content_type)
                    del avoid_result["genres"][genre]
                    notes.append(f"Exclude content type: {content_type}")

            if avoid_result["genres"]:
                result.genre_penalties = avoid_result["genres"]
                notes.append(avoid_result["note"])

        # Try prefer patterns (genre boosts)
        prefer_result = self._try_prefer_patterns(rule)
        if prefer_result:
            # Check if it's a content type filter
            for genre in list(prefer_result["genres"]):
                content_type = _normalize_content_type(genre)
                if content_type:
                    result.content_type_filters.add(content_type)
                    del prefer_result["genres"][genre]
                    notes.append(f"Filter to content type: {content_type}")

            if prefer_result["genres"]:
                result.genre_boosts = prefer_result["genres"]
                notes.append(prefer_result["note"])

        # Determine confidence
        if result.is_empty():
            result.confidence = PatternConfidence.NONE
            result.interpretation_notes = "Could not interpret rule"
        elif notes:
            result.interpretation_notes = "; ".join(notes)
            # High confidence if we matched a clear pattern
            if any(
                word in rule.lower()
                for word in ["avoid", "prefer", "only", "no ", "hate", "love"]
            ):
                result.confidence = PatternConfidence.HIGH
            else:
                result.confidence = PatternConfidence.MEDIUM

        return result

    def interpret_all(self, rules: list[str]) -> InterpretedPreference:
        """Interpret multiple rules and merge the results.

        Args:
            rules: List of rule strings to interpret.

        Returns:
            Merged InterpretedPreference from all rules.
        """
        if not rules:
            return InterpretedPreference(
                confidence=PatternConfidence.NONE,
                interpretation_notes="No rules provided",
            )

        result = InterpretedPreference()
        for rule in rules:
            interpreted = self.interpret(rule)
            result = result.merge_with(interpreted)

        return result

    def _try_avoid_patterns(self, rule: str) -> dict | None:
        """Try to match avoid/penalty patterns.

        Args:
            rule: Rule text to match.

        Returns:
            Dict with genres and note, or None if no match.
        """
        for pattern in self._avoid_patterns:
            match = pattern.search(rule)
            if match:
                raw_genre = match.group(1).strip()
                # Remove trailing punctuation
                raw_genre = re.sub(r"[.,!?]+$", "", raw_genre)
                normalized = _normalize_genre(raw_genre)
                return {
                    "genres": {normalized: 1.0},  # Full penalty
                    "note": f"Avoid genre: {normalized}",
                }
        return None

    def _try_prefer_patterns(self, rule: str) -> dict | None:
        """Try to match prefer/boost patterns.

        Args:
            rule: Rule text to match.

        Returns:
            Dict with genres and note, or None if no match.
        """
        for pattern in self._prefer_patterns:
            match = pattern.search(rule)
            if match:
                raw_genre = match.group(1).strip()
                raw_genre = re.sub(r"[.,!?]+$", "", raw_genre)
                normalized = _normalize_genre(raw_genre)
                return {
                    "genres": {normalized: 1.0},  # Full boost
                    "note": f"Prefer genre: {normalized}",
                }
        return None

    def _try_only_type_patterns(self, rule: str) -> dict | None:
        """Try to match content type filter patterns.

        Args:
            rule: Rule text to match.

        Returns:
            Dict with filters and note, or None if no match.
        """
        for pattern in self._only_type_patterns:
            match = pattern.search(rule)
            if match:
                raw_type = match.group(1).strip()
                raw_type = re.sub(r"[.,!?]+$", "", raw_type)
                normalized = _normalize_content_type(raw_type)
                if normalized:
                    return {
                        "filters": {normalized},
                        "note": f"Only content type: {normalized}",
                    }
        return None

    def _try_length_patterns(self, rule: str) -> dict | None:
        """Try to match length preference patterns.

        Args:
            rule: Rule text to match.

        Returns:
            Dict with preferences and note, or None if no match.
        """
        for pattern in self._length_patterns:
            match = pattern.search(rule)
            if match:
                groups = match.groups()
                # Determine which group is length and which is content type
                if len(groups) == 2:
                    # Try both orderings
                    length1 = _normalize_length(groups[0])
                    type1 = _normalize_content_type(groups[1])

                    if length1 and type1:
                        return {
                            "preferences": {type1: length1},
                            "note": f"Length preference: {length1} {type1}",
                        }

                    # Try reverse
                    length2 = _normalize_length(groups[1])
                    type2 = _normalize_content_type(groups[0])

                    if length2 and type2:
                        return {
                            "preferences": {type2: length2},
                            "note": f"Length preference: {length2} {type2}",
                        }

        return None


class LLMPreferenceInterpreter:
    """LLM-powered preference interpreter with pattern-based fallback.

    Uses an LLM to interpret nuanced natural language rules, falling back
    to the PatternBasedInterpreter when the LLM is unavailable or fails.
    Caches interpretations to avoid repeated LLM calls for the same rules.
    """

    def __init__(
        self,
        ollama_client: OllamaClient,
        storage_manager: StorageManager | None = None,
        model: str = "llama3.2",
    ) -> None:
        """Initialize the LLM preference interpreter.

        Args:
            ollama_client: Client for making LLM requests.
            storage_manager: Optional storage manager for caching interpretations.
            model: Model name to use for interpretation.
        """
        self.client = ollama_client
        self.storage = storage_manager
        self.model = model
        self.pattern_interpreter = PatternBasedInterpreter()

    def _compute_cache_key(self, rules: list[str]) -> str:
        """Compute a cache key for a set of rules.

        Args:
            rules: List of rule strings.

        Returns:
            SHA256 hash of the sorted, joined rules.
        """
        normalized = ";".join(sorted(r.strip().lower() for r in rules if r.strip()))
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]

    def _get_cached(self, cache_key: str) -> InterpretedPreference | None:
        """Try to get a cached interpretation.

        Args:
            cache_key: The cache key to look up.

        Returns:
            Cached InterpretedPreference or None if not found.
        """
        if self.storage is None:
            return None
        try:
            cached_json = self.storage.get_cached_preference_interpretation(cache_key)
            if cached_json:
                return self._json_to_interpreted(cached_json)
        except Exception as error:
            logger.debug(f"Cache lookup failed: {error}")
        return None

    def _save_to_cache(
        self, cache_key: str, interpreted: InterpretedPreference
    ) -> None:
        """Save an interpretation to the cache.

        Args:
            cache_key: The cache key.
            interpreted: The interpreted preference to cache.
        """
        if self.storage is None:
            return
        try:
            cache_data = self._interpreted_to_json(interpreted)
            self.storage.save_cached_preference_interpretation(cache_key, cache_data)
        except Exception as error:
            logger.debug(f"Cache save failed: {error}")

    def _interpreted_to_json(self, interpreted: InterpretedPreference) -> str:
        """Convert InterpretedPreference to JSON string.

        Args:
            interpreted: The preference to serialize.

        Returns:
            JSON string representation.
        """
        data = {
            "genre_boosts": interpreted.genre_boosts,
            "genre_penalties": interpreted.genre_penalties,
            "content_type_filters": list(interpreted.content_type_filters),
            "content_type_exclusions": list(interpreted.content_type_exclusions),
            "length_preferences": interpreted.length_preferences,
            "confidence": interpreted.confidence.value,
            "original_rule": interpreted.original_rule,
            "interpretation_notes": interpreted.interpretation_notes,
        }
        return json.dumps(data)

    def _json_to_interpreted(self, json_str: str) -> InterpretedPreference:
        """Convert JSON string to InterpretedPreference.

        Args:
            json_str: JSON string representation.

        Returns:
            InterpretedPreference instance.
        """
        data = json.loads(json_str)
        return InterpretedPreference(
            genre_boosts=data.get("genre_boosts", {}),
            genre_penalties=data.get("genre_penalties", {}),
            content_type_filters=set(data.get("content_type_filters", [])),
            content_type_exclusions=set(data.get("content_type_exclusions", [])),
            length_preferences=data.get("length_preferences", {}),
            confidence=PatternConfidence(data.get("confidence", "none")),
            original_rule=data.get("original_rule", ""),
            interpretation_notes=data.get("interpretation_notes", ""),
        )

    def _parse_llm_response(self, response: str) -> InterpretedPreference | None:
        """Parse LLM response into InterpretedPreference.

        Args:
            response: Raw LLM response text.

        Returns:
            InterpretedPreference or None if parsing fails.
        """
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_str = response.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)

            # Map confidence string to enum
            confidence_str = data.get("confidence", "medium").lower()
            confidence_map = {
                "high": PatternConfidence.HIGH,
                "medium": PatternConfidence.MEDIUM,
                "low": PatternConfidence.LOW,
            }
            confidence = confidence_map.get(confidence_str, PatternConfidence.MEDIUM)

            return InterpretedPreference(
                genre_boosts=data.get("genre_boosts", {}),
                genre_penalties=data.get("genre_penalties", {}),
                content_type_filters=set(data.get("content_type_filters", [])),
                content_type_exclusions=set(data.get("content_type_exclusions", [])),
                length_preferences=data.get("length_preferences", {}),
                confidence=confidence,
                original_rule="",  # Will be set by caller
                interpretation_notes=data.get("notes", "LLM interpretation"),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            logger.debug(f"Failed to parse LLM response: {error}")
            return None

    def interpret_all(self, rules: list[str]) -> InterpretedPreference:
        """Interpret multiple rules using LLM with pattern fallback.

        Args:
            rules: List of rule strings to interpret.

        Returns:
            Merged InterpretedPreference from all rules.
        """
        if not rules:
            return InterpretedPreference(
                confidence=PatternConfidence.NONE,
                interpretation_notes="No rules provided",
            )

        # Check cache first
        cache_key = self._compute_cache_key(rules)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug(f"Using cached interpretation for {len(rules)} rules")
            return cached

        # Try LLM interpretation
        try:
            prompt = build_batch_interpretation_prompt(rules)
            response = self.client.generate_text(
                prompt=prompt,
                system_prompt=PREFERENCE_INTERPRETATION_SYSTEM_PROMPT,
                model=self.model,
                temperature=0.1,  # Low temperature for consistent parsing
            )

            llm_result = self._parse_llm_response(response)
            if llm_result is not None and not llm_result.is_empty():
                llm_result.original_rule = "; ".join(rules)
                self._save_to_cache(cache_key, llm_result)
                logger.info(f"LLM interpreted {len(rules)} custom rules")
                return llm_result

        except Exception as error:
            logger.warning(
                f"LLM interpretation failed, using pattern fallback: {error}"
            )

        # Fall back to pattern-based interpretation
        pattern_result = self.pattern_interpreter.interpret_all(rules)
        if not pattern_result.is_empty():
            self._save_to_cache(cache_key, pattern_result)
        return pattern_result

    def interpret(self, rule: str) -> InterpretedPreference:
        """Interpret a single rule using LLM with pattern fallback.

        Args:
            rule: The rule text to interpret.

        Returns:
            InterpretedPreference with extracted preferences.
        """
        return self.interpret_all([rule])

    def clear_cache(self) -> None:
        """Clear all cached interpretations."""
        if self.storage is not None:
            try:
                self.storage.clear_cached_preference_interpretations()
            except Exception as error:
                logger.warning(f"Failed to clear interpretation cache: {error}")
