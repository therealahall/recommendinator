"""Unified storage manager for SQLite and optionally ChromaDB."""

from pathlib import Path
from typing import Any

from src.ingestion.conflict import ConflictStrategy, resolve_conflict
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.storage.schema import get_user_by_id, update_user_settings
from src.storage.sqlite_db import SQLiteDB


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
        self.vector_db = None
        self.ai_enabled = ai_enabled
        self.conflict_strategy = conflict_strategy
        self.source_priority = source_priority or []

        # Only initialize vector DB if AI is enabled and path provided
        if ai_enabled and vector_db_path:
            from src.storage.vector_db import VectorDB

            self.vector_db = VectorDB(vector_db_path, vector_collection_name)

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
                user_id=user_id or item.user_id,
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
        if embedding and self.vector_db:
            # Use external_id if available, otherwise use db_id as string
            content_id = item.id if item.id else f"db_{db_id}"

            # Handle enum-to-string conversion
            def get_enum_value(val: Any) -> str:
                """Get string value from enum or string."""
                return val.value if hasattr(val, "value") else str(val)

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

    def get_content_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get content items with optional filters.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            status: Filter by consumption status
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of ContentItem objects
        """
        return self.sqlite_db.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=status,
            min_rating=min_rating,
            limit=limit,
        )

    def get_unconsumed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get unconsumed items (status = UNREAD).

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
        """Get completed items with optional minimum rating.

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

        # Handle enum-to-string conversion
        def get_enum_value(val: Any) -> str:
            """Get string value from enum or string."""
            return val.value if hasattr(val, "value") else str(val)

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

    def get_user_preference_config(self, user_id: int) -> UserPreferenceConfig:
        """Load user preference config from DB.

        Returns defaults if no preference config is stored for the user.

        Args:
            user_id: User ID to look up.

        Returns:
            UserPreferenceConfig for the user.
        """
        conn = self.sqlite_db._get_connection()
        try:
            user = get_user_by_id(conn, user_id)
            if (
                user
                and user.get("settings")
                and "preference_config" in user["settings"]
            ):
                return UserPreferenceConfig.from_dict(
                    user["settings"]["preference_config"]
                )
            return UserPreferenceConfig()
        finally:
            conn.close()

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
        conn = self.sqlite_db._get_connection()
        try:
            update_user_settings(
                conn, user_id, {"preference_config": preference_config.to_dict()}
            )
        finally:
            conn.close()
