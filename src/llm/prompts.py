"""Prompt templates for LLM interactions."""

from src.models.content import ContentItem, ContentType


def build_recommendation_prompt(
    content_type: ContentType,
    consumed_items: list[ContentItem],
    unconsumed_items: list[ContentItem],
    count: int = 5,
) -> str:
    """Build a prompt for generating recommendations.

    Args:
        content_type: Type of content to recommend
        consumed_items: List of consumed items with ratings/reviews
        unconsumed_items: List of unconsumed items to choose from
        count: Number of recommendations to generate

    Returns:
        Formatted prompt string
    """
    # Handle both enum and string (Pydantic use_enum_values converts to string)
    content_type_str = (
        content_type.value if hasattr(content_type, "value") else str(content_type)
    )
    content_type_name = content_type_str.replace("_", " ").title()

    # Build context from consumed items
    high_rated = [item for item in consumed_items if item.rating and item.rating >= 4]
    context_items = high_rated[:10]  # Limit context size

    context_text = ""
    if context_items:
        context_text = "Based on your consumption history:\n\n"
        for item in context_items:
            rating_text = f"Rating: {item.rating}/5" if item.rating else "Unrated"
            review_text = f"\nReview: {item.review}" if item.review else ""
            author_text = f" by {item.author}" if item.author else ""
            context_text += (
                f"- {item.title}{author_text} ({rating_text}){review_text}\n"
            )

    # Build list of unconsumed items
    items_text = ""
    for i, item in enumerate(unconsumed_items[:50], 1):  # Limit to 50 for context
        author_text = f" by {item.author}" if item.author else ""
        items_text += f"{i}. {item.title}{author_text}\n"

    prompt = f"""You are a personal recommendation assistant. Your task is to recommend {count} {content_type_name.lower()}s that the user would enjoy based on their preferences.

{context_text}

Here are {content_type_name}s the user has NOT yet consumed:

{items_text}

Please recommend exactly {count} {content_type_name.lower()}s from the list above that best match the user's preferences based on their high-rated items. For each recommendation, provide:
1. The title (and author if applicable)
2. A brief explanation of why you think they would enjoy it

Format your response as a numbered list, one recommendation per item."""

    return prompt


def build_recommendation_system_prompt(content_type: ContentType) -> str:
    """Build system prompt for recommendations.

    Args:
        content_type: Type of content being recommended

    Returns:
        System prompt string
    """
    # Handle both enum and string (Pydantic use_enum_values converts to string)
    content_type_str = (
        content_type.value if hasattr(content_type, "value") else str(content_type)
    )
    content_type_name = content_type_str.replace("_", " ").title()

    return f"""You are an expert recommendation assistant specializing in {content_type_name.lower()}s.
Your goal is to understand user preferences from their consumption history and provide personalized recommendations.

Key principles:
- Focus on items the user has rated highly (4-5 stars)
- Consider themes, genres, authors, and styles from their favorites
- Provide clear, concise reasoning for each recommendation
- Only recommend items from the provided list
- Be specific about what makes each recommendation a good match"""


def build_content_description(item: ContentItem) -> str:
    """Build a text description of a content item for embedding.

    Args:
        item: ContentItem to describe

    Returns:
        Description string
    """
    parts = [item.title]

    if item.author:
        parts.append(f"by {item.author}")

    if item.review:
        parts.append(f"Review: {item.review}")

    if item.metadata:
        # Add relevant metadata
        if "genre" in item.metadata:
            parts.append(f"Genre: {item.metadata['genre']}")
        # Handle both enum and string (Pydantic use_enum_values converts to string)
        content_type_str = (
            item.content_type.value
            if hasattr(item.content_type, "value")
            else str(item.content_type)
        )
        if "pages" in item.metadata and content_type_str == "book":
            parts.append(f"Pages: {item.metadata['pages']}")

    return " | ".join(parts)
