"""Semantic genre clusters for cross-content-type matching.

Groups normalized genre/tag terms into thematic clusters so that items
sharing a theme (e.g. a book tagged "space warfare" and a TV show tagged
"war") can be connected even when they share no raw terms.

Terms intentionally appear in multiple clusters (e.g. "quest" in both
*fantasy* and *adventure_exploration*) to create richer thematic links.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cluster definitions
# ---------------------------------------------------------------------------

CLUSTERS: dict[str, set[str]] = {
    "science_fiction": {
        "science fiction",
        "cyberpunk",
        "steampunk",
        "space",
        "space opera",
        "space warfare",
        "robots",
        "android",
        "cyborg",
        "alien",
        "extraterrestrial",
        "time travel",
        "artificial intelligence",
        "virtual reality",
        "simulation",
        "matrix",
        "singularity",
        "transhumanism",
        "nanotechnology",
        "genetic engineering",
        "clone",
        "near future",
        "future",
        "parallel universe",
        "life on other planets",
        "interplanetary voyages",
        "spaceship",
        "space station",
    },
    "fantasy": {
        "fantasy",
        "magic",
        "wizard",
        "wizards",
        "witch",
        "witches",
        "sorcery",
        "dragons",
        "elves",
        "dwarves",
        "orcs",
        "fairies",
        "mythology",
        "folklore",
        "fairy tale",
        "legend",
        "epic",
        "quest",
        "sword",
        "medieval",
        "knights",
        "kingdom",
        "royalty",
        "prophecy",
        "chosen one",
        "good and evil",
        "dark lord",
    },
    "war_military": {
        "war",
        "military",
        "soldier",
        "battle",
        "combat",
        "space warfare",
        "world war",
        "world war i",
        "world war ii",
        "vietnam war",
        "cold war",
        "navy",
        "army",
        "marines",
        "special forces",
    },
    "horror_dark": {
        "horror",
        "supernatural",
        "paranormal",
        "occult",
        "gothic",
        "vampire",
        "werewolf",
        "zombie",
        "monster",
        "creature",
        "slasher",
        "survival horror",
        "haunted house",
        "dark",
    },
    "crime_thriller": {
        "crime",
        "thriller",
        "mystery",
        "detective",
        "spy",
        "murder",
        "serial killer",
        "heist",
        "robbery",
        "kidnapping",
        "hostage",
        "police",
        "fbi",
        "cia",
        "noir",
        "neo-noir",
        "stealth",
        "conspiracy",
    },
    "adventure_exploration": {
        "adventure",
        "exploration",
        "quest",
        "open world",
        "survival",
        "journey",
        "expedition",
        "treasure",
        "road trip",
        "travel",
        "pirate",
        "island",
    },
    "post_apocalyptic": {
        "apocalyptic",
        "post-apocalyptic",
        "dystopia",
        "dystopian",
        "pandemic",
        "survival",
        "disaster",
        "invasion",
    },
    "space_opera": {
        "space opera",
        "space",
        "spaceship",
        "space station",
        "space warfare",
        "interplanetary voyages",
        "life on other planets",
    },
    "drama_emotional": {
        "drama",
        "emotional",
        "coming of age",
        "bildungsroman",
        "redemption",
        "friendship",
        "heartwarming",
        "heartbreaking",
        "inspiring",
        "tragic",
        "melancholic",
        "uplifting",
    },
    "comedy_lighthearted": {
        "comedy",
        "satirical",
        "parody",
        "absurd",
        "feel-good",
    },
    "rpg_narrative": {
        "rpg",
        "action rpg",
        "jrpg",
        "story rich",
        "narrative",
        "choices matter",
        "multiple endings",
    },
    "action_combat": {
        "action",
        "shooter",
        "first person shooter",
        "third person shooter",
        "martial arts",
        "hack and slash",
        "beat em up",
        "fight",
        "gun",
        "shootout",
        "explosion",
        "violence",
        "car chase",
    },
    "strategy_tactics": {
        "strategy",
        "rts",
        "turn-based",
        "real-time",
        "management",
        "puzzle",
    },
    "historical": {
        "historical",
        "period piece",
        "medieval",
        "victorian",
        "renaissance",
        "ancient",
        "1920s",
        "1930s",
        "1940s",
        "1950s",
        "1960s",
        "1970s",
        "1980s",
        "1990s",
        "world war",
        "world war i",
        "world war ii",
        "cold war",
        "vietnam war",
    },
    "western": {
        "western",
        "cowboy",
    },
    "romance": {
        "romance",
        "love",
        "marriage",
    },
}

# ---------------------------------------------------------------------------
# Pre-computed reverse index
# ---------------------------------------------------------------------------

_TERM_TO_CLUSTERS: dict[str, set[str]] = {}

for _cluster_name, _terms in CLUSTERS.items():
    for _term in _terms:
        _TERM_TO_CLUSTERS.setdefault(_term, set()).add(_cluster_name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_clusters_for_terms(terms: list[str]) -> set[str]:
    """Return all cluster names that any of *terms* belongs to.

    Args:
        terms: Normalized genre/tag strings.

    Returns:
        Set of cluster name strings.
    """
    clusters: set[str] = set()
    for term in terms:
        matching = _TERM_TO_CLUSTERS.get(term)
        if matching:
            clusters.update(matching)
    return clusters


def cluster_overlap(terms_a: list[str], terms_b: list[str]) -> float:
    """Jaccard similarity of the cluster memberships of two term lists.

    Args:
        terms_a: First set of normalized genre/tag strings.
        terms_b: Second set of normalized genre/tag strings.

    Returns:
        Jaccard similarity in ``[0.0, 1.0]``.
    """
    clusters_a = get_clusters_for_terms(terms_a)
    clusters_b = get_clusters_for_terms(terms_b)

    if not clusters_a or not clusters_b:
        return 0.0

    intersection = clusters_a & clusters_b
    union = clusters_a | clusters_b
    return len(intersection) / len(union)
