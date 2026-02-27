"""Recommendation generation using LLM."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.llm.client import OllamaClient
from src.llm.prompts import (
    build_blurb_system_prompt,
    build_recommendation_prompt,
    build_recommendation_system_prompt,
    build_single_blurb_prompt,
)
from src.models.content import ContentItem, ContentType

logger = logging.getLogger(__name__)

_TRADEMARK_RE = re.compile(r"[™®©]")


def _fix_author_attributions(recommendations: list[dict[str, Any]]) -> None:
    """Fix cross-contaminated author names in reasoning text.

    Local LLMs sometimes attribute item X to the author of item Y when
    both appear in the same batch.  When the reasoning for an item
    mentions another batch author but NOT the item's own author,
    substitute the correct name.

    Only applies when exactly one wrong author is found — ambiguous
    cases (multiple wrong authors, or legitimate cross-references where
    both authors are mentioned) are left untouched.

    Modifies *recommendations* in place.
    """
    batch_authors: dict[str, str] = {}  # lowercase -> original case
    for rec in recommendations:
        item = rec.get("item")
        if item and item.author:
            batch_authors[item.author.lower()] = item.author

    for rec in recommendations:
        item = rec.get("item")
        reasoning = rec.get("reasoning", "")
        if not item or not item.author or not reasoning:
            continue

        correct_author = item.author
        reasoning_lower = reasoning.lower()

        if re.search(
            r"\b" + re.escape(correct_author.lower()) + r"\b", reasoning_lower
        ):
            continue

        wrong_authors = [
            original
            for lower, original in batch_authors.items()
            if lower != correct_author.lower()
            and re.search(r"\b" + re.escape(lower) + r"\b", reasoning_lower)
        ]

        if len(wrong_authors) == 1:
            safe_replacement = correct_author.replace("\\", "\\\\")
            rec["reasoning"] = re.sub(
                re.escape(wrong_authors[0]),
                safe_replacement,
                reasoning,
                flags=re.IGNORECASE,
            )
            logger.info(
                "Fixed author attribution in reasoning for %r: %r -> %r",
                item.title,
                wrong_authors[0],
                correct_author,
            )


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

    def generate_single_blurb(
        self,
        content_type: ContentType,
        item: ContentItem,
        consumed_items: list[ContentItem],
        references: list[ContentItem] | None = None,
        model: str | None = None,
    ) -> str:
        """Generate a blurb for a single recommendation item.

        One LLM call, returns raw prose.  No parsing or title-matching
        needed because the response describes exactly one pre-identified
        item.

        Args:
            content_type: Type of content being recommended
            item: The single item to write a blurb for
            consumed_items: User's consumed items for taste reference
            references: Genre-relevant reference items for this pick
            model: Optional model override

        Returns:
            Blurb text (stripped)
        """
        system_prompt = build_blurb_system_prompt(content_type)
        user_prompt = build_single_blurb_prompt(
            content_type=content_type,
            item=item,
            consumed_items=consumed_items,
            references=references,
        )
        response = self.client.generate_text(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=0.7,
        )
        return response.strip()

    def generate_blurbs_per_item(
        self,
        content_type: ContentType,
        items_with_refs: list[tuple[ContentItem, list[ContentItem]]],
        consumed_items: list[ContentItem],
        model: str | None = None,
    ) -> dict[str, str]:
        """Generate blurbs for multiple items, one LLM call per item.

        Uses ``ThreadPoolExecutor`` to run calls concurrently (I/O-bound
        Ollama HTTP calls).  Returns a mapping of ``item.id`` to blurb
        text with consumed-title highlighting applied.

        Args:
            content_type: Type of content being recommended
            items_with_refs: List of ``(item, references)`` pairs
            consumed_items: User's consumed items for taste reference
            model: Optional model override

        Returns:
            Dict mapping item ID to highlighted blurb text
        """
        if not items_with_refs:
            return {}

        def _generate_one(
            item: ContentItem, refs: list[ContentItem]
        ) -> tuple[str, str]:
            """Generate a blurb for one item (runs in thread)."""
            blurb = self.generate_single_blurb(
                content_type=content_type,
                item=item,
                consumed_items=consumed_items,
                references=refs or None,
                model=model,
            )
            return (item.id or "", blurb)

        results: dict[str, str] = {}

        # Single item — skip threading overhead
        if len(items_with_refs) == 1:
            item, refs = items_with_refs[0]
            try:
                item_id, blurb = _generate_one(item, refs)
                results[item_id] = blurb
            except Exception as error:
                logger.warning("Blurb generation failed for %r: %s", item.title, error)
        else:
            with ThreadPoolExecutor(
                max_workers=min(len(items_with_refs), 4)
            ) as executor:
                future_to_item = {
                    executor.submit(_generate_one, item, refs): item
                    for item, refs in items_with_refs
                }
                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    try:
                        item_id, blurb = future.result()
                        results[item_id] = blurb
                    except Exception as error:
                        logger.warning(
                            "Blurb generation failed for %r: %s",
                            item.title,
                            error,
                        )

        return results

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
                    author_text = remainder[3:].strip()
                    # Split author from inline reasoning at first separator
                    sep_match = re.search(r"\s+[-—–]\s+|:\s+", author_text)
                    if sep_match:
                        author = author_text[: sep_match.start()].strip()
                        inline_reasoning = author_text[sep_match.end() :].strip()
                    else:
                        author = author_text
                elif remainder:
                    # Strip leading separators (-, —, –, :) from inline reasoning
                    inline_reasoning = re.sub(r"^[-—–:]\s*", "", remainder).strip()
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

            # Fallback: when the LLM invents an author (e.g. a director for
            # movies) that doesn't match any item, retry title-only matching.
            if not matching_item and author is not None:
                for item in unconsumed_items:
                    item_title_lower = _TRADEMARK_RE.sub("", item.title.lower())
                    if (
                        title_lower in item_title_lower
                        or item_title_lower in title_lower
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

        _fix_author_attributions(recommendations)

        matched_count = sum(1 for r in recommendations if r["item"] is not None)
        reasoning_count = sum(1 for r in recommendations if r["reasoning"])
        logger.info(
            "Parse results: %d items parsed, %d matched, %d with reasoning",
            len(recommendations),
            matched_count,
            reasoning_count,
        )

        return recommendations
