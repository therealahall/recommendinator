"""Context assembly for LLM conversations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import (
    ConversationContext,
    PreferenceProfile,
    RecommendationBrief,
)
from src.utils.series import build_series_tracking, should_recommend_item

if TYPE_CHECKING:
    from src.conversation.memory import MemoryManager
    from src.llm.client import OllamaClient
    from src.recommendations.engine import RecommendationEngine
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Builds dynamic context for LLM queries.

    Assembles relevant information from user data to provide the LLM with
    personalized context for generating recommendations and responses.
    """

    def __init__(
        self,
        storage_manager: StorageManager,
        memory_manager: MemoryManager,
        ollama_client: OllamaClient | None = None,
        recommendation_engine: RecommendationEngine | None = None,
    ) -> None:
        """Initialize the context assembler.

        Args:
            storage_manager: Storage manager for content data
            memory_manager: Memory manager for preferences and history
            ollama_client: Optional Ollama client for embeddings
            recommendation_engine: Optional recommendation engine for
                pre-filtered, scored backlog items. When provided and a
                content_type is specified, the pipeline's series-filtered
                and scored output replaces the raw unconsumed-items query.
        """
        self.storage = storage_manager
        self.memory = memory_manager
        self.ollama = ollama_client
        self.recommendation_engine = recommendation_engine

    def assemble_context(
        self,
        user_id: int,
        user_query: str,
        content_type: ContentType | None = None,
        max_memories: int = 20,
        max_history_messages: int = 10,
        max_relevant_items: int = 10,
        max_unconsumed_items: int = 20,
    ) -> ConversationContext:
        """Assemble context for an LLM conversation turn.

        When the recommendation pipeline is available and a content_type is
        specified, the pipeline produces RecommendationBriefs which carry
        pre-computed reasoning, scores, and contributing items. The
        contributing items replace the RAG embedding lookup, saving 1-3s.

        Args:
            user_id: User ID
            user_query: The user's message/query
            content_type: Optional filter for content type
            max_memories: Maximum core memories to include
            max_history_messages: Maximum recent messages to include
            max_relevant_items: Maximum relevant completed items via RAG
            max_unconsumed_items: Maximum unconsumed items to include

        Returns:
            ConversationContext with all assembled data
        """
        # Get active core memories
        core_memories = self.memory.get_core_memories(
            user_id=user_id, active_only=True
        )[:max_memories]

        # Get recent conversation history
        recent_messages = self.memory.get_conversation_history(
            user_id=user_id, limit=max_history_messages
        )

        # Try pipeline → RecommendationBriefs (includes contributing items)
        briefs = self._get_recommendation_briefs_from_pipeline(
            user_id=user_id,
            content_type=content_type,
            limit=max_unconsumed_items,
        )

        if briefs is not None:
            # Pipeline provided briefs — extract unconsumed + contributing items
            relevant_unconsumed = [brief.item for brief in briefs]
            relevant_completed = _extract_contributing_items(
                briefs, limit=max_relevant_items
            )
        else:
            # No pipeline — fall back to storage + RAG
            relevant_unconsumed = self._get_unconsumed_items_fallback(
                user_id=user_id,
                content_type=content_type,
                limit=max_unconsumed_items,
            )
            relevant_completed = self._retrieve_relevant_items(
                query=user_query,
                user_id=user_id,
                content_type=content_type,
                limit=max_relevant_items,
            )

        # Get preference profile and build summary
        preference_summary = self._build_profile_summary(user_id)

        return ConversationContext(
            user_id=user_id,
            core_memories=core_memories,
            recent_messages=recent_messages,
            relevant_completed=relevant_completed,
            relevant_unconsumed=relevant_unconsumed,
            preference_summary=preference_summary,
            recommendation_briefs=briefs,
        )

    def _retrieve_relevant_items(
        self,
        query: str,
        user_id: int,
        content_type: ContentType | None = None,
        limit: int = 10,
    ) -> list[ContentItem]:
        """Retrieve completed items relevant to the query using RAG.

        Uses embedding similarity to find items the user has completed
        and rated highly that are semantically similar to their query.

        Args:
            query: User query to match against
            user_id: User ID
            content_type: Optional content type filter
            limit: Maximum items to return

        Returns:
            List of relevant ContentItem objects
        """
        # If vector search is available, use it
        if self.ollama and self.storage.vector_db:
            try:
                # Generate query embedding
                query_embedding = self.ollama.generate_embedding(query)

                # Search for similar items
                results = self.storage.search_similar(
                    query_embedding=query_embedding,
                    user_id=user_id,
                    n_results=limit * 2,  # Get more to filter
                    content_type=content_type,
                    exclude_consumed=False,  # We want completed items
                )

                # Get full content items for matches
                relevant_items = []
                for result in results:
                    content_id = result.get("content_id", "")
                    # Try to find by external ID
                    if content_id.startswith("db_"):
                        db_id = int(content_id[3:])
                        item = self.storage.get_content_item(db_id, user_id=user_id)
                    else:
                        item = self.storage.get_content_item_by_external_id(
                            external_id=content_id,
                            content_type=content_type or ContentType.BOOK,
                            user_id=user_id,
                        )

                    if item and item.status == ConsumptionStatus.COMPLETED:
                        if item.rating and item.rating >= 3:  # Include decent items
                            relevant_items.append(item)

                    if len(relevant_items) >= limit:
                        break

                return relevant_items

            except Exception as error:
                logger.warning(f"RAG retrieval failed, falling back to simple: {error}")

        # Fallback: return high-rated completed items
        return self._get_high_rated_items(
            user_id=user_id,
            content_type=content_type,
            limit=limit,
        )

    def _get_high_rated_items(
        self,
        user_id: int,
        content_type: ContentType | None = None,
        limit: int = 10,
    ) -> list[ContentItem]:
        """Get high-rated completed items as fallback for RAG.

        Args:
            user_id: User ID
            content_type: Optional content type filter
            limit: Maximum items to return

        Returns:
            List of high-rated ContentItem objects
        """
        return self.storage.get_completed_items(
            user_id=user_id,
            content_type=content_type,
            min_rating=4,
            limit=limit,
        )

    def _get_recommendation_briefs_from_pipeline(
        self,
        user_id: int,
        content_type: ContentType | None,
        limit: int = 20,
    ) -> list[RecommendationBrief] | None:
        """Get pre-scored recommendation briefs from the pipeline.

        When the recommendation engine is available and a content_type is
        specified, runs the full scoring pipeline and converts each output
        dict into a RecommendationBrief carrying scores, reasoning,
        contributing items, and cross-media adaptations.

        Args:
            user_id: User ID
            content_type: Content type to recommend (required for pipeline)
            limit: Maximum briefs to return

        Returns:
            List of RecommendationBrief objects, or None if the pipeline
            is unavailable or fails.
        """
        if self.recommendation_engine is None or content_type is None:
            return None

        try:
            user_preference_config = self.storage.get_user_preference_config(user_id)
            recommendations: list[dict[str, Any]] = (
                self.recommendation_engine.generate_recommendations(
                    content_type=content_type,
                    count=limit,
                    use_llm=False,
                    user_preference_config=user_preference_config,
                )
            )

            briefs = [
                RecommendationBrief(
                    item=rec["item"],
                    score=rec.get("score", 0.0),
                    reasoning=rec.get("reasoning", ""),
                    score_breakdown=rec.get("score_breakdown", {}),
                    contributing_items=rec.get("contributing_items", []),
                    adaptations=rec.get("adaptations", []),
                    similarity_score=rec.get("similarity_score", 0.0),
                    preference_score=rec.get("preference_score", 0.0),
                )
                for rec in recommendations
            ]
            logger.info(
                "Pipeline returned %d briefs for %s backlog",
                len(briefs),
                content_type.value,
            )
            return briefs

        except Exception as error:
            logger.warning(
                "Recommendation pipeline failed, falling back to storage: %s",
                error,
            )
            return None

    def _get_unconsumed_items_fallback(
        self,
        user_id: int,
        content_type: ContentType | None = None,
        limit: int = 20,
    ) -> list[ContentItem]:
        """Fallback: get unconsumed items from storage with basic filtering.

        Used when no recommendation engine is available or when content_type
        is not specified.

        Args:
            user_id: User ID
            content_type: Optional content type filter
            limit: Maximum items to return

        Returns:
            List of unconsumed ContentItem objects
        """
        # Fetch all unconsumed items for accurate series filtering
        items = self.storage.get_unconsumed_items(
            user_id=user_id,
            content_type=content_type,
        )

        # Filter out ignored items
        non_ignored = [item for item in items if not item.ignored]

        # Build series tracking from completed items to enforce series order
        completed_items = self.storage.get_completed_items(
            user_id=user_id,
            content_type=content_type,
        )
        series_tracking = build_series_tracking(completed_items)

        # Filter by series ordering rules
        recommendable = [
            item
            for item in non_ignored
            if should_recommend_item(
                item, series_tracking, unconsumed_items=non_ignored
            )
        ]

        return recommendable[:limit]

    def _build_profile_summary(self, user_id: int) -> str:
        """Build a text summary of the user's preference profile.

        Args:
            user_id: User ID

        Returns:
            Formatted preference summary string
        """
        profile = self.memory.get_preference_profile(user_id)

        if not profile:
            # Build a basic profile from core memories if no profile exists
            return self._build_summary_from_memories(user_id)

        return self._format_profile(profile)

    def _build_summary_from_memories(self, user_id: int) -> str:
        """Build a preference summary from core memories.

        Args:
            user_id: User ID

        Returns:
            Formatted summary from memories
        """
        memories = self.memory.get_core_memories(user_id=user_id, active_only=True)

        if not memories:
            return "No preference profile available yet."

        user_stated = [
            memory for memory in memories if memory.memory_type == "user_stated"
        ]
        inferred = [memory for memory in memories if memory.memory_type == "inferred"]

        parts = []

        if user_stated:
            parts.append("User preferences:")
            for memory in user_stated[:5]:
                parts.append(f"  - {memory.memory_text}")

        if inferred:
            parts.append("\nObserved patterns:")
            for memory in inferred[:5]:
                confidence_text = (
                    f" (confidence: {memory.confidence:.0%})"
                    if memory.confidence < 1.0
                    else ""
                )
                parts.append(f"  - {memory.memory_text}{confidence_text}")

        return "\n".join(parts)

    def _format_profile(self, profile: PreferenceProfile) -> str:
        """Format a preference profile as text.

        Args:
            profile: PreferenceProfile to format

        Returns:
            Formatted profile string
        """
        parts = []

        if profile.genre_affinities:
            sorted_genres = sorted(
                profile.genre_affinities.items(), key=lambda x: x[1], reverse=True
            )
            top_genres = [
                f"{genre} ({score:.1f}\u2605)" for genre, score in sorted_genres[:5]
            ]
            parts.append(f"Top genres: {', '.join(top_genres)}")

        if profile.theme_preferences:
            parts.append(
                f"Preferred themes: {', '.join(profile.theme_preferences[:5])}"
            )

        if profile.anti_preferences:
            parts.append(f"Dislikes: {', '.join(profile.anti_preferences[:5])}")

        if profile.cross_media_patterns:
            parts.append(
                f"Cross-media patterns: {'; '.join(profile.cross_media_patterns[:3])}"
            )

        return "\n".join(parts) if parts else "No detailed profile available."


