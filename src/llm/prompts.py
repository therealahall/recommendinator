"""Prompt templates for LLM interactions."""

from src.llm.tone import ADVISOR_IDENTITY, PERSONALITY_TRAITS, STYLE_RULES
from src.models.content import ContentItem, ContentType, get_enum_value
from src.utils.text import extract_raw_genres, format_genre_tag


def _format_context_item(
    item: ContentItem,
    *,
    include_type_label: bool,
    include_review: bool = False,
) -> str:
    """Format a single consumed item as a context line for LLM prompts.

    Args:
        item: Content item to format
        include_type_label: Whether to prepend a [type] prefix
        include_review: Whether to include the user's review text

    Returns:
        Formatted context line ending with newline
    """
    rating_text = f"{item.rating}/5" if item.rating else "Unrated"
    author_text = f" by {item.author}" if item.author else ""
    genre_tag = format_genre_tag(item)
    review_text = (
        f' — Review: "{item.review}"' if include_review and item.review else ""
    )

    if include_type_label:
        type_label = get_enum_value(item.content_type).replace("_", " ")
        return (
            f"- [{type_label}] **{item.title}**{author_text}"
            f" ({rating_text}){genre_tag}{review_text}\n"
        )
    return (
        f"- **{item.title}**{author_text}" f" ({rating_text}){genre_tag}{review_text}\n"
    )


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

    # Split into same-type and cross-type to reduce confusion for small models
    same_type_items = [
        item
        for item in high_rated
        if get_enum_value(item.content_type) == content_type_str
    ]
    cross_type_items = [
        item
        for item in high_rated
        if get_enum_value(item.content_type) != content_type_str
    ]

    # When >= 5 same-type items exist, use only same-type (no cross-type noise).
    # When < 5 same-type items, fill remaining slots with cross-type items.
    if len(same_type_items) >= 5:
        context_same = same_type_items[:10]
        context_cross: list[ContentItem] = []
    else:
        context_same = same_type_items
        remaining_slots = 10 - len(context_same)
        context_cross = cross_type_items[:remaining_slots]

    context_text = ""
    if context_same:
        context_text = f"Here are {content_type_name.lower()}s they love:\n\n"
        for item in context_same:
            # Same-type items keep reviews — no type label (redundant, confuses 3B models)
            context_text += _format_context_item(
                item, include_type_label=False, include_review=True
            )

    if context_cross:
        separator = "\n" if context_same else ""
        context_text += f"{separator}From other types they've enjoyed:\n\n"
        for item in context_cross:
            # No review text for cross-type items — prevents review misattribution
            context_text += _format_context_item(
                item, include_type_label=True, include_review=False
            )

    # Build list of unconsumed items
    items_text = ""
    for item_index, item in enumerate(unconsumed_items[:50], 1):
        author_text = f" by {item.author}" if item.author else ""
        genre_tag = format_genre_tag(item)
        items_text += f"{item_index}. {item.title}{author_text}{genre_tag}\n"

    prompt = f"""Pick the {count} best {content_type_name.lower()}s for this person from the candidates below.

{context_text}

Candidates (NOT yet consumed):

{items_text}

For each pick, write 2-3 sentences explaining WHY it's a great fit. Connect it to specific items they rated highly — mention titles and ratings. Be enthusiastic and specific, not generic.

IMPORTANT formatting rules:
- Use a numbered list: "1. **Title**" on the first line, reasoning on the next lines
- Do NOT start the reasoning with "Reasoning:" or any label — just dive straight into WHY
- Use **bold** for emphasis on key connections
- Address them as "you" — never say "the user"
- Only pick from the candidates list above
- ONLY quote reviews that appear above. If no review is shown for an item, do NOT invent one.
- Do NOT use your general knowledge to fabricate what someone thought or felt — only reference reviews and ratings explicitly shown above
- Each review belongs to the item on the SAME line — do NOT attribute it to a different item
- Author names are exact — do NOT claim two items share an author unless the names shown above match
- Reference ratings as numbers — do NOT interpret them as emotions or sentiments
- Write about the recommended item itself — do NOT describe its sequels, prequels, or other entries in the same franchise
- Do NOT reference other candidates as things the user has played or enjoyed — they have NOT consumed any candidate. Only reference items from the rated list above."""

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
- Only recommend items from the provided candidate list

