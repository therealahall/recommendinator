"""Pre-LLM intent detection for conversation system.

Detects simple tool-action intents (mark completed, rate, add to wishlist,
save preference) from user messages using regex/keyword patterns. When a
high-confidence match is found, the tool action can be executed immediately
without invoking the LLM, saving both tokens and latency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from src.models.content import ContentItem, get_enum_value

if TYPE_CHECKING:
    from src.conversation.tools import ToolExecutor


@dataclass
class IntentResult:
    """Result of pre-LLM intent detection.

    Attributes:
        intent_type: Whether this is a tool action or a conversation
            that needs the LLM.
        tool_name: Name of the tool to execute (when intent_type is
            "tool_action").
        tool_params: Parameters for the tool call.
        confidence: How confident the detector is (0.0-1.0).
        matched_item: The content item matched from the user's library,
            if any.
    """

    intent_type: Literal["tool_action", "conversation"]
    tool_name: str | None = None
    tool_params: dict | None = None
    confidence: float = 0.0
    matched_item: ContentItem | None = None


# Patterns for "I finished X", "just completed X", "done with X", "I beat X",
# "watched X", "I read X"
_COMPLETED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:i\s+)?(?:just\s+)?(?:finished|completed|beat|done\s+with)\s+(.+?)(?:\s*[,.]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:i\s+)?(?:just\s+)?(?:watched|read|played)\s+(.+?)(?:\s*[,.]|$)",
        re.IGNORECASE,
    ),
]

# Pattern for rating: "rate X N/5", "give X N stars", "X was a N/5", "X is N/5"
_RATING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?:rate|give)\s+(.+?)\s+(\d)\s*(?:/\s*5|stars?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(.+?)\s+(?:was|is)\s+(?:a\s+)?(\d)\s*(?:/\s*5|stars?)",
        re.IGNORECASE,
    ),
]

# Pattern for rating attached to completed: "finished X, 4/5" or "beat X 5 stars"
_COMPLETED_WITH_RATING_PATTERN: re.Pattern[str] = re.compile(
    r"(?:i\s+)?(?:just\s+)?(?:finished|completed|beat|done\s+with|watched|read|played)"
    r"\s+(.+?)[,.\s]+(\d)\s*(?:/\s*5|stars?)",
    re.IGNORECASE,
)

# Pattern for preferences: "I prefer X", "I don't like X", "I love X", "I hate X"
_PREFERENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"i\s+(?:really\s+)?(?:prefer|love|enjoy|like)\s+(.+?)(?:\s*[,.]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"i\s+(?:really\s+)?(?:don'?t\s+like|hate|dislike|can'?t\s+stand)\s+(.+?)(?:\s*[,.]|$)",
        re.IGNORECASE,
    ),
]

# Minimum confidence threshold to consider a match actionable
_CONFIDENCE_THRESHOLD: float = 0.8


def detect_intent(
    message: str,
    user_id: int,
    tool_executor: ToolExecutor,
) -> IntentResult:
    """Detect tool-action intent from a user message.

    Checks the message against known patterns for completing items,
    rating items, adding to wishlist, and saving preferences. When a
    high-confidence match is found, returns a tool action intent that
    can be executed without the LLM.

    Args:
        message: The user's message text
        user_id: User ID for item resolution
        tool_executor: ToolExecutor for resolving item titles

    Returns:
        IntentResult with either a tool action or conversation intent
    """
    message_stripped = message.strip()

    # Try completed-with-rating first (most specific)
    result = _detect_completed_with_rating(message_stripped, user_id, tool_executor)
    if result.confidence >= _CONFIDENCE_THRESHOLD:
        return result

    # Try standalone rating
    result = _detect_rating(message_stripped, user_id, tool_executor)
    if result.confidence >= _CONFIDENCE_THRESHOLD:
        return result

    # Try mark completed (without rating)
    result = _detect_completed(message_stripped, user_id, tool_executor)
    if result.confidence >= _CONFIDENCE_THRESHOLD:
        return result

    # Try preference
    result = _detect_preference(message_stripped)
    if result.confidence >= _CONFIDENCE_THRESHOLD:
        return result

    # No pattern matched — let the LLM handle it
    return IntentResult(intent_type="conversation")


def _detect_completed_with_rating(
    message: str,
    user_id: int,
    tool_executor: ToolExecutor,
) -> IntentResult:
    """Detect "I finished X, N/5" pattern."""
    match = _COMPLETED_WITH_RATING_PATTERN.search(message)
    if not match:
        return IntentResult(intent_type="conversation")

    title_query = match.group(1).strip()
    rating = int(match.group(2))

    if not (1 <= rating <= 5):
        return IntentResult(intent_type="conversation")

    matched_item = _resolve_single_item(title_query, user_id, tool_executor)
    if matched_item is None:
        return IntentResult(intent_type="conversation")

    return IntentResult(
        intent_type="tool_action",
        tool_name="mark_completed",
        tool_params={"item_id": matched_item.db_id, "rating": rating},
        confidence=0.95,
        matched_item=matched_item,
    )


def _detect_completed(
    message: str,
    user_id: int,
    tool_executor: ToolExecutor,
) -> IntentResult:
    """Detect "I finished X" pattern (no rating)."""
    for pattern in _COMPLETED_PATTERNS:
        match = pattern.search(message)
        if match:
            title_query = match.group(1).strip()
            matched_item = _resolve_single_item(title_query, user_id, tool_executor)
            if matched_item is not None:
                return IntentResult(
                    intent_type="tool_action",
                    tool_name="mark_completed",
                    tool_params={"item_id": matched_item.db_id},
                    confidence=0.9,
                    matched_item=matched_item,
                )

    return IntentResult(intent_type="conversation")


def _detect_rating(
    message: str,
    user_id: int,
    tool_executor: ToolExecutor,
) -> IntentResult:
    """Detect "rate X N/5" pattern."""
    for pattern in _RATING_PATTERNS:
        match = pattern.search(message)
        if match:
            title_query = match.group(1).strip()
            rating = int(match.group(2))

            if not (1 <= rating <= 5):
                continue

            matched_item = _resolve_single_item(title_query, user_id, tool_executor)
            if matched_item is not None:
                return IntentResult(
                    intent_type="tool_action",
                    tool_name="update_rating",
                    tool_params={"item_id": matched_item.db_id, "rating": rating},
                    confidence=0.9,
                    matched_item=matched_item,
                )

    return IntentResult(intent_type="conversation")


def _detect_preference(message: str) -> IntentResult:
    """Detect "I love/hate X" preference pattern."""
    for pattern in _PREFERENCE_PATTERNS:
        match = pattern.search(message)
        if match:
            preference_text = match.group(0).strip().rstrip(",.")
            if preference_text:
                return IntentResult(
                    intent_type="tool_action",
                    tool_name="save_memory",
                    tool_params={"memory_text": preference_text},
                    confidence=0.85,
                )

    return IntentResult(intent_type="conversation")


def _resolve_single_item(
    title_query: str,
    user_id: int,
    tool_executor: ToolExecutor,
) -> ContentItem | None:
    """Resolve a title query to a single unambiguous item.

    Returns the item only if exactly one match is found. When multiple
    items match, returns None to let the LLM handle disambiguation.

    Args:
        title_query: Partial title to search for
        user_id: User ID
        tool_executor: ToolExecutor with access to storage

    Returns:
        Single matching ContentItem, or None if 0 or 2+ matches
    """
    matches = tool_executor.find_matching_items(query=title_query, user_id=user_id)

    if len(matches) == 1:
        return matches[0]

    return None


def build_confirmation_message(
    tool_name: str,
    tool_params: dict,
    matched_item: ContentItem | None = None,
) -> str:
    """Build a canned confirmation message for an intent-detected tool action.

    Args:
        tool_name: Name of the executed tool
        tool_params: Parameters used
        matched_item: The matched content item (if any)

    Returns:
        Human-readable confirmation string
    """
    title = matched_item.title if matched_item else tool_params.get("title", "item")

    if tool_name == "mark_completed":
        rating = tool_params.get("rating")
        if rating is not None:
            return f"Done! Marked **{title}** as completed with **{rating}/5**."
        return f"Done! Marked **{title}** as completed."

    if tool_name == "update_rating":
        rating = tool_params.get("rating")
        return f"Updated! **{title}** is now rated **{rating}/5**."

    if tool_name == "add_to_wishlist":
        content_type = (
            get_enum_value(matched_item.content_type).replace("_", " ")
            if matched_item
            else tool_params.get("content_type", "").replace("_", " ")
        )
        return f"Added **{title}** to your {content_type} backlog!"

    if tool_name == "save_memory":
        memory_text = tool_params.get("memory_text", "")
        return f"Noted: {memory_text}"

    return f"Done! Executed {tool_name}."
