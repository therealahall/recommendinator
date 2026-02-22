"""Similarity matching using vector embeddings."""

import logging

import numpy as np

from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ContentItem, ContentType
from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class SimilarityMatcher:
    """Match content using vector similarity."""

    def __init__(
        self, storage_manager: StorageManager, embedding_generator: EmbeddingGenerator
    ) -> None:
        """Initialize similarity matcher.

        Args:
            storage_manager: Storage manager for accessing embeddings
            embedding_generator: Generator for creating embeddings
        """
        self.storage = storage_manager
        self.embedding_gen = embedding_generator

    def find_similar(
        self,
        reference_items: list[ContentItem],
        content_type: ContentType | None = None,
        exclude_ids: list[str] | None = None,
        limit: int = 20,
        user_id: int | None = None,
    ) -> list[tuple[ContentItem, float]]:
        """Find items similar to reference items.

        Args:
            reference_items: Items to find similar content for
            content_type: Optional filter by content type
            exclude_ids: Optional list of IDs to exclude
            limit: Maximum number of results
            user_id: User ID to scope item lookup (defaults to default user)

        Returns:
            List of (ContentItem, similarity_score) tuples, sorted by score
        """
        if not reference_items:
            return []

        # Generate embeddings for reference items if needed
        reference_embeddings = []
        for item in reference_items:
            # Check if embedding exists
            content_id = item.id if item.id else None
            if (
                content_id
                and self.storage.vector_db is not None
                and self.storage.vector_db.has_embedding(content_id)
            ):
                embedding = self.storage.vector_db.get_embedding(content_id)
                if embedding is not None:
                    reference_embeddings.append(embedding)
            else:
                # Generate embedding
                try:
                    embedding = self.embedding_gen.generate_content_embedding(item)
                    reference_embeddings.append(embedding)
                    # Save embedding for future use
                    if content_id:
                        self.storage.save_content_item(item, embedding=embedding)
                except Exception as error:
                    logger.warning(
                        "Failed to generate embedding for %s: %s", item.title, error
                    )
                    continue

        if not reference_embeddings:
            logger.warning("No reference embeddings available for similarity search")
            return []

        # Use average of reference embeddings as query
        embeddings_array = np.array(reference_embeddings)
        query_embedding = np.mean(embeddings_array, axis=0).tolist()

        # Search for similar items
        try:
            # StorageManager.search_similar uses exclude_consumed (bool) and n_results (int)
            # We need to handle exclude_ids by checking if items should be excluded
            # For now, we'll use exclude_consumed=True to exclude completed items
            # and filter out exclude_ids manually if needed
            similar_results = self.storage.search_similar(
                query_embedding=query_embedding,
                n_results=limit,
                content_type=content_type,
                exclude_consumed=True,  # Exclude completed items by default
            )

            # Filter out explicitly excluded IDs if provided
            if exclude_ids:
                similar_results = [
                    result
                    for result in similar_results
                    if result.get("content_id") not in exclude_ids
                ]

            # Convert dictionary results to (ContentItem, float) tuples
            # The results contain content_id, score, and metadata
            # We need to look up the actual ContentItem objects
            similar_items: list[tuple[ContentItem, float]] = []

            if not similar_results:
                return []

            # Build a lookup dictionary for efficient item retrieval
            # Get all items of this content type and index by external_id
            all_items = self.storage.get_content_items(
                user_id=user_id, content_type=content_type
            )
            items_by_id = {item.id: item for item in all_items if item.id}

            for result in similar_results:
                content_id = result.get("content_id")
                score = result.get("score", 0.0)

                if not content_id:
                    continue

                # Look up the ContentItem by external_id
                matching_item = items_by_id.get(content_id)
                if matching_item:
                    similar_items.append((matching_item, score))

            # Sort by score (descending)
            similar_items.sort(key=lambda entry: entry[1], reverse=True)

            return similar_items
        except Exception as error:
            logger.error("Similarity search failed: %s", error)
            return []
