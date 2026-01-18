"""Similarity matching using vector embeddings."""

import logging
from typing import List, Tuple, Optional

from src.models.content import ContentItem, ContentType
from src.storage.manager import StorageManager
from src.llm.embeddings import EmbeddingGenerator

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
        reference_items: List[ContentItem],
        content_type: Optional[ContentType] = None,
        exclude_ids: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Tuple[ContentItem, float]]:
        """Find items similar to reference items.

        Args:
            reference_items: Items to find similar content for
            content_type: Optional filter by content type
            exclude_ids: Optional list of IDs to exclude
            limit: Maximum number of results

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
            if content_id and self.storage.vector_db.has_embedding(content_id):
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
                        self.storage.save_content_item(item, embedding)
                except Exception as e:
                    logger.warning(
                        f"Failed to generate embedding for {item.title}: {e}"
                    )
                    continue

        if not reference_embeddings:
            logger.warning("No reference embeddings available for similarity search")
            return []

        # Use average of reference embeddings as query
        import numpy as np

        embeddings_array = np.array(reference_embeddings)
        query_embedding = np.mean(embeddings_array, axis=0).tolist()

        # Search for similar items
        try:
            similar_items = self.storage.search_similar(
                query_embedding=query_embedding,
                content_type=content_type,
                exclude_ids=exclude_ids,
                limit=limit,
            )
            return similar_items
        except Exception as e:
            logger.error(f"Similarity search failed: {e}")
            return []

    def find_similar_to_item(
        self,
        item: ContentItem,
        content_type: Optional[ContentType] = None,
        exclude_ids: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Tuple[ContentItem, float]]:
        """Find items similar to a single item.

        Args:
            item: Item to find similar content for
            content_type: Optional filter by content type
            exclude_ids: Optional list of IDs to exclude
            limit: Maximum number of results

        Returns:
            List of (ContentItem, similarity_score) tuples, sorted by score
        """
        return self.find_similar([item], content_type, exclude_ids, limit)
