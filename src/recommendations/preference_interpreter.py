"""Pattern-based natural language preference interpreter.

Parses common natural language rules into structured scoring adjustments.

Example rules:
- "avoid horror" -> genre penalty for horror
- "prefer sci-fi" -> genre boost for sci-fi
- "only books" -> content type filter
- "short movies" -> length preference for movies
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from src.utils.text import sanitize_rule_text

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

    Fast and predictable for well-formed rules.
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

        Sanitized first: the keys below are cut from this string, and a
        surrogate breaks whoever encodes it.

        Args:
            rule: The rule text to interpret.

        Returns:
            InterpretedPreference with extracted preferences.
        """
        rule = sanitize_rule_text(rule)
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
