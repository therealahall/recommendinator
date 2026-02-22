"""Embedding generation and management."""

import logging

from src.llm.client import OllamaClient
from src.llm.prompts import build_content_description
from src.models.content import ContentItem

logger = logging.getLogger(__name__)


class EmbeddingGenerator:
    """Generate and manage embeddings for content items."""

    def __init__(self, ollama_client: OllamaClient) -> None:
        """Initialize embedding generator.

        Args:
            ollama_client: Ollama client instance
        """
        self.client = ollama_client

    def generate_content_embedding(self, item: ContentItem) -> list[float]:
        """Generate embedding for a content item.

        Args:
            item: ContentItem to generate embedding for

        Returns:
            Embedding vector

        Raises:
            RuntimeError: If embedding generation fails
        """
        description = build_content_description(item)
        return self.client.generate_embedding(description)

    def generate_review_embedding(self, review_text: str) -> list[float]:
        """Generate embedding for a review text.

        Args:
            review_text: Review text to embed

        Returns:
            Embedding vector

        Raises:
            RuntimeError: If embedding generation fails
        """
        if not review_text or not review_text.strip():
            raise ValueError("Review text cannot be empty")

        return self.client.generate_embedding(review_text.strip())

    def generate_embeddings_batch(
        self, items: list[ContentItem], batch_size: int = 10
    ) -> list[list[float]]:
        """Generate embeddings for multiple content items in batches.

        Args:
            items: List of ContentItems to generate embeddings for
            batch_size: Number of items to process per batch

        Returns:
            List of embedding vectors

        Raises:
            RuntimeError: If embedding generation fails
        """
        embeddings = []

        for index in range(0, len(items), batch_size):
            batch = items[index : index + batch_size]
            logger.info("Generating embeddings for batch %d", index // batch_size + 1)

            for item in batch:
                try:
                    embedding = self.generate_content_embedding(item)
                    embeddings.append(embedding)
                except Exception as error:
                    logger.error(
                        "Failed to generate embedding for %s: %s", item.title, error
                    )
                    # Append empty list or re-raise based on requirements
                    raise

        return embeddings
