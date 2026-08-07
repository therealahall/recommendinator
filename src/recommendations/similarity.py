"""Similarity matching using vector embeddings."""

import logging

import numpy as np

from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ContentItem, ContentType
from src.storage.manager import StorageManager, stored_embedding_key

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
        include_ignored: bool = True,
    ) -> list[tuple[ContentItem, float]]:
        """Find items similar to reference items.

        Args:
            reference_items: Items to find similar content for
            content_type: Optional filter by content type
            exclude_ids: Optional list of embedding keys to exclude, as
                ``stored_embedding_key`` derives them — the search returns
                keys, not external ids
            limit: Maximum number of results
            user_id: User ID to scope item lookup (defaults to default user)
            include_ignored: Whether ignored items may be resolved from the
                search hits (default True). Recommendation callers pass False
                so ignored items are never surfaced as similar candidates.

        Returns:
            List of (ContentItem, similarity_score) tuples, sorted by score
        """
        if not reference_items:
            return []

        # Generate embeddings for reference items if needed
        reference_embeddings = []
        for item in reference_items:
            # The key the item's own embedding was written under, which for an
            # item with no external id is its row rather than nothing.
            reference_key = stored_embedding_key(item)
            if (
                reference_key
                and self.storage.vector_db is not None
                and self.storage.vector_db.has_embedding(reference_key)
            ):
                embedding = self.storage.vector_db.get_embedding(reference_key)
                if embedding is not None:
                    reference_embeddings.append(embedding)
            else:
                # Generate embedding
                try:
                    embedding = self.embedding_gen.generate_content_embedding(item)
                    reference_embeddings.append(embedding)
                    # Save embedding for future use
                    if reference_key:
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

        try:
            # search_similar already drops completed items; exclude_ids is
            # filtered here because it is a per-call list, not a status.
            similar_results = self.storage.search_similar(
                query_embedding=query_embedding,
                n_results=limit,
                content_type=content_type,
                exclude_consumed=True,
            )

            if exclude_ids:
                # A set once, not a scan per hit: the engine builds this list
                # from every item the user has consumed.
                excluded = set(exclude_ids)
                similar_results = [
                    result
                    for result in similar_results
                    if result.get("content_id") not in excluded
                ]

            hits = [
                (content_id, result.get("score", 0.0))
                for result in similar_results
                if (content_id := result.get("content_id"))
            ]
            if not hits:
                return []

            # Resolve exactly the keys the search returned, so the lookup does
            # not depend on how large the library is or how it sorts.
            items_by_key = self.storage.get_items_by_embedding_keys(
                [key for key, _ in hits],
                user_id=user_id,
                content_type=content_type,
                include_ignored=include_ignored,
            )

            similar_items = [
                (items_by_key[key], score) for key, score in hits if key in items_by_key
            ]
            if len(similar_items) < len(hits):
                logger.info(
                    "Similarity search: %d of %d hits matched no visible item",
                    len(hits) - len(similar_items),
                    len(hits),
                )

            # Sort by score (descending)
            similar_items.sort(key=lambda entry: entry[1], reverse=True)

            return similar_items
        except Exception as error:
            logger.error("Similarity search failed: %s", error)
            return []
