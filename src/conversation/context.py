"""Context assembly for LLM conversations."""

import logging
from typing import TYPE_CHECKING

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import ConversationContext, PreferenceProfile

if TYPE_CHECKING:
    from src.conversation.memory import MemoryManager
    from src.llm.client import OllamaClient
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Builds dynamic context for LLM queries.

    Assembles relevant information from user data to provide the LLM with
    personalized context for generating recommendations and responses.
    """

    def __init__(
        self,
        storage_manager: "StorageManager",
        memory_manager: "MemoryManager",
        ollama_client: "OllamaClient | None" = None,
    ) -> None:
        """Initialize the context assembler.

        Args:
            storage_manager: Storage manager for content data
            memory_manager: Memory manager for preferences and history
            ollama_client: Optional Ollama client for embeddings
        """
        self.storage = storage_manager
        self.memory = memory_manager
        self.ollama = ollama_client

    def assemble_context(
        self,
        user_id: int,
        user_query: str,
        content_type: ContentType | None = None,
        max_memories: int = 20,
        max_history_messages: int = 10,
        max_relevant_items: int = 10,
        max_unconsumed_items: int = 20,
        include_algorithmic_recs: bool = False,
    ) -> ConversationContext:
        """Assemble context for an LLM conversation turn.

        Args:
            user_id: User ID
            user_query: The user's message/query
            content_type: Optional filter for content type
            max_memories: Maximum core memories to include
            max_history_messages: Maximum recent messages to include
            max_relevant_items: Maximum relevant completed items via RAG
            max_unconsumed_items: Maximum unconsumed items to include
            include_algorithmic_recs: Whether to include algorithmic recommendations

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

        # Get relevant completed items (high-rated items similar to query)
        relevant_completed = self._retrieve_relevant_items(
            query=user_query,
            user_id=user_id,
            content_type=content_type,
            limit=max_relevant_items,
        )

        # Get unconsumed items that might match
        relevant_unconsumed = self._get_unconsumed_items(
            user_id=user_id,
            content_type=content_type,
            limit=max_unconsumed_items,
        )

        # Get preference profile and build summary
        preference_summary = self._build_profile_summary(user_id)

        # Optionally get algorithmic recommendations
        algorithmic_recommendations = None
        if include_algorithmic_recs and content_type:
            algorithmic_recommendations = self._get_algorithmic_recommendations(
                user_id=user_id,
                content_type=content_type,
            )

        return ConversationContext(
            user_id=user_id,
            core_memories=core_memories,
            recent_messages=recent_messages,
            relevant_completed=relevant_completed,
            relevant_unconsumed=relevant_unconsumed,
            preference_summary=preference_summary,
            algorithmic_recommendations=algorithmic_recommendations,
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

    def _get_unconsumed_items(
        self,
        user_id: int,
        content_type: ContentType | None = None,
        limit: int = 20,
    ) -> list[ContentItem]:
        """Get unconsumed items from the user's backlog.

        Args:
            user_id: User ID
            content_type: Optional content type filter
            limit: Maximum items to return

        Returns:
            List of unconsumed ContentItem objects
        """
        items = self.storage.get_unconsumed_items(
            user_id=user_id,
            content_type=content_type,
            limit=limit,
        )

        # Filter out ignored items
        return [item for item in items if not item.ignored]

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

        user_stated = [m for m in memories if m.memory_type == "user_stated"]
        inferred = [m for m in memories if m.memory_type == "inferred"]

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
                f"{genre} ({score:.0%})" for genre, score in sorted_genres[:5]
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

    def _get_algorithmic_recommendations(
        self,
        user_id: int,
        content_type: ContentType,
        count: int = 5,
    ) -> list | None:
        """Get recommendations from the algorithmic scoring pipeline.

        Args:
            user_id: User ID
            content_type: Content type for recommendations
            count: Number of recommendations

        Returns:
            List of ScoredCandidate or None if unavailable
        """
        # This would integrate with the existing recommendation engine
        # For now, return None to indicate feature is not yet integrated
        # TODO: Integrate with RecommendationEngine once context is wired up
        return None


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
        for item in context.relevant_completed[:5]:
            rating_str = f" ({item.rating}/5)" if item.rating else ""
            author_str = f" by {item.author}" if item.author else ""
            parts.append(f"- {item.title}{author_str}{rating_str}")
        parts.append("")

    # Unconsumed items (backlog)
    if context.relevant_unconsumed:
        parts.append("## Available in Backlog")
        for item in context.relevant_unconsumed[:15]:
            author_str = f" by {item.author}" if item.author else ""
            content_type_str = str(item.content_type).replace("_", " ").title()
            parts.append(f"- [{content_type_str}] {item.title}{author_str}")
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