def _extract_contributing_items(
    briefs: list[RecommendationBrief],
    limit: int = 10,
) -> list[ContentItem]:
    """Deduplicate contributing items from across all briefs.

    Collects the consumed items that influenced each recommendation,
    deduplicates by item ID, and returns up to ``limit`` items. This
    replaces the RAG embedding lookup when pipeline briefs are available.

    Args:
        briefs: Recommendation briefs from the pipeline
        limit: Maximum contributing items to return

    Returns:
        Deduplicated list of ContentItem objects
    """
    seen_ids: set[str | int | None] = set()
    contributing: list[ContentItem] = []

    for brief in briefs:
        for item in brief.contributing_items:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                contributing.append(item)

    return contributing[:limit]


def _format_content_type(content_type: ContentType) -> str:
    """Format a ContentType enum as a human-readable title string.

    Example: ContentType.VIDEO_GAME -> "Video Game"
    """
    return str(content_type).replace("_", " ").title()


def _format_item_detail(item: ContentItem) -> str:
    """Format a content item with rich detail for LLM context.

    Includes type, title, author, rating, genres, and review snippet
    so the LLM can reference specific details about the user's experience.

    Args:
        item: ContentItem to format

    Returns:
        Formatted detail string
    """
    content_type_str = _format_content_type(item.content_type)
    author_str = f" by {item.author}" if item.author else ""
    rating_str = f" — {item.rating}/5" if item.rating is not None else ""

    genres = item.metadata.get("genres", [])
    genre_str = f" [{', '.join(genres[:4])}]" if genres else ""

    line = f"- [{content_type_str}] {item.title}{author_str}{rating_str}{genre_str}"

    # Include review snippet if available (truncated)
    if item.review:
        review_snippet = item.review[:150]
        if len(item.review) > 150:
            review_snippet += "..."
        line += f'\n  Review: "{review_snippet}"'

    return line


