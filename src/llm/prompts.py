"""Prompt templates for LLM interactions."""

import random

from src.llm.tone import ADVISOR_IDENTITY, PERSONALITY_TRAITS, STYLE_RULES
from src.models.content import ContentItem, ContentType, get_enum_value
from src.recommendations.constants import (
    CROSS_TYPE_MIN_OVERLAP,
    SCORE_PROXIMITY_THRESHOLD,
)
from src.recommendations.genre_clusters import cluster_overlap as _cluster_overlap
from src.recommendations.scorers import extract_creator, extract_genres
from src.utils.series import get_series_name
from src.utils.text import extract_raw_genres, format_genre_tag, sanitize_prompt_text


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
    safe_title = sanitize_prompt_text(item.title)
    safe_author = sanitize_prompt_text(item.author) if item.author else ""
    author_text = f" by {safe_author}" if safe_author else ""
    genre_tag = format_genre_tag(item)
    safe_review = sanitize_prompt_text(item.review) if item.review else ""
    review_text = (
        f' — Review: "{safe_review}"' if include_review and safe_review else ""
    )

    # Annotate series entries so the LLM refers to "the Harry Potter series"
    # instead of treating "Harry Potter and the Order of the Phoenix" as a
    # series name.
    raw_series_name = get_series_name(item)
    series_name = sanitize_prompt_text(raw_series_name) if raw_series_name else ""
    series_tag = f" ({series_name} series)" if series_name else ""

    if include_type_label:
        type_label = get_enum_value(item.content_type).replace("_", " ")
        return (
            f"- [{type_label}] **{safe_title}**{series_tag}{author_text}"
            f"{genre_tag}{review_text}\n"
        )
    return f"- **{safe_title}**{series_tag}{author_text}{genre_tag}{review_text}\n"


