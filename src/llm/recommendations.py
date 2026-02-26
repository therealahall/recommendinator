"""Recommendation generation using LLM."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.llm.client import OllamaClient
from src.llm.prompts import (
    build_blurb_prompt,
    build_blurb_system_prompt,
    build_recommendation_prompt,
    build_recommendation_system_prompt,
)
from src.models.content import ContentItem, ContentType

logger = logging.getLogger(__name__)

_TRADEMARK_RE = re.compile(r"[™®©]")

# Maximum items per LLM call when generating blurbs.  Local models struggle
# with long prompts and responses, so we batch into smaller groups.
BLURB_BATCH_SIZE = 5


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
            logger.info("Reducing recommendation count to %d (available items)", count)

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

        except Exception as error:
            logger.error("Failed to generate recommendations: %s", error)
            raise RuntimeError(f"Recommendation generation failed: {error}") from error

    def generate_blurbs(
        self,
        content_type: ContentType,
        selected_items: list[ContentItem],
        consumed_items: list[ContentItem],
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate blurbs for pre-selected recommendation items.

        Unlike ``generate_recommendations`` which asks the LLM to pick from
        a large candidate list, this method accepts pre-selected items and
        asks only for enthusiastic blurbs. Uses a slimmer prompt that saves
        ~1,000-2,000 tokens.

        When more than :data:`BLURB_BATCH_SIZE` items are provided, the
        items are split into batches and a separate LLM call is made for
        each batch.  This prevents local models from failing on long
        prompts/responses while still supporting up to the configured
        ``max_count`` (default 20).

        Args:
            content_type: Type of content being recommended
            selected_items: Pre-selected items to write blurbs for
            consumed_items: User's consumed items for taste reference
            model: Optional model override

        Returns:
            List of recommendation dicts with title, reasoning, and item

        Raises:
            RuntimeError: If blurb generation fails for every batch
        """
        if not selected_items:
            return []

        # Split into batches to keep each LLM call manageable
        batches: list[list[ContentItem]] = [
            selected_items[i : i + BLURB_BATCH_SIZE]
            for i in range(0, len(selected_items), BLURB_BATCH_SIZE)
        ]

        system_prompt = build_blurb_system_prompt(content_type)

        def _generate_batch(batch: list[ContentItem]) -> list[dict[str, Any]]:
            """Generate blurbs for a single batch (runs in thread)."""
            user_prompt = build_blurb_prompt(
                content_type=content_type,
                selected_items=batch,
                consumed_items=consumed_items,
            )
            response = self.client.generate_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=model,
                temperature=0.7,
            )
            return self._parse_recommendations(response, batch, count=len(batch))

        # Single batch — skip threading overhead
        if len(batches) == 1:
            try:
                return _generate_batch(batches[0])
            except Exception as error:
                logger.error("Blurb generation failed: %s", error)
                raise RuntimeError("Blurb generation failed") from error

        # Multiple batches — run concurrently (I/O-bound Ollama HTTP calls)
        all_results: list[dict[str, Any]] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=len(batches)) as executor:
            future_to_index = {
                executor.submit(_generate_batch, batch): batch_index
                for batch_index, batch in enumerate(batches)
            }

            for future in as_completed(future_to_index):
                batch_index = future_to_index[future]
                try:
                    parsed = future.result()
                    all_results.extend(parsed)
                except Exception as error:
                    logger.warning(
                        "Blurb generation failed for batch %d/%d: %s",
                        batch_index + 1,
                        len(batches),
                        error,
                    )
                    errors.append(str(error))

        # Raise only when no results were produced AND at least one batch errored.
        # If all batches parsed successfully but returned no matches, return [].
        if not all_results and errors:
            logger.error(
                "Blurb generation failed for all %d batch(es). Errors: %s",
                len(batches),
                "; ".join(errors),
            )
            raise RuntimeError(
                f"Blurb generation failed for all {len(batches)} batch(es)"
            )

        return all_results

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

        logger.info("Raw LLM response received: %d chars", len(response))
        logger.debug("Raw LLM response: %.500s", response)

        # Try to extract numbered list items
        # Pattern: "1. Title by Author\n   Explanation..."
        # Split by numbered list pattern — anchored to line starts AND
        # restricted to 1–2 digit numbers so that years (1984, 2019) or
        # other large numbers at the start of reasoning lines don't cause
        # spurious splits that eat neighboring recommendations.
        pattern = r"^(\d{1,2})\.\s+"
        parts = re.split(pattern, response, flags=re.MULTILINE)

        # Process each recommendation (skip first empty part)
        for index in range(1, len(parts), 2):
            if index + 1 >= len(parts):
                break

            match_content = parts[index + 1].strip()
            if not match_content:
                continue

            lines = [line.strip() for line in match_content.split("\n") if line.strip()]
            if not lines:
                continue

            title_line = lines[0]

            # Extract title, author, and any inline reasoning.
            title = title_line
            author = None
            inline_reasoning = ""

            # Bold-marker pattern: **Title** [by Author | separator reasoning]
            bold_match = re.match(r"\*\*(.+?)\*\*(.*)", title_line)
            if bold_match:
                title = bold_match.group(1).strip()
                remainder = bold_match.group(2).strip()
                if remainder.lower().startswith("by "):
                    author = remainder[3:].strip()
                elif remainder:
                    # Strip leading separators (-, —, :) from inline reasoning
                    inline_reasoning = re.sub(r"^[-—:]\s*", "", remainder).strip()
            else:
                # No bold markers — existing fallback behavior
                if " by " in title_line:
                    parts_split = title_line.split(" by ", 1)
                    title = parts_split[0].strip()
                    author = parts_split[1].strip()
                title = title.strip("*")
                if author:
                    author = author.strip("*")

            # Combine inline reasoning with any remaining lines
            remaining = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
            if inline_reasoning and remaining:
                reasoning = f"{inline_reasoning}\n{remaining}"
            elif inline_reasoning:
                reasoning = inline_reasoning
            else:
                reasoning = remaining

            # Try to find matching item.  Database titles often include a
            # series suffix like "(The Kingkiller Chronicle, #1)" that the
            # LLM omits, so use substring containment rather than exact
            # equality.
            title_lower = _TRADEMARK_RE.sub("", title.lower())
            matching_item = None
            for item in unconsumed_items:
                item_title_lower = _TRADEMARK_RE.sub("", item.title.lower())
                if title_lower in item_title_lower or item_title_lower in title_lower:
                    if author is None or (
                        item.author and item.author.lower() == author.lower()
                    ):
                        matching_item = item
                        break

            if not matching_item:
                logger.debug(
                    "Blurb parsed title %r (author=%r) matched no item in batch of %d",
                    title,
                    author,
                    len(unconsumed_items),
                )

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
            response_lower = _TRADEMARK_RE.sub("", response.lower())
            for item in unconsumed_items:
                item_title_lower = _TRADEMARK_RE.sub("", item.title.lower())
                if item_title_lower in response_lower:
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

        matched_count = sum(1 for r in recommendations if r["item"] is not None)
        reasoning_count = sum(1 for r in recommendations if r["reasoning"])
        logger.info(
            "Parse results: %d items parsed, %d matched, %d with reasoning",
            len(recommendations),
            matched_count,
            reasoning_count,
        )

        return recommendations
