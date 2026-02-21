"""Prompt templates for LLM interactions."""

from src.llm.tone import ADVISOR_IDENTITY, PERSONALITY_TRAITS, STYLE_RULES
from src.models.content import ContentItem, ContentType, get_enum_value


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
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()

    # Build context from consumed items — include ratings and reviews
    high_rated = [item for item in consumed_items if item.rating and item.rating >= 4]
    context_items = high_rated[:10]  # Limit context size

    context_text = ""
    if context_items:
        context_text = "Here's what they love:\n\n"
        for item in context_items:
            rating_text = f"{item.rating}/5" if item.rating else "Unrated"
            review_text = f' — Review: "{item.review}"' if item.review else ""
            author_text = f" by {item.author}" if item.author else ""
            type_label = get_enum_value(item.content_type).replace("_", " ")
            context_text += (
                f"- [{type_label}] **{item.title}**{author_text}"
                f" ({rating_text}){review_text}\n"
            )

    # Build list of unconsumed items
    items_text = ""
    for item_index, item in enumerate(unconsumed_items[:50], 1):
        author_text = f" by {item.author}" if item.author else ""
        items_text += f"{item_index}. {item.title}{author_text}\n"

    prompt = f"""Pick the {count} best {content_type_name.lower()}s for this person from the candidates below.

{context_text}

Candidates (NOT yet consumed):

{items_text}

For each pick, write 2-3 sentences explaining WHY it's a great fit. Connect it to specific items they rated highly — mention titles, ratings, and what they loved. Be enthusiastic and specific, not generic.

IMPORTANT formatting rules:
- Use a numbered list: "1. **Title** by Author" on the first line, reasoning on the next lines
- Do NOT start the reasoning with "Reasoning:" or any label — just dive straight into WHY
- Use **bold** for emphasis on key connections
- Address them as "you" — never say "the user"
- Only pick from the candidates list above"""

    return prompt


def build_recommendation_system_prompt(content_type: ContentType) -> str:
    """Build system prompt for recommendations.

    Args:
        content_type: Type of content being recommended

    Returns:
        System prompt string
    """
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()
    identity = ADVISOR_IDENTITY.format(domain=content_type_name.lower())

    return f"""You are {identity}.

## Your Personality
{PERSONALITY_TRAITS}

## Style
{STYLE_RULES}
- Keep it concise — 2-3 punchy sentences per recommendation, not essays
- Only recommend items from the provided candidate list"""


def build_blurb_system_prompt(content_type: ContentType) -> str:
    """Build a slim system prompt for writing blurbs about pre-selected items.

    Uses only the advisor identity — no personality traits or style rules —
    since the blurb prompt itself provides the formatting instructions.
    This saves ~500 tokens compared to ``build_recommendation_system_prompt``.

    Args:
        content_type: Type of content being recommended

    Returns:
        System prompt string
    """
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()
    identity = ADVISOR_IDENTITY.format(domain=content_type_name.lower())

    return (
        f"You are {identity}. Write enthusiastic, specific blurbs"
        " connecting each pick to the user's taste. Be concise."
    )


def build_blurb_prompt(
    content_type: ContentType,
    selected_items: list[ContentItem],
    consumed_items: list[ContentItem],
) -> str:
    """Build a prompt for writing blurbs about pre-selected recommendation items.

    Unlike ``build_recommendation_prompt`` which asks the LLM to pick N from
    50 candidates, this prompt presents only the pre-selected items and asks
    for 2-3 sentence blurbs. This saves ~1,000-2,000 tokens.

    Args:
        content_type: Type of content being recommended
        selected_items: Pre-selected items to write blurbs for
        consumed_items: User's favorites for taste reference

    Returns:
        Formatted prompt string
    """
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()

    # Build taste context from top consumed items
    favorites = [item for item in consumed_items if item.rating and item.rating >= 4]
    context_text = ""
    if favorites:
        context_text = "Their favorites:\n"
        for item in favorites[:5]:
            rating_text = f"{item.rating}/5" if item.rating else "Unrated"
            author_text = f" by {item.author}" if item.author else ""
            context_text += f"- **{item.title}**{author_text} ({rating_text})\n"
        context_text += "\n"

    # Build selected items list
    items_text = ""
    for item_index, item in enumerate(selected_items, 1):
        author_text = f" by {item.author}" if item.author else ""
        items_text += f"{item_index}. {item.title}{author_text}\n"

    return f"""Write a 2-3 sentence blurb for each of these {content_type_name.lower()} picks explaining WHY it fits this person's taste.

{context_text}Picks:
{items_text}
Rules:
- Numbered list: "1. **Title** by Author" then reasoning on the next lines
- Connect to specific favorites above — mention titles and ratings
- Address them as "you"
- Be enthusiastic and specific, not generic"""


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
        content_type_str = get_enum_value(item.content_type)
        if "pages" in item.metadata and content_type_str == "book":
            parts.append(f"Pages: {item.metadata['pages']}")

    return " | ".join(parts)