## Data Accuracy
- ONLY reference reviews or quotes that appear in the user's item list
- If an item has no review, reference only its rating — NEVER invent quotes
- Do NOT use your general knowledge to fabricate what they thought or felt about an item — only reference reviews and ratings explicitly provided
- Each review belongs to the item on the SAME line — never attribute a review to a different item
- Do NOT claim items share the same author unless the author names shown are identical
- A book is NOT a show, a movie is NOT a game — use the correct content type
- Write about the RECOMMENDED item itself — NOT about its sequels, prequels, or other entries in the same series that appear in the user's history
- NEVER reference other candidates or picks as things the user has consumed — they are unconsumed recommendations"""


def build_blurb_system_prompt(content_type: ContentType) -> str:
    """Build a slim system prompt for writing blurbs about pre-selected items.

    Uses the advisor identity plus core behavioural guardrails (no spoilers,
    no fabricated quotes, no sentiment inference from ratings). Omits
    PERSONALITY_TRAITS and the full STYLE_RULES list to save ~500 tokens
    compared to ``build_recommendation_system_prompt``.

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
        " NEVER invent quotes or reviews the user did not write."
        " NEVER use general knowledge to fabricate what they thought or felt."
        " NEVER misattribute reviews or author connections between items."
        " NEVER reveal plot twists, endings, or major surprises."
        " State ratings as numbers, never interpret them as emotions or sentiments."
        " Write about the RECOMMENDED item itself — NEVER write about its"
        " sequels, prequels, or other series entries instead."
        " NEVER reference other picks as things the user has consumed —"
        " only reference the user's favorites."
    )


def build_blurb_prompt(
    content_type: ContentType,
    selected_items: list[ContentItem],
    consumed_items: list[ContentItem],
    per_item_references: list[list[ContentItem]] | None = None,
) -> str:
    """Build a prompt for writing blurbs about pre-selected recommendation items.

    Unlike ``build_recommendation_prompt`` which asks the LLM to pick N from
    50 candidates, this prompt presents only the pre-selected items and asks
    for 2-3 sentence blurbs. This saves ~1,000-2,000 tokens.

    Args:
        content_type: Type of content being recommended
        selected_items: Pre-selected items to write blurbs for
        consumed_items: User's favorites for taste reference
        per_item_references: Genre-relevant reference items for each pick.
            ``per_item_references[i]`` lists the consumed items that
            contributed to recommending ``selected_items[i]``.

    Returns:
        Formatted prompt string
    """
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()

    # Build taste context from top consumed items — split by content type
    favorites = [item for item in consumed_items if item.rating and item.rating >= 4]
    same_type_favorites = [
        item
        for item in favorites
        if get_enum_value(item.content_type) == content_type_str
    ]
    cross_type_favorites = [
        item
        for item in favorites
        if get_enum_value(item.content_type) != content_type_str
    ]

    # When >= 5 same-type favorites exist, use only same-type (no cross-type noise).
    # When < 5 same-type, fill remaining slots with cross-type items.
    if len(same_type_favorites) >= 5:
        context_same = same_type_favorites[:5]
        context_cross: list[ContentItem] = []
    else:
        context_same = same_type_favorites
        remaining_slots = 5 - len(context_same)
        context_cross = cross_type_favorites[:remaining_slots]

    context_text = ""
    if context_same:
        context_text = f"Their favorite {content_type_name.lower()}s:\n"
        for item in context_same:
            context_text += _format_context_item(
                item, include_type_label=False, include_review=False
            )

    if context_cross:
        separator = "\n" if context_same else ""
        context_text += f"{separator}From other types:\n"
        for item in context_cross:
            context_text += _format_context_item(
                item, include_type_label=True, include_review=False
            )

    if context_text:
        context_text += "\n"

    # Build selected items list, with optional per-item reference lines
    items_text = ""
    for ref_index, item in enumerate(selected_items):
        author_text = f" by {item.author}" if item.author else ""
        genre_tag = format_genre_tag(item)
        items_text += f"{ref_index + 1}. {item.title}{author_text}{genre_tag}\n"

        if per_item_references and ref_index < len(per_item_references):
            refs = per_item_references[ref_index]
            if refs:
                ref_parts = [
                    f"{ref.title} ({ref.rating}/5)" if ref.rating else ref.title
                    for ref in refs
                ]
                items_text += f"   Related: {', '.join(ref_parts)}\n"

    return f"""Write a 2-3 sentence blurb for each of these {content_type_name.lower()} picks explaining WHY it fits this person's taste.

{context_text}Picks:
{items_text}
Rules:
- Numbered list: "1. **Title**" then reasoning on the next lines
- Connect each pick to its Related items when listed, otherwise to favorites above — mention titles and ratings
- Address them as "you"
- Be enthusiastic and specific, not generic
- Do NOT invent quotes or opinions — only reference what's shown above
- Do NOT use your general knowledge to fabricate what someone thought or felt — only reference reviews and ratings explicitly shown above
- Each review belongs to the item on the SAME line — do NOT attribute it to a different item
- Author names are exact — do NOT claim two items share an author unless the names shown above match
- Do NOT reveal plot twists, endings, or major surprises
- Reference ratings as numbers — do NOT interpret them as emotions or sentiments
- Each blurb must describe the PICK itself — do NOT write about its sequels, prequels, or other series entries
- Do NOT reference other picks as things the user has played or enjoyed — they have NOT consumed any pick. Only reference favorites listed above."""


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

    genres = extract_raw_genres(item)
    if genres:
        parts.append(f"Genre: {', '.join(genres)}")

    if item.metadata:
        content_type_str = get_enum_value(item.content_type)
        if "pages" in item.metadata and content_type_str == "book":
            parts.append(f"Pages: {item.metadata['pages']}")

    return " | ".join(parts)