def _shuffle_items_by_rating_tier(items: list[ContentItem]) -> list[ContentItem]:
    """Sort by rating DESC and shuffle within same-rating groups for variety."""
    by_rating: dict[int, list[ContentItem]] = {}
    for item in items:
        by_rating.setdefault(item.rating or 0, []).append(item)
    result: list[ContentItem] = []
    for rating in sorted(by_rating, reverse=True):
        group = by_rating[rating]
        random.shuffle(group)
        result.extend(group)
    return result


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

    # Build context from consumed items — include reviews for same-type
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

    # Shuffle within same-rating tiers for variety
    same_type_items = _shuffle_items_by_rating_tier(same_type_items)
    cross_type_items = _shuffle_items_by_rating_tier(cross_type_items)

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
        safe_title = sanitize_prompt_text(item.title)
        safe_author = sanitize_prompt_text(item.author) if item.author else ""
        author_text = f" by {safe_author}" if safe_author else ""
        genre_tag = format_genre_tag(item)
        items_text += f"{item_index}. {safe_title}{author_text}{genre_tag}\n"

    prompt = f"""Pick the {count} best {content_type_name.lower()}s for this person from the candidates below.

{context_text}

Candidates (NOT yet consumed):

{items_text}

For each pick, write 2-3 sentences explaining WHY it's a great fit. Connect it to specific items they enjoyed — mention titles. Be enthusiastic and specific, not generic.

IMPORTANT formatting rules:
- Use a numbered list: "1. **Title**" on the first line, reasoning on the next lines
- Do NOT start the reasoning with "Reasoning:" or any label — just dive straight into WHY
- Use **bold** for emphasis on key connections
- Address them as "you" — never say "the user"
- Only pick from the candidates list above
- Only reference what's shown above — do NOT invent quotes, opinions, or facts about items
- Each review belongs to the item on the SAME line — do NOT attribute it to a different item
- Author names are exact — do NOT claim two items share an author unless the names shown above match
- Write about the recommended item itself — do NOT describe its sequels, prequels, or other entries in the same franchise
- Do NOT reference other candidates as things the user has consumed — they have NOT consumed any candidate. Only reference items from the favorites list above.
- Use correct verbs for each content type — you READ books, WATCH movies and TV shows, and PLAY video games
- Do NOT include genre tags like (Comedy) or (Fantasy) in the blurb text — only use genres to understand the item
- Do NOT justify a pick by referencing the user's experience with the pick's OWN series — that's circular. Connect it to DIFFERENT favorites.
- Vary your opening — do NOT start with formulaic phrases like "You'll adore", "You'll love", or "If you enjoyed" — jump straight into WHAT connects the pick to their taste"""

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
- Only reference what's shown in the user's item list — do NOT invent quotes, opinions, or facts
- Each review belongs to the item on the SAME line — never attribute it to a different item
- Do NOT claim items share the same author unless the author names shown are identical
- A book is NOT a show, a movie is NOT a game — use the correct content type; you READ books, WATCH movies and TV shows, and PLAY video games
- Write about the RECOMMENDED item itself — NOT about its sequels, prequels, or other entries in the same franchise
- NEVER reference candidates or picks as things the user has consumed — they are unconsumed recommendations
- Do NOT include genre tags like (Comedy) or (Fantasy) in recommendation text — genres are metadata, not prose
- Do NOT justify a pick by referencing the user's experience with the pick's OWN series — that's circular; connect it to DIFFERENT favorites
- Vary your opening — do NOT start with formulaic phrases like "You'll adore", "You'll love", or "If you enjoyed" — jump straight into WHAT connects the pick to their taste"""


def _shuffle_close_scores(
    items_with_scores: list[tuple[ContentItem, float]],
) -> list[ContentItem]:
    """Shuffle items whose relevance scores are within a small tolerance.

    Items are already sorted by descending score.  Adjacent items whose
    scores differ by at most ``SCORE_PROXIMITY_THRESHOLD`` are grouped
    and shuffled so the ordering varies across runs while still respecting
    meaningful relevance differences.

    Replicates the engine's ``_shuffle_close_scores`` logic without
    importing from the engine (avoids tight coupling).
    """
    if not items_with_scores:
        return []

    groups: list[list[ContentItem]] = [[items_with_scores[0][0]]]
    group_score = items_with_scores[0][1]

    for item, score in items_with_scores[1:]:
        if group_score - score <= SCORE_PROXIMITY_THRESHOLD:
            groups[-1].append(item)
        else:
            groups.append([item])
            group_score = score

    result: list[ContentItem] = []
    for group in groups:
        random.shuffle(group)
        result.extend(group)
    return result


def _score_favorites_by_relevance(
    favorites: list[ContentItem],
    target_items: list[ContentItem],
    candidate_type: str,
    cap: int,
) -> tuple[list[ContentItem], list[ContentItem]]:
    """Score and select favorites most relevant to the target items.

    Uses the same scoring model as the engine's
    ``_find_contributing_reference_items``: Jaccard for same-type,
    ``cluster_overlap`` for cross-type, creator match bonus, and
    high-rating boost.

    Args:
        favorites: High-rated consumed items (rating >= 4).
        target_items: The recommendation items to match against.
        candidate_type: The content type string of the recommendations.
        cap: Maximum total favorites to return.

    Returns:
        Tuple of (same_type_favorites, cross_type_favorites), each list
        containing up to *cap* items total (same-type fills first).
    """
    # Compute the union of target genres for matching
    target_genres_set: set[str] = set()
    target_genres_list: list[str] = []
    target_creators: set[str] = set()
    for target in target_items:
        genres = extract_genres(target)
        target_genres_set.update(genres)
        target_genres_list.extend(genres)
        creator = extract_creator(target)
        if creator:
            target_creators.add(creator)

    scored: list[tuple[ContentItem, float]] = []
    for fav in favorites:
        overlap = 0.0
        fav_genres = extract_genres(fav)
        fav_genres_set = set(fav_genres)
        fav_type = get_enum_value(fav.content_type)
        is_same_type = fav_type == candidate_type

        if target_genres_set and fav_genres_set:
            if is_same_type:
                # Same type: raw Jaccard (shared vocabulary)
                intersection = target_genres_set & fav_genres_set
                if intersection:
                    overlap += len(intersection) / len(
                        target_genres_set | fav_genres_set
                    )
            else:
                # Cross type: thematic cluster overlap
                overlap += _cluster_overlap(target_genres_list, fav_genres)

        fav_creator = extract_creator(fav)
        if fav_creator and fav_creator in target_creators:
            overlap += 0.5

        if fav.rating and fav.rating >= 4:
            overlap += 0.15

        if overlap > 0:
            scored.append((fav, overlap))

    scored.sort(key=lambda pair: pair[1], reverse=True)

    # Split into same-type and cross-type, respecting the overlap threshold
    same_type: list[tuple[ContentItem, float]] = []
    cross_type: list[tuple[ContentItem, float]] = []
    for item, score in scored:
        item_type = get_enum_value(item.content_type)
        if item_type == candidate_type:
            same_type.append((item, score))
        elif score >= CROSS_TYPE_MIN_OVERLAP:
            cross_type.append((item, score))

    # Fill same-type first, then cross-type up to cap
    same_result = _shuffle_close_scores(same_type[:cap])
    remaining = cap - len(same_result)
    cross_result = (
        _shuffle_close_scores(cross_type[:remaining]) if remaining > 0 else []
    )

    return same_result, cross_result


def _build_blurb_taste_context(
    content_type: ContentType,
    consumed_items: list[ContentItem],
    target_items: list[ContentItem] | None = None,
) -> str:
    """Build taste context text for blurb prompts.

    When *target_items* are provided, selects up to 5 genre-relevant
    favorites using the engine's scoring model (Jaccard, cluster overlap,
    creator bonus, rating boost).  Otherwise falls back to rating-sorted
    selection with tier shuffling.

    Args:
        content_type: Type of content being recommended
        consumed_items: User's consumed items
        target_items: Recommendation items to match favorites against

    Returns:
        Formatted taste context string (may be empty)
    """
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()

    favorites = [item for item in consumed_items if item.rating and item.rating >= 4]

    context_same: list[ContentItem]
    context_cross: list[ContentItem]

    # Only use genre-relevance scoring when target items have genre data;
    # otherwise, scoring has nothing to work with and the fallback is better.
    if target_items and any(extract_raw_genres(t) for t in target_items):
        context_same, context_cross = _score_favorites_by_relevance(
            favorites, target_items, content_type_str, cap=5
        )
    else:
        # Fallback: sort by rating DESC, shuffle within same-rating tiers
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

        same_type_favorites = _shuffle_items_by_rating_tier(same_type_favorites)
        cross_type_favorites = _shuffle_items_by_rating_tier(cross_type_favorites)

        if len(same_type_favorites) >= 5:
            context_same = same_type_favorites[:5]
            context_cross = []
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

    return context_text


def build_blurb_system_prompt(content_type: ContentType) -> str:
    """Build a slim system prompt for writing blurbs about pre-selected items.

    Uses the advisor identity plus core behavioural guardrails (no spoilers,
    no fabricated quotes, no misattribution). Omits
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
        " NEVER invent quotes, opinions, or facts the user did not express."
        " NEVER misattribute reviews or authors between items."
        " NEVER reveal plot twists, endings, or major surprises."
        " Write about the RECOMMENDED item itself — NEVER write about its"
        " sequels, prequels, or other series entries instead."
        " NEVER reference other picks as things the user has consumed —"
        " only reference the user's favorites."
        " When a favorite is part of a series, refer to the SERIES by its"
        " series name (shown in parentheses), not the individual entry title."
        " NEVER claim a referenced item has genres, settings, or themes"
        " that are not listed in its genre brackets — a fantasy series is"
        " NOT set in space, and a sci-fi series is NOT set in a medieval realm."
        " Use correct verbs for each content type — you READ books,"
        " WATCH movies and TV shows, and PLAY video games."
        " Do NOT include genre tags like (Comedy) or (Fantasy) in the"
        " blurb text — genres are metadata for your understanding, not prose."
        " Do NOT justify a pick by saying the user enjoyed the pick's OWN"
        " series — that's circular; connect it to DIFFERENT favorites."
        " Vary your opening — do NOT start with formulaic phrases like"
        " 'You'll adore', 'You'll love', or 'If you enjoyed'."
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

    context_text = _build_blurb_taste_context(
        content_type, consumed_items, target_items=selected_items
    )

    # Build selected items list, with optional per-item reference lines
    items_text = ""
    for ref_index, item in enumerate(selected_items):
        safe_title = sanitize_prompt_text(item.title)
        safe_author = sanitize_prompt_text(item.author) if item.author else ""
        author_text = f" by {safe_author}" if safe_author else ""
        genre_tag = format_genre_tag(item)
        items_text += f"{ref_index + 1}. {safe_title}{author_text}{genre_tag}\n"

        if per_item_references and ref_index < len(per_item_references):
            refs = per_item_references[ref_index]
            if refs:
                ref_parts = [f"{ref.title}{format_genre_tag(ref)}" for ref in refs]
                items_text += f"   Related: {', '.join(ref_parts)}\n"

    return f"""Write a 2-3 sentence blurb for each of these {content_type_name.lower()} picks explaining WHY it fits this person's taste.

{context_text}Picks:
{items_text}
Rules:
- Numbered list: "1. **Title**" then reasoning on the next lines
- Connect each pick to its Related items when listed, otherwise to favorites above — mention specific titles
- Address them as "you"
- Be enthusiastic and specific, not generic
- Only reference what's shown above — do NOT invent quotes, opinions, or facts about items
- Each review belongs to the item on the SAME line — do NOT attribute it to a different item
- Author names are exact — do NOT claim two items share an author unless the names shown above match
- Do NOT reveal plot twists, endings, or major surprises
- Each blurb must describe the PICK itself — do NOT write about its sequels, prequels, or other series entries
- Do NOT reference other picks as things the user has consumed — they have NOT consumed any pick. Only reference favorites listed above.
- When a favorite is part of a series, refer to the SERIES by its series name (shown in parentheses), not the individual entry title
- NEVER claim a referenced item has genres, settings, or themes not listed in its genre brackets — a fantasy series is NOT set in space
- Use correct verbs for each content type — you READ books, WATCH movies and TV shows, and PLAY video games
- Do NOT include genre tags like (Comedy) or (Fantasy) in the blurb text — only use genres to understand the item
- Do NOT justify a pick by referencing the user's experience with the pick's OWN series — that's circular. Connect it to DIFFERENT favorites.
- Vary your opening — do NOT start with formulaic phrases like "You'll adore", "You'll love", or "If you enjoyed" — jump straight into WHAT connects the pick to their taste"""