def _format_recommendation_brief(brief: RecommendationBrief) -> str:
    """Format a recommendation brief with match score, reasoning, and connections.

    Produces an enriched block so the LLM can present and explain the
    recommendation rather than independently re-analyzing it.

    Args:
        brief: Pre-scored recommendation brief from the pipeline

    Returns:
        Formatted multi-line string for LLM context
    """
    item = brief.item
    content_type_str = _format_content_type(item.content_type)
    author_str = f" by {item.author}" if item.author else ""

    genres = item.metadata.get("genres", [])
    genre_str = f" [{', '.join(genres[:4])}]" if genres else ""

    match_percent = round(brief.score * 100)
    lines = [
        f"- [{content_type_str}] {item.title}{author_str}{genre_str}",
        f"  Match: {match_percent}%",
    ]

    if brief.reasoning:
        lines.append(f"  Why: {brief.reasoning}")

    # Top scoring dimensions
    if brief.score_breakdown:
        top_dimensions = sorted(
            brief.score_breakdown.items(), key=lambda entry: entry[1], reverse=True
        )[:3]
        strengths = ", ".join(
            f"{name}: {round(value * 100)}%" for name, value in top_dimensions
        )
        lines.append(f"  Strengths: {strengths}")

    # Cross-media connections
    if brief.adaptations:
        adaptation_titles = [
            f"{adaptation.title} ({_format_content_type(adaptation.content_type)})"
            for adaptation in brief.adaptations[:2]
        ]
        lines.append(f"  Cross-media: {', '.join(adaptation_titles)}")

    return "\n".join(lines)


