"""Unified storage manager for SQLite and optionally ChromaDB."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.conflict import ConflictStrategy, resolve_conflict
from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.storage.schema import (
    ConversationMessageDict,
    CoreMemoryDict,
    EnrichmentStatusDict,
    UserDict,
    clear_cached_preference_interpretations,
    clear_conversation_history,
    delete_core_memory,
    get_all_users,
    get_cached_preference_interpretation,
    get_conversation_history,
    get_core_memories,
    get_enrichment_stats,
    get_enrichment_status,
    get_preference_profile,
    get_user_by_id,
    mark_enrichment_complete,
    mark_enrichment_failed,
    mark_item_needs_enrichment,
    reset_enrichment_status,
    save_cached_preference_interpretation,
    save_conversation_message,
    save_core_memory,
    save_preference_profile,
    update_core_memory,
    update_user_settings,
)

# Re-exported so consumers import from storage.manager rather than the
# internal sqlite_db module.  The `as VALID_SORT_OPTIONS` form marks
# the name as an intentional public re-export for type checkers.
from src.storage.sqlite_db import VALID_SORT_OPTIONS as VALID_SORT_OPTIONS
from src.storage.sqlite_db import SQLiteDB

if TYPE_CHECKING:
    from src.storage.vector_db import VectorDB


class StorageManager:
    """Unified storage manager for SQLite and optionally ChromaDB.

    When ai_enabled is False (default), only SQLite is used.
    When ai_enabled is True, ChromaDB is also initialized for embeddings.
    """

    def __init__(
        self,
        sqlite_path: Path,
        vector_db_path: Path | None = None,
        vector_collection_name: str = "content_embeddings",
        ai_enabled: bool = False,
        conflict_strategy: ConflictStrategy = ConflictStrategy.LAST_WRITE_WINS,
        source_priority: list[str] | None = None,
    ) -> None:
        """Initialize storage manager.

        Args:
            sqlite_path: Path to SQLite database file
            vector_db_path: Path to ChromaDB database directory (optional)
            vector_collection_name: Name of ChromaDB collection
            ai_enabled: Whether to enable AI features (embeddings)
            conflict_strategy: Strategy for resolving duplicate content items
            source_priority: Ordered list of source names (highest priority first)
        """
        self.sqlite_db = SQLiteDB(sqlite_path)
        self.vector_db: VectorDB | None = None
        self.ai_enabled = ai_enabled
        self.conflict_strategy = conflict_strategy
        self.source_priority = source_priority or []

        # Only initialize vector DB if AI is enabled and path provided.
        # Deferred import: chromadb is heavy (~500 MB+) and should not load
        # when AI features are disabled.
        if ai_enabled and vector_db_path:
            from src.storage.vector_db import VectorDB

            self.vector_db = VectorDB(vector_db_path, vector_collection_name)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a managed SQLite connection.

        Delegates to the underlying SQLiteDB connection context manager.
        """
        with self.sqlite_db.connection() as conn:
            yield conn

    def save_content_item(
        self,
        item: ContentItem,
        user_id: int | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        """Save a content item to SQLite and optionally ChromaDB.

        If the item has an external ID, checks for an existing item with the
        same external ID and content type. If found, applies the configured
        conflict resolution strategy before saving.

        Args:
            item: ContentItem to save
            user_id: User ID (defaults to item.user_id)
            embedding: Optional embedding vector to store (requires ai_enabled)

        Returns:
            Database ID of the saved item
        """
        # Apply conflict resolution if item has an external ID
        resolved_item = item
        if item.id:
            existing = self.get_content_item_by_external_id(
                external_id=item.id,
                content_type=ContentType(item.content_type),
                user_id=user_id if user_id is not None else item.user_id,
            )
            if existing is not None:
                resolved_item = resolve_conflict(
                    existing=existing,
                    incoming=item,
                    strategy=self.conflict_strategy,
                    source_priority=self.source_priority,
                )

        # Save to SQLite
        db_id = self.sqlite_db.save_content_item(resolved_item, user_id=user_id)

        # Save embedding if provided and vector DB is enabled
        if embedding is not None and self.vector_db:
            # Use external_id if available, otherwise use db_id as string
            content_id = item.id if item.id else f"db_{db_id}"

            metadata = {
                "content_type": get_enum_value(item.content_type),
                "title": item.title,
                "author": item.author or "",
                "status": get_enum_value(item.status),
                "user_id": str(user_id or item.user_id),
            }
            self.vector_db.add_embedding(content_id, embedding, metadata)

        return db_id

    def get_content_item(
        self, db_id: int, user_id: int | None = None
    ) -> ContentItem | None:
        """Get a content item by database ID.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter

        Returns:
            ContentItem if found, None otherwise
        """
        return self.sqlite_db.get_content_item(db_id, user_id=user_id)

    def get_content_items_by_db_ids(self, db_ids: list[int]) -> list[ContentItem]:
        """Get multiple content items by their database IDs in a single query.

        Args:
            db_ids: List of database IDs to fetch

        Returns:
            List of ContentItem objects found
        """
        return self.sqlite_db.get_content_items_by_db_ids(db_ids)

    def get_content_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | list[ConsumptionStatus] | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
        offset: int = 0,
        sort_by: str = "title",
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        """Get content items with optional filters.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            status: Filter by consumption status (single value or list for
                IN-clause filtering)
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results
            offset: Number of results to skip (for pagination)
            sort_by: Sort order - "title" (default, ignores articles),
                "updated_at", "rating", or "created_at"
            include_ignored: Whether to include ignored items (default True
                for backward compatibility)

        Returns:
            List of ContentItem objects
        """
        return self.sqlite_db.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=status,
            min_rating=min_rating,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            include_ignored=include_ignored,
        )

    def get_unconsumed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get unconsumed items (status = UNREAD or CURRENTLY_CONSUMING).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            limit: Maximum number of results

        Returns:
            List of unconsumed ContentItem objects
        """
        return self.sqlite_db.get_unconsumed_items(
            user_id=user_id, content_type=content_type, limit=limit
        )

    def get_completed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get completed items (status = COMPLETED or CURRENTLY_CONSUMING).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of completed ContentItem objects
        """
        return self.sqlite_db.get_completed_items(
            user_id=user_id,
            content_type=content_type,
            min_rating=min_rating,
            limit=limit,
        )

    def search_similar(
        self,
        query_embedding: list[float],
        user_id: int | None = None,
        n_results: int = 10,
        content_type: ContentType | None = None,
        exclude_consumed: bool = True,
    ) -> list[dict[str, Any]]:
        """Search for similar content using vector similarity.

        Requires ai_enabled=True.

        Args:
            query_embedding: Query embedding vector
            user_id: Filter by user ID
            n_results: Number of results to return
            content_type: Optional filter by content type
            exclude_consumed: If True, exclude consumed items

        Returns:
            List of similar content items with scores and metadata

        Raises:
            RuntimeError: If called when AI is not enabled
        """
        if not self.vector_db:
            raise RuntimeError(
                "Vector search requires ai_enabled=True in StorageManager"
            )

        # Get consumed item IDs to exclude
        exclude_ids: list[str] | None = None
        if exclude_consumed:
            consumed = self.get_completed_items(
                user_id=user_id, content_type=content_type
            )
            exclude_ids = [item.id for item in consumed if item.id]

        content_type_str = get_enum_value(content_type) if content_type else None

        results = self.vector_db.search_similar(
            query_embedding=query_embedding,
            n_results=n_results,
            content_type=content_type_str,
            exclude_ids=exclude_ids,
        )

        return results

    def delete_content_item(self, db_id: int, user_id: int | None = None) -> bool:
        """Delete a content item from both databases.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter

        Returns:
            True if item was deleted, False if not found
        """
        # Get item first to find its external_id
        item = self.sqlite_db.get_content_item(db_id, user_id=user_id)
        if not item:
            return False

        # Delete from SQLite
        deleted = self.sqlite_db.delete_content_item(db_id, user_id=user_id)

        # Delete embedding if it exists and vector DB is enabled
        if deleted and item.id and self.vector_db:
            self.vector_db.delete_embedding(item.id)

        return deleted

    def set_item_ignored(
        self, db_id: int, ignored: bool, user_id: int | None = None
    ) -> bool:
        """Set the ignored status of a content item.

        Ignored items are excluded from recommendations.

        Args:
            db_id: Database ID of the item
            ignored: Whether the item should be ignored
            user_id: Optional user ID filter (for security)

        Returns:
            True if item was updated, False if not found
        """
        return self.sqlite_db.set_item_ignored(db_id, ignored, user_id=user_id)

    def update_item_from_ui(
        self,
        db_id: int,
        status: str,
        rating: int | None = None,
        review: str | None = None,
        seasons_watched: list[int] | None = None,
        user_id: int | None = None,
    ) -> bool:
        """Update a content item from the web UI (unrestricted editing).

        Delegates to SQLiteDB.update_item_from_ui which allows full editing
        without the forward-only/set-once constraints of save_content_item.

        Args:
            db_id: Database ID of the item to update.
            status: New status value.
            rating: New rating (1-5) or None to clear.
            review: New review text or None to clear.
            seasons_watched: List of watched season numbers (TV shows only).
            user_id: Optional user ID filter for authorization.

        Returns:
            True if item was updated, False if not found.
        """
        return self.sqlite_db.update_item_from_ui(
            db_id=db_id,
            status=status,
            rating=rating,
            review=review,
            seasons_watched=seasons_watched,
            user_id=user_id,
        )

    def count_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
    ) -> int:
        """Count content items with optional filters.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            status: Filter by consumption status

        Returns:
            Number of matching items
        """
        return self.sqlite_db.count_items(
            user_id=user_id, content_type=content_type, status=status
        )

    def add_embedding(
        self,
        content_id: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add or update an embedding for a content item.

        Requires ai_enabled=True.

        Args:
            content_id: Unique identifier for the content item
            embedding: Vector embedding
            metadata: Optional metadata dictionary

        Raises:
            RuntimeError: If called when AI is not enabled
        """
        if not self.vector_db:
            raise RuntimeError("Embeddings require ai_enabled=True in StorageManager")
        self.vector_db.add_embedding(content_id, embedding, metadata)

    def get_embedding(self, content_id: str) -> list[float] | None:
        """Get embedding for a content item.

        Requires ai_enabled=True.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            Embedding vector if found, None otherwise

        Raises:
            RuntimeError: If called when AI is not enabled
        """
        if not self.vector_db:
            raise RuntimeError("Embeddings require ai_enabled=True in StorageManager")
        return self.vector_db.get_embedding(content_id)

    def has_embedding(self, content_id: str) -> bool:
        """Check if an embedding exists for a content item.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            True if embedding exists, False otherwise (or if AI disabled)
        """
        if not self.vector_db:
            return False
        return self.vector_db.has_embedding(content_id)

    def get_content_item_by_external_id(
        self,
        external_id: str,
        content_type: ContentType,
        user_id: int | None = None,
    ) -> ContentItem | None:
        """Get a content item by external ID and content type.

        Args:
            external_id: External ID from source
            content_type: Content type
            user_id: Filter by user ID

        Returns:
            ContentItem if found, None otherwise
        """
        return self.sqlite_db.get_content_item_by_external_id(
            external_id=external_id,
            content_type=content_type,
            user_id=user_id,
        )

    def get_all_users(self) -> list[UserDict]:
        """Get all users.

        Returns:
            List of user dicts ordered by id.
        """
        with self.sqlite_db.connection() as conn:
            return get_all_users(conn)

    def get_user_preference_config(self, user_id: int) -> UserPreferenceConfig:
        """Load user preference config from DB.

        Returns defaults if no preference config is stored for the user.

        Args:
            user_id: User ID to look up.

        Returns:
            UserPreferenceConfig for the user.
        """
        with self.sqlite_db.connection() as conn:
            user = get_user_by_id(conn, user_id)
            if user is not None:
                settings = user["settings"]
                if settings and "preference_config" in settings:
                    return UserPreferenceConfig.from_dict(settings["preference_config"])
            return UserPreferenceConfig()

    def save_user_preference_config(
        self, user_id: int, preference_config: UserPreferenceConfig
    ) -> None:
        """Save user preference config to DB.

        Merges into the ``users.settings`` JSON blob under the
        ``"preference_config"`` key without clobbering other settings.

        Args:
            user_id: User ID.
            preference_config: Preference config to persist.
        """
        with self.sqlite_db.connection() as conn:
            update_user_settings(
                conn, user_id, {"preference_config": preference_config.to_dict()}
            )

    def get_cached_preference_interpretation(self, cache_key: str) -> str | None:
        """Get a cached preference interpretation.

        Args:
            cache_key: The cache key to look up.

        Returns:
            Cached JSON string or None if not found.
        """
        with self.sqlite_db.connection() as conn:
            return get_cached_preference_interpretation(conn, cache_key)

    def save_cached_preference_interpretation(
        self, cache_key: str, interpretation_json: str
    ) -> None:
        """Save a preference interpretation to the cache.

        Args:
            cache_key: The cache key.
            interpretation_json: JSON string of the interpretation.
        """
        with self.sqlite_db.connection() as conn:
            save_cached_preference_interpretation(conn, cache_key, interpretation_json)

    def clear_cached_preference_interpretations(self) -> int:
        """Clear all cached preference interpretations.

        Returns:
            Number of rows deleted.
        """
        with self.sqlite_db.connection() as conn:
            return clear_cached_preference_interpretations(conn)

    # Enrichment status methods

    def get_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
        include_not_found: bool = False,
    ) -> list[tuple[int, ContentItem]]:
        """Get content items that need enrichment.

        Returns items where no enrichment_status record exists (new items)
        or where needs_enrichment = TRUE.

        Args:
            content_type: Optional filter by content type
            user_id: Filter by user ID
            limit: Maximum number of items to return
            include_not_found: Also include items previously marked as not_found

        Returns:
            List of (db_id, ContentItem) tuples
        """
        return self.sqlite_db.get_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
            limit=limit,
            include_not_found=include_not_found,
        )

    def get_enrichment_status(
        self, content_item_id: int
    ) -> EnrichmentStatusDict | None:
        """Get enrichment status for a content item.

        Args:
            content_item_id: Content item database ID

        Returns:
            Enrichment status dict or None if not found
        """
        with self.sqlite_db.connection() as conn:
            return get_enrichment_status(conn, content_item_id)

    def mark_enrichment_complete(
        self,
        content_item_id: int,
        provider: str,
        quality: str,
    ) -> None:
        """Mark an item as successfully enriched.

        Args:
            content_item_id: Content item database ID
            provider: Name of the provider that enriched the item
            quality: Match quality ("high", "medium", "not_found")
        """
        with self.sqlite_db.connection() as conn:
            mark_enrichment_complete(conn, content_item_id, provider, quality)

    def mark_enrichment_failed(
        self,
        content_item_id: int,
        error: str,
    ) -> None:
        """Mark an item's enrichment as failed.

        Args:
            content_item_id: Content item database ID
            error: Error message describing the failure
        """
        with self.sqlite_db.connection() as conn:
            mark_enrichment_failed(conn, content_item_id, error)

    def mark_item_needs_enrichment(self, content_item_id: int) -> None:
        """Mark an item as needing enrichment.

        Args:
            content_item_id: Content item database ID
        """
        with self.sqlite_db.connection() as conn:
            mark_item_needs_enrichment(conn, content_item_id)

    def reset_enrichment_status(
        self,
        provider: str | None = None,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> int:
        """Reset enrichment status for items to allow re-enrichment.

        Args:
            provider: If specified, only reset items enriched by this provider.
                      If None, reset all items.
            content_type: If specified, only reset items of this content type.
            user_id: If specified, only reset items for this user.

        Returns:
            Number of items reset
        """
        with self.sqlite_db.connection() as conn:
            content_type_str = content_type.value if content_type else None
            return reset_enrichment_status(conn, provider, content_type_str, user_id)

    def get_enrichment_stats(
        self, user_id: int | None = None
    ) -> dict[str, int | dict[str, int]]:
        """Get overall enrichment statistics.

        Args:
            user_id: If specified, only count items for this user.

        Returns:
            Dict with enrichment statistics including:
            - total: Total content items
            - enriched: Successfully enriched items
            - pending: Items pending enrichment
            - not_found: Items where no match was found
            - failed: Items with enrichment errors
            - by_provider: Breakdown by provider
            - by_quality: Breakdown by match quality
        """
        with self.sqlite_db.connection() as conn:
            return get_enrichment_stats(conn, user_id)

    def get_content_item_db_id(
        self,
        external_id: str,
        content_type: ContentType,
        user_id: int | None = None,
    ) -> int | None:
        """Get the database ID of a content item by external ID.

        Args:
            external_id: External ID from source
            content_type: Content type
            user_id: Filter by user ID

        Returns:
            Database ID if found, None otherwise
        """
        return self.sqlite_db.get_content_item_db_id(
            external_id=external_id,
            content_type=content_type,
            user_id=user_id,
        )

    # Core memory methods

    def get_core_memories(
        self,
        user_id: int,
        active_only: bool = True,
        memory_type: str | None = None,
    ) -> list[CoreMemoryDict]:
        """Get core memories for a user.

        Args:
            user_id: User ID
            active_only: If True, only return active memories
            memory_type: Filter by type ("user_stated" or "inferred")

        Returns:
            List of memory dicts
        """
        with self.sqlite_db.connection() as conn:
            return get_core_memories(
                conn, user_id, active_only=active_only, memory_type=memory_type
            )

    def save_core_memory(
        self,
        user_id: int,
        memory_text: str,
        memory_type: str,
        source: str,
        confidence: float = 1.0,
    ) -> int:
        """Save a new core memory.

        Args:
            user_id: User ID
            memory_text: The preference statement
            memory_type: "user_stated" or "inferred"
            source: "conversation", "rating_pattern", or "manual"
            confidence: Confidence score (0.0-1.0)

        Returns:
            New memory ID
        """
        with self.sqlite_db.connection() as conn:
            return save_core_memory(
                conn,
                user_id=user_id,
                memory_text=memory_text,
                memory_type=memory_type,
                source=source,
                confidence=confidence,
            )

    def update_core_memory(
        self,
        memory_id: int,
        memory_text: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        """Update a core memory.

        Args:
            memory_id: Memory ID to update
            memory_text: New memory text (optional)
            is_active: New active status (optional)

        Returns:
            True if updated, False if not found
        """
        with self.sqlite_db.connection() as conn:
            return update_core_memory(
                conn,
                memory_id=memory_id,
                memory_text=memory_text,
                is_active=is_active,
            )

    def delete_core_memory(self, memory_id: int) -> bool:
        """Delete a core memory.

        Args:
            memory_id: Memory ID to delete

        Returns:
            True if deleted, False if not found
        """
        with self.sqlite_db.connection() as conn:
            return delete_core_memory(conn, memory_id)

    # Conversation history methods

    def get_conversation_history(
        self,
        user_id: int,
        limit: int = 50,
    ) -> list[ConversationMessageDict]:
        """Get recent conversation history for a user.

        Args:
            user_id: User ID
            limit: Maximum number of messages to return

        Returns:
            List of message dicts ordered chronologically (oldest first)
        """
        with self.sqlite_db.connection() as conn:
            return get_conversation_history(conn, user_id, limit=limit)

    def save_conversation_message(
        self,
        user_id: int,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> int:
        """Save a conversation message.

        Args:
            user_id: User ID
            role: "user" or "assistant"
            content: Message content
            tool_calls: Optional list of tool calls made

        Returns:
            New message ID
        """
        with self.sqlite_db.connection() as conn:
            return save_conversation_message(
                conn,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
            )

    def clear_conversation_history(self, user_id: int) -> int:
        """Clear conversation history for a user (the "reset" functionality).

        Note: This clears the conversation but preserves core memories.

        Args:
            user_id: User ID

        Returns:
            Number of messages deleted
        """
        with self.sqlite_db.connection() as conn:
            return clear_conversation_history(conn, user_id)

    # Preference profile methods

    def get_preference_profile(self, user_id: int) -> dict | None:
        """Get the preference profile for a user.

        Args:
            user_id: User ID

        Returns:
            Profile dict or None if not found
        """
        with self.sqlite_db.connection() as conn:
            return get_preference_profile(conn, user_id)

    def save_preference_profile(self, user_id: int, profile_json: str) -> int:
        """Save or update a preference profile.

        Args:
            user_id: User ID
            profile_json: JSON string of the profile

        Returns:
            Profile ID
        """
        with self.sqlite_db.connection() as conn:
            return save_preference_profile(conn, user_id, profile_json)
