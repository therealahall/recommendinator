"""Unified storage manager for SQLite and ChromaDB."""

from pathlib import Path
from typing import Any, List, Optional, Dict

from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.storage.sqlite_db import SQLiteDB
from src.storage.vector_db import VectorDB


class StorageManager:
    """Unified storage manager for both SQLite and ChromaDB."""

    def __init__(
        self,
        sqlite_path: Path,
        vector_db_path: Path,
        vector_collection_name: str = "content_embeddings",
    ) -> None:
        """Initialize storage manager.

        Args:
            sqlite_path: Path to SQLite database file
            vector_db_path: Path to ChromaDB database directory
            vector_collection_name: Name of ChromaDB collection
        """
        self.sqlite_db = SQLiteDB(sqlite_path)
        self.vector_db = VectorDB(vector_db_path, vector_collection_name)

    def save_content_item(
        self, item: ContentItem, embedding: Optional[List[float]] = None
    ) -> int:
        """Save a content item to both SQLite and optionally ChromaDB.

        Args:
            item: ContentItem to save
            embedding: Optional embedding vector to store

        Returns:
            Database ID of the saved item
        """
        # Save to SQLite
        db_id = self.sqlite_db.save_content_item(item)

        # Save embedding if provided
        if embedding:
            # Use external_id if available, otherwise use db_id as string
            content_id = item.id if item.id else f"db_{db_id}"

            # Handle enum-to-string conversion (Pydantic use_enum_values converts to string)
            def get_enum_value(val: Any) -> str:
                """Get string value from enum or string."""
                return val.value if hasattr(val, "value") else str(val)

            metadata = {
                "content_type": get_enum_value(item.content_type),
                "title": item.title,
                "author": item.author or "",
                "status": get_enum_value(item.status),
            }
            self.vector_db.add_embedding(content_id, embedding, metadata)

        return db_id

    def get_content_item(self, db_id: int) -> Optional[ContentItem]:
        """Get a content item by database ID.

        Args:
            db_id: Database ID

        Returns:
            ContentItem if found, None otherwise
        """
        return self.sqlite_db.get_content_item(db_id)

    def get_content_items(
        self,
        content_type: Optional[ContentType] = None,
        status: Optional[ConsumptionStatus] = None,
        min_rating: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ContentItem]:
        """Get content items with optional filters.

        Args:
            content_type: Filter by content type
            status: Filter by consumption status
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of ContentItem objects
        """
        return self.sqlite_db.get_content_items(
            content_type=content_type,
            status=status,
            min_rating=min_rating,
            limit=limit,
        )

    def get_unconsumed_items(
        self, content_type: Optional[ContentType] = None, limit: Optional[int] = None
    ) -> List[ContentItem]:
        """Get unconsumed items (status = UNREAD).

        Args:
            content_type: Filter by content type
            limit: Maximum number of results

        Returns:
            List of unconsumed ContentItem objects
        """
        return self.sqlite_db.get_unconsumed_items(
            content_type=content_type, limit=limit
        )

    def get_completed_items(
        self,
        content_type: Optional[ContentType] = None,
        min_rating: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ContentItem]:
        """Get completed items with optional minimum rating.

        Args:
            content_type: Filter by content type
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results

        Returns:
            List of completed ContentItem objects
        """
        return self.sqlite_db.get_completed_items(
            content_type=content_type, min_rating=min_rating, limit=limit
        )

    def search_similar(
        self,
        query_embedding: List[float],
        n_results: int = 10,
        content_type: Optional[ContentType] = None,
        exclude_consumed: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search for similar content using vector similarity.

        Args:
            query_embedding: Query embedding vector
            n_results: Number of results to return
            content_type: Optional filter by content type
            exclude_consumed: If True, exclude consumed items

        Returns:
            List of similar content items with scores and metadata
        """
        # Get consumed item IDs to exclude
        exclude_ids: Optional[List[str]] = None
        if exclude_consumed:
            consumed = self.get_completed_items(content_type=content_type)
            exclude_ids = [item.id for item in consumed if item.id]

        # Search vector database
        results = self.vector_db.search_similar(
            query_embedding=query_embedding,
            n_results=n_results,
            content_type=content_type.value if content_type else None,
            exclude_ids=exclude_ids,
        )

        # Enrich results with full content item data
        enriched_results = []
        for result in results:
            content_id = result["content_id"]
            # Try to find the content item
            # If content_id is an external_id, we need to search for it
            # For now, return the vector DB result with metadata
            enriched_results.append(result)

        return enriched_results

    def delete_content_item(self, db_id: int) -> bool:
        """Delete a content item from both databases.

        Args:
            db_id: Database ID

        Returns:
            True if item was deleted, False if not found
        """
        # Get item first to find its external_id
        item = self.sqlite_db.get_content_item(db_id)
        if not item:
            return False

        # Delete from SQLite
        deleted = self.sqlite_db.delete_content_item(db_id)

        # Delete embedding if it exists
        if deleted and item.id:
            self.vector_db.delete_embedding(item.id)

        return deleted

    def count_items(
        self,
        content_type: Optional[ContentType] = None,
        status: Optional[ConsumptionStatus] = None,
    ) -> int:
        """Count content items with optional filters.

        Args:
            content_type: Filter by content type
            status: Filter by consumption status

        Returns:
            Number of matching items
        """
        return self.sqlite_db.count_items(content_type=content_type, status=status)

    def add_embedding(
        self,
        content_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add or update an embedding for a content item.

        Args:
            content_id: Unique identifier for the content item
            embedding: Vector embedding
            metadata: Optional metadata dictionary
        """
        self.vector_db.add_embedding(content_id, embedding, metadata)

    def get_embedding(self, content_id: str) -> Optional[List[float]]:
        """Get embedding for a content item.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            Embedding vector if found, None otherwise
        """
        return self.vector_db.get_embedding(content_id)