def _format_item_compact(item: ContentItem) -> str:
    """Format a content item in compact form for small-model context.

    Includes only type tag, title, author, and rating — no genres or review
    snippets, saving ~50% per item compared to ``_format_item_detail``.

    Args:
        item: ContentItem to format

    Returns:
        Single-line compact string
    """
    content_type_str = _format_content_type(item.content_type)
    author_str = f" by {item.author}" if item.author else ""
    rating_str = f" — {item.rating}/5" if item.rating is not None else ""
    return f"- [{content_type_str}] {item.title}{author_str}{rating_str}"


def _format_recommendation_brief_compact(brief: RecommendationBrief) -> str:
    """Format a recommendation brief in compact form for small-model context.

    Includes title, author, match percentage, and one-line reasoning.
    Omits score breakdowns and cross-media connections.

    Args:
        brief: Pre-scored recommendation brief from the pipeline

    Returns:
        Compact formatted string (1-2 lines)
    """
    item = brief.item
    content_type_str = _format_content_type(item.content_type)
    author_str = f" by {item.author}" if item.author else ""
    match_percent = round(brief.score * 100)

    line = f"- [{content_type_str}] {item.title}{author_str} — {match_percent}% match"
    if brief.reasoning:
        # Truncate reasoning to first sentence or 100 chars
        short_reason = brief.reasoning[:100]
        if len(brief.reasoning) > 100:
            short_reason = short_reason.rsplit(" ", 1)[0] + "..."
        line += f"\n  {short_reason}"
    return line