def build_single_blurb_prompt(
    content_type: ContentType,
    item: ContentItem,
    consumed_items: list[ContentItem],
    references: list[ContentItem] | None = None,
) -> str:
    """Build a prompt for writing a blurb about exactly ONE recommendation item.

    Unlike ``build_blurb_prompt`` which asks for a numbered list of blurbs,
    this prompt targets a single item and expects raw prose back — no title
    prefix, no numbered list.  This eliminates the need for response parsing
    and title-matching, which is the root cause of blurb mismatches for
    movies and TV shows.

    Args:
        content_type: Type of content being recommended
        item: The single pre-selected item to write a blurb for
        consumed_items: User's favorites for taste reference
        references: Genre-relevant reference items that contributed to
            recommending this item.

    Returns:
        Formatted prompt string
    """
    content_type_str = get_enum_value(content_type)
    content_type_name = content_type_str.replace("_", " ").title()

    context_text = _build_blurb_taste_context(
        content_type, consumed_items, target_items=[item]
    )

    # Build the single item line
    safe_title = sanitize_prompt_text(item.title)
    safe_author = sanitize_prompt_text(item.author) if item.author else ""
    author_text = f" by {safe_author}" if safe_author else ""
    genre_tag = format_genre_tag(item)
    item_line = f"{safe_title}{author_text}{genre_tag}"

    # Optional reference line (with genre tags so the LLM knows each
    # reference's actual genre and doesn't invent settings or themes).
    ref_line = ""
    if references:
        ref_parts = [f"{ref.title}{format_genre_tag(ref)}" for ref in references]
        ref_line = f"\nRelated: {', '.join(ref_parts)}"

    return f"""Write a 2-3 sentence blurb explaining WHY this {content_type_name.lower()} pick fits this person's taste.

{context_text}Pick: {item_line}{ref_line}

Rules:
- Write only the blurb — no title, no numbered list, just the explanation
- Connect the pick to its Related items when listed, otherwise to favorites above — mention specific titles
- Address them as "you"
- Be enthusiastic and specific, not generic
- Only reference what's shown above — do NOT invent quotes, opinions, or facts about items
- Each review belongs to the item on the SAME line — do NOT attribute it to a different item
- Author names are exact — do NOT claim two items share an author unless the names shown above match
- Do NOT reveal plot twists, endings, or major surprises
- Describe the PICK itself — do NOT write about its sequels, prequels, or other series entries
- Do NOT reference any other recommendation as something the user has consumed — only reference favorites listed above.
- When a favorite is part of a series, refer to the SERIES by its series name (shown in parentheses), not the individual entry title
- NEVER claim a referenced item has genres, settings, or themes not listed in its genre brackets — a fantasy series is NOT set in space
- Use correct verbs for each content type — you READ books, WATCH movies and TV shows, and PLAY video games
- Do NOT include genre tags like (Comedy) or (Fantasy) in the blurb text — only use genres to understand the item
- Do NOT justify a pick by referencing the user's experience with the pick's OWN series — that's circular. Connect it to DIFFERENT favorites.
- Vary your opening — do NOT start with "You'll adore", "You'll love", or "If you enjoyed" — jump straight into WHAT connects the pick to their taste"""


def build_content_description(item: ContentItem) -> str:
    """Build a text description of a content item for embedding.

    Args:
        item: ContentItem to describe

    Returns:
        Description string
    """
    parts = [sanitize_prompt_text(item.title)]

    if item.author:
        parts.append(f"by {sanitize_prompt_text(item.author)}")

    if item.review:
        parts.append(f"Review: {sanitize_prompt_text(item.review)}")

    genres = extract_raw_genres(item)
    if genres:
        parts.append(f"Genre: {', '.join(genres)}")

    if item.metadata:
        content_type_str = get_enum_value(item.content_type)
        if "pages" in item.metadata and content_type_str == "book":
            parts.append(f"Pages: {item.metadata['pages']}")

    return " | ".join(parts)
