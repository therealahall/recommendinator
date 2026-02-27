"""Conversation and memory data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from src.models.content import ContentItem


@dataclass
class CoreMemory:
    """A significant preference signal or memory about the user.

    Core memories capture important preference information that should persist
    across conversations. They can be user-stated (explicit) or inferred (derived
    from patterns in user behavior).

    Examples:
        - User-stated: "I don't like slow burns"
        - Inferred: "Tends to abandon games with grinding mechanics"
    """

    user_id: int
    memory_text: str
    memory_type: Literal["user_stated", "inferred"]
    source: str  # "conversation", "rating_pattern", "manual"
    id: int | None = None
    confidence: float = 1.0  # 0.0-1.0 for inferred memories
    is_active: bool = True  # User can deactivate inferred memories
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ConversationMessage:
    """A single message in a conversation.

    Stores both user and assistant messages along with any tool calls
    made during the conversation turn.
    """

    user_id: int
    role: Literal["user", "assistant"]
    content: str
    id: int | None = None
    tool_calls: list[dict[str, Any]] | None = (
        None  # JSON-serializable list of tool calls
    )
    created_at: datetime | None = None


@dataclass
class PreferenceProfile:
    """A distilled summary of user preferences.

    Generated periodically from user data including ratings, reviews,
    completions, and core memories. Used to provide context to the LLM.
    """

    user_id: int
    genre_affinities: dict[str, float] = field(
        default_factory=dict
    )  # {"sci-fi": 0.8, "fantasy": 0.6}
    theme_preferences: list[str] = field(
        default_factory=list
    )  # ["exploration", "narrative depth"]
    anti_preferences: list[str] = field(
        default_factory=list
    )  # ["grinding", "precision platformers"]
    cross_media_patterns: list[str] = field(
        default_factory=list
    )  # ["loves sci-fi books but prefers fantasy games"]
    generated_at: datetime | None = None


@dataclass
class ToolResult:
    """Result from executing a conversation tool.

    Tools allow the LLM to take actions like marking items completed,
    updating ratings, or requesting clarification when multiple items
    match a query.
    """

    success: bool
    message: str
    data: dict[str, Any] | None = None
    needs_clarification: bool = False
    clarification_options: list[dict[str, Any]] | None = None  # For ambiguous matches


@dataclass
class ConversationChunk:
    """A chunk of streaming conversation output.

    Used to stream responses back to the UI, including text, tool calls,
    tool results, and memory extraction notifications.
    """

    chunk_type: Literal["text", "tool_call", "tool_result", "memory_extracted", "done"]
    content: str | None = None
    tool_name: str | None = None
    tool_params: dict[str, Any] | None = None
    tool_result: ToolResult | None = None
    memory: CoreMemory | None = None


@dataclass
class RecommendationBrief:
    """Pre-scored recommendation with pipeline-computed reasoning.

    Carries all the data the pipeline already computed — score, reasoning,
    per-scorer breakdown, contributing reference items, and cross-media
    adaptations — so the LLM can present and explain rather than
    independently re-analyze.
    """

    item: ContentItem
    score: float  # [0, 1] final ranked score
    reasoning: str  # "Recommended because you liked..."
    score_breakdown: dict[str, float]  # {genre_match: 0.85, ...}
    contributing_items: list[ContentItem]  # consumed items that influenced this
    adaptations: list[ContentItem]  # cross-media connections
    similarity_score: float = 0.0
    preference_score: float = 0.0


@dataclass
class ConversationContext:
    """Assembled context for an LLM conversation turn.

    Contains all the relevant information needed for the LLM to generate
    a personalized response, including memories, history, and relevant items.
    """

    user_id: int
    core_memories: list[CoreMemory] = field(default_factory=list)
    recent_messages: list[ConversationMessage] = field(default_factory=list)
    relevant_completed: list[ContentItem] = field(default_factory=list)
    relevant_unconsumed: list[ContentItem] = field(default_factory=list)
    preference_summary: str = ""
    recommendation_briefs: list[RecommendationBrief] | None = None