def build_user_context_block_compact(context: ConversationContext) -> str:
    """Build a compact context block optimized for small (3B) models.

    Uses fewer items and shorter formatting than
    :func:`build_user_context_block` to reduce token count by ~50%.

    Args:
        context: Assembled conversation context

    Returns:
        Compact formatted context string
    """
    parts = []

    # Preference summary (kept as-is — already compact)
    if context.preference_summary:
        parts.append("## Profile")
        parts.append(context.preference_summary)
        parts.append("")

    # Core memories (max 5)
    if context.core_memories:
        parts.append("## Preferences")
        for memory in context.core_memories[:5]:
            parts.append(f"- {memory.memory_text}")
        parts.append("")

    # Completed items (max 5, compact format)
    if context.relevant_completed:
        parts.append("## Completed")
        for item in context.relevant_completed[:5]:
            parts.append(_format_item_compact(item))
        parts.append("")

    # Backlog — enriched when briefs available (max 5)
    if context.recommendation_briefs:
        parts.append("## Recommended (Pre-Scored)")
        for brief in context.recommendation_briefs[:5]:
            parts.append(_format_recommendation_brief_compact(brief))
        parts.append("")
    elif context.relevant_unconsumed:
        parts.append("## Backlog")
        for item in context.relevant_unconsumed[:5]:
            parts.append(_format_item_compact(item))
        parts.append("")

    # Recent conversation (last 3, truncated to 100 chars)
    if context.recent_messages:
        parts.append("## Recent Chat")
        for message in context.recent_messages[-3:]:
            role_label = "User" if message.role == "user" else "You"
            content = message.content[:100]
            if len(message.content) > 100:
                content += "..."
            parts.append(f"{role_label}: {content}")
        parts.append("")

    return "\n".join(parts)


def build_user_context_block(context: ConversationContext) -> str:
    """Build a formatted context block for injection into LLM prompts.

    Args:
        context: Assembled conversation context

    Returns:
        Formatted context string for the LLM
    """
    parts = []

    # Preference summary
    if context.preference_summary:
        parts.append("## User Profile")
        parts.append(context.preference_summary)
        parts.append("")

    # Core memories
    if context.core_memories:
        parts.append("## Key Preferences & Memories")
        for memory in context.core_memories[:10]:
            memory_type_label = (
                "[stated]" if memory.memory_type == "user_stated" else "[observed]"
            )
            parts.append(f"- {memory_type_label} {memory.memory_text}")
        parts.append("")

    # Relevant completed items (what they loved)
    if context.relevant_completed:
        parts.append("## Recently Completed (High-Rated)")
        for item in context.relevant_completed[:10]:
            parts.append(_format_item_detail(item))
        parts.append("")

    # Unconsumed items (backlog) — enriched when briefs available
    if context.recommendation_briefs:
        parts.append(
            "## Recommended From Backlog (Pre-Scored)\n"
            "Items are ranked by match quality. "
            "Use the reasoning and scores to explain your picks."
        )
        for brief in context.recommendation_briefs[:10]:
            parts.append(_format_recommendation_brief(brief))
        parts.append("")
    elif context.relevant_unconsumed:
        parts.append("## Available in Backlog (Recommend FROM This List)")
        for item in context.relevant_unconsumed[:15]:
            parts.append(_format_item_detail(item))
        parts.append("")

    # Recent conversation for continuity
    if context.recent_messages:
        parts.append("## Recent Conversation")
        for message in context.recent_messages[-5:]:
            role_label = "User" if message.role == "user" else "Assistant"
            # Truncate long messages
            content = message.content
            if len(content) > 200:
                content = content[:200] + "..."
            parts.append(f"{role_label}: {content}")
        parts.append("")

    return "\n".join(parts)
