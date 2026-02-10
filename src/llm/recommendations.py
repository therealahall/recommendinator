"""Recommendation generation using LLM."""

import logging
import re
from typing import Any

from src.llm.client import OllamaClient
from src.llm.prompts import (
    build_recommendation_prompt,
    build_recommendation_system_prompt,
)
from src.models.content import ContentItem, ContentType

logger = logging.getLogger(__name__)


class RecommendationGenerator:
    """Generate recommendations using LLM."""

    def __init__(self, ollama_client: OllamaClient) -> None:
        """Initialize recommendation generator.

        Args:
            ollama_client: Ollama client instance
        """
        self.client = ollama_client

    def generate_recommendations(
        self,
        content_type: ContentType,
        consumed_items: list[ContentItem],
        unconsumed_items: list[ContentItem],
        count: int = 5,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate recommendations using LLM.

        Args:
            content_type: Type of content to recommend
            consumed_items: List of consumed items with ratings/reviews
            unconsumed_items: List of unconsumed items to choose from
            count: Number of recommendations to generate
            model: Optional model override

        Returns:
            List of recommendation dictionaries with title, reasoning, etc.

        Raises:
            RuntimeError: If recommendation generation fails
        """
        if not unconsumed_items:
            logger.warning("No unconsumed items available for recommendations")
            return []

        if len(unconsumed_items) < count:
            count = len(unconsumed_items)
            logger.info(f"Reducing recommendation count to {count} (available items)")

        # Build prompts
        system_prompt = build_recommendation_system_prompt(content_type)
        user_prompt = build_recommendation_prompt(
            content_type, consumed_items, unconsumed_items, count
        )

        try:
            # Generate recommendations
            response = self.client.generate_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=model,
                temperature=0.7,
            )

            # Parse response
            recommendations = self._parse_recommendations(
                response, unconsumed_items, count
            )

            return recommendations[:count]  # Ensure we don't exceed count

        except Exception as e:
            logger.error(f"Failed to generate recommendations: {e}")
            raise RuntimeError(f"Recommendation generation failed: {e}") from e

    def _parse_recommendations(
        self,
        response: str,
        unconsumed_items: list[ContentItem],
        count: int = 5,
    ) -> list[dict[str, Any]]:
        """Parse LLM response into structured recommendations.

        Args:
            response: LLM response text
            unconsumed_items: List of available items to match against
            count: Maximum number of fallback recommendations

        Returns:
            List of recommendation dictionaries
        """
        recommendations = []

        # Try to extract numbered list items
        # Pattern: "1. Title by Author\n   Explanation..."
        # Split by numbered list pattern
        pattern = r"(\d+)\.\s+"
        parts = re.split(pattern, response)

        # Process each recommendation (skip first empty part)
        for i in range(1, len(parts), 2):
            if i + 1 >= len(parts):
                break

            match_content = parts[i + 1].strip()
            if not match_content:
                continue

            lines = [line.strip() for line in match_content.split("\n") if line.strip()]
            if not lines:
                continue

            title_line = lines[0]

            # Extract title and author if present
            title = title_line
            author = None
            if " by " in title_line:
                parts_split = title_line.split(" by ", 1)
                title = parts_split[0].strip()
                author = parts_split[1].strip()

            # Extract reasoning (remaining lines)
            reasoning = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

            # Try to find matching item
            matching_item = None
            for item in unconsumed_items:
                if item.title.lower() == title.lower():
                    if author is None or (
                        item.author and item.author.lower() == author.lower()
                    ):
                        matching_item = item
                        break

            recommendations.append(
                {
                    "title": title,
                    "author": author,
                    "reasoning": reasoning,
                    "item": matching_item,
                }
            )

        # If parsing failed, try simpler extraction
        if not recommendations:
            # Fallback: extract titles from response
            for item in unconsumed_items:
                if item.title.lower() in response.lower():
                    recommendations.append(
                        {
                            "title": item.title,
                            "author": item.author,
                            "reasoning": "Recommended based on your preferences",
                            "item": item,
                        }
                    )
                    if len(recommendations) >= count:
                        break

        return recommendations
