"""Embedding generation and management."""

from src.llm.client import OllamaClient
from src.llm.prompts import build_content_description
from src.models.content import ContentItem


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
