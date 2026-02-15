"""Genre and tag normalization for cross-content-type matching.

Normalizes genres and tags from different providers (TMDB, OpenLibrary, RAWG)
to a common vocabulary, filtering out noise and platform-specific terms.
"""

import re

# Patterns to strip from the beginning of terms
PREFIX_PATTERNS = [
    r"^fiction,\s*",
    r"^genre:\s*",
    r"^firm:\s*",
    r"^subject:\s*",
]

# Patterns to strip from the end of terms
SUFFIX_PATTERNS = [
    r",\s*fiction$",
    r",\s*general$",
    r"\s*\(fictitious character\).*$",
    r"\s*\(imaginary place\).*$",
    r"\s*\(imaginary\).*$",
]

# Terms to completely exclude (noise)
EXCLUDED_TERMS = {
    # Steam/game platform noise
    "steam achievements",
    "steam cloud",
    "steam-trading-cards",
    "full controller support",
    "partial controller support",
    "steam workshop",
    "remote play",
    "cloud saves",
    "controller",
    "achievements",
    # Movie metadata noise
    "aftercreditsstinger",
    "duringcreditsstinger",
    # Book metadata noise
    "large type books",
    "large print books",
    "open library staff picks",
    "staff picks",
    "juvenile literature",
    "juvenile works",
    "american literature",
    "english literature",
    "british literature",
    "fiction in english",
    "nonfiction",
    "accessible book",
    "protected daisy",
    "lending library",
    "overdrive",
    "internet archive wishlist",
    # Too vague
    "fiction",
    "general",
    "literature",
    "reading",
    "books",
    "novels",
    "stories",
}

# Patterns that indicate noise (checked with 'in')
EXCLUDED_PATTERNS = [
    "nyt:",
    "(imaginary place)",
    "(fictitious character)",
    "large type",
    "staff picks",
    "accessible book",
    "protected daisy",
    "lending library",
    "overdrive",
    "wishlist",
]

# Compound genre splits — expanded before individual normalization so
# both constituent terms are preserved.
COMPOUND_SPLITS: dict[str, list[str]] = {
    "sci-fi & fantasy": ["science fiction", "fantasy"],
    "sci-fi and fantasy": ["science fiction", "fantasy"],
    "action & adventure": ["action", "adventure"],
    "action and adventure": ["action", "adventure"],
    "war & politics": ["war", "politics"],
    "war and politics": ["war", "politics"],
    "science fiction & fantasy": ["science fiction", "fantasy"],
    "science fiction and fantasy": ["science fiction", "fantasy"],
}

# Normalization mappings (maps variations to canonical form)
NORMALIZATIONS = {
    # Science Fiction variations
    "sci-fi": "science fiction",
    "scifi": "science fiction",
    "sf": "science fiction",
    "science-fiction": "science fiction",
    # Fantasy variations
    "fantasy fiction": "fantasy",
    "epic fantasy": "fantasy",
    "high fantasy": "fantasy",
    "urban fantasy": "fantasy",
    "dark fantasy": "fantasy",
    # Horror variations
    "horror fiction": "horror",
    "supernatural horror": "horror",
    # Mystery/Thriller
    "mystery fiction": "mystery",
    "thriller fiction": "thriller",
    "suspense fiction": "thriller",
    "suspense": "thriller",
    # Action/Adventure
    "adventure fiction": "adventure",
    # Romance
    "romance fiction": "romance",
    "romantic fiction": "romance",
    "love stories": "romance",
    # Historical
    "historical fiction": "historical",
    "history fiction": "historical",
    # Other normalizations
    "biographical": "biography",
    "biographies": "biography",
    "comedies": "comedy",
    "documentaries": "documentary",
    "animated": "animation",
    "children's fiction": "children",
    "childrens": "children",
    "young adult fiction": "young adult",
    "ya": "young adult",
    "mini-series": "miniseries",
    "tv movie": "television",
    "tv special": "television",
    # Game-specific
    "singleplayer": "single-player",
    "single player": "single-player",
    "multiplayer": "multi-player",
    "multi player": "multi-player",
    "co-op": "cooperative",
    "coop": "cooperative",
    "online co-op": "cooperative",
    "local co-op": "cooperative",
    "first-person": "first person",
    "third-person": "third person",
    "role-playing": "rpg",
    "role playing": "rpg",
    "roleplaying": "rpg",
    "fps": "first person shooter",
    "first-person shooter": "first person shooter",
}

# Curated list of useful genres and tags to keep
# This is permissive but filters out truly useless terms
ALLOWED_TERMS = {
    # Core genres
    "action",
    "adventure",
    "animation",
    "biography",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "family",
    "fantasy",
    "history",
    "horror",
    "music",
    "musical",
    "mystery",
    "romance",
    "science fiction",
    "sport",
    "sports",
    "thriller",
    "war",
    "western",
    # Sub-genres and themes
    "anthology",
    "apocalyptic",
    "post-apocalyptic",
    "dystopia",
    "dystopian",
    "utopia",
    "cyberpunk",
    "steampunk",
    "noir",
    "neo-noir",
    "gothic",
    "psychological",
    "paranormal",
    "supernatural",
    "occult",
    "slasher",
    "zombie",
    "vampire",
    "werewolf",
    "monster",
    "creature",
    "alien",
    "extraterrestrial",
    "invasion",
    "disaster",
    "survival",
    "pandemic",
    # Sci-fi themes
    "space",
    "space opera",
    "space warfare",
    "interplanetary voyages",
    "life on other planets",
    "time travel",
    "parallel universe",
    "artificial intelligence",
    "robots",
    "android",
    "cyborg",
    "clone",
    "genetic engineering",
    "nanotechnology",
    "virtual reality",
    "simulation",
    "matrix",
    "singularity",
    "transhumanism",
    # Fantasy themes
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
    # Character types
    "superhero",
    "supervillain",
    "antihero",
    "villain",
    "hero",
    "heroes",
    "female protagonist",
    "male protagonist",
    "ensemble cast",
    "strong female lead",
    "detective",
    "spy",
    "assassin",
    "warrior",
    "soldier",
    "pirate",
    "ninja",
    "samurai",
    "cowboy",
    # Settings
    "new york city",
    "los angeles",
    "london",
    "tokyo",
    "paris",
    "small town",
    "rural",
    "urban",
    "suburban",
    "island",
    "prison",
    "school",
    "college",
    "hospital",
    "haunted house",
    "spaceship",
    "space station",
    "underwater",
    "jungle",
    "desert",
    "arctic",
    "mountain",
    "forest",
    "ocean",
    # Time periods
    "historical",
    "period piece",
    "1920s",
    "1930s",
    "1940s",
    "1950s",
    "1960s",
    "1970s",
    "1980s",
    "1990s",
    "2000s",
    "future",
    "near future",
    "ancient",
    "renaissance",
    "victorian",
    "world war",
    "world war i",
    "world war ii",
    "cold war",
    "vietnam war",
    # Emotional/Tone
    "dark",
    "gritty",
    "atmospheric",
    "suspenseful",
    "tense",
    "emotional",
    "heartwarming",
    "heartbreaking",
    "inspiring",
    "thought-provoking",
    "satirical",
    "parody",
    "absurd",
    "feel-good",
    "uplifting",
    "tragic",
    "melancholic",
    # Relationships/Social
    "friendship",
    "love",
    "marriage",
    "divorce",
    "parent-child",
    "sibling",
    "rivalry",
    "revenge",
    "betrayal",
    "redemption",
    "coming of age",
    "bildungsroman",
    "midlife crisis",
    # Action/Violence
    "violence",
    "violent",
    "gore",
    "gory",
    "murder",
    "serial killer",
    "heist",
    "robbery",
    "chase",
    "escape",
    "kidnapping",
    "hostage",
    "martial arts",
    "gun",
    "shootout",
    "explosion",
    "car chase",
    "fight",
    "battle",
    "combat",
    "duel",
    # Game genres
    "rpg",
    "action rpg",
    "jrpg",
    "mmorpg",
    "strategy",
    "rts",
    "turn-based",
    "real-time",
    "puzzle",
    "platformer",
    "shooter",
    "first person shooter",
    "third person shooter",
    "stealth",
    "survival horror",
    "roguelike",
    "roguelite",
    "metroidvania",
    "sandbox",
    "open world",
    "linear",
    "exploration",
    "crafting",
    "building",
    "management",
    "racing",
    "fighting",
    "beat em up",
    "hack and slash",
    "souls-like",
    "indie",
    # Game features
    "single-player",
    "multi-player",
    "cooperative",
    "competitive",
    "pvp",
    "pve",
    "online",
    "local",
    "split-screen",
    "story rich",
    "narrative",
    "choices matter",
    "multiple endings",
    "replayable",
    "difficult",
    "challenging",
    "casual",
    "relaxing",
    # Media types
    "anime",
    "manga",
    "cartoon",
    "animated",
    "live action",
    "black and white",
    "silent",
    "foreign",
    "international",
    "miniseries",
    "limited series",
    "reality",
    "game show",
    "talk show",
    "news",
    "wrestling",
    "competition",
    # Adaptations
    "based on novel",
    "based on book",
    "based on novel or book",
    "based on comic",
    "based on manga",
    "based on video game",
    "based on true story",
    "based on true events",
    "remake",
    "reboot",
    "sequel",
    "prequel",
    "spinoff",
    "adaptation",
    # Age/Audience
    "children",
    "kids",
    "family friendly",
    "young adult",
    "teen",
    "adult",
    "mature",
    "r-rated",
    # Awards/Recognition
    "classic",
    "cult classic",
    "award winning",
    "critically acclaimed",
    # Miscellaneous useful
    "christmas",
    "holiday",
    "halloween",
    "summer",
    "winter",
    "road trip",
    "travel",
    "journey",
    "expedition",
    "treasure",
    "conspiracy",
    "government",
    "politics",
    "corruption",
    "justice",
    "law",
    "legal",
    "courtroom",
    "police",
    "fbi",
    "cia",
    "military",
    "navy",
    "army",
    "marines",
    "special forces",
    "religion",
    "faith",
    "spiritual",
    "philosophy",
    "existential",
    "technology",
    "hacking",
    "computer",
    "internet",
    "social media",
    "musician",
    "band",
    "concert",
    "dancing",
    "art",
    "artist",
    "painting",
    "photography",
    "food",
    "cooking",
    "restaurant",
    "chef",
    "nature",
    "animals",
    "wildlife",
    "environmental",
    "football",
    "basketball",
    "baseball",
    "soccer",
    "boxing",
    "mma",
    "olympics",
}


def normalize_term(term: str) -> str | None:
    """Normalize a genre/tag term.

    Cleans prefixes/suffixes, applies normalization mappings,
    and filters out noise terms.

    Args:
        term: Raw genre or tag string

    Returns:
        Normalized term, or None if it should be filtered out
    """
    if not term:
        return None

    # Lowercase and strip
    normalized = term.lower().strip()

    # Check for excluded patterns first
    for pattern in EXCLUDED_PATTERNS:
        if pattern in normalized:
            return None

    # Strip prefixes
    for pattern in PREFIX_PATTERNS:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)

    # Strip suffixes
    for pattern in SUFFIX_PATTERNS:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)

    # Strip again after pattern removal
    normalized = normalized.strip()

    # Check if in excluded terms
    if normalized in EXCLUDED_TERMS:
        return None

    # Apply normalizations
    if normalized in NORMALIZATIONS:
        normalized = NORMALIZATIONS[normalized]

    # Check if allowed (or if it's a reasonably short, clean term)
    if normalized in ALLOWED_TERMS:
        return normalized

    # Allow terms that are short and don't contain problematic patterns
    if len(normalized) <= 25 and normalized.replace(" ", "").isalpha():
        # Only allow if it seems like a real genre/tag
        # (not too long, only letters/spaces)
        return normalized

    return None


def normalize_terms(terms: list[str]) -> list[str]:
    """Normalize a list of genre/tag terms.

    Compound genres (e.g. ``"Sci-Fi & Fantasy"``) are split into their
    constituent terms before individual normalization, so both components
    are preserved.

    Args:
        terms: List of raw genre or tag strings

    Returns:
        List of normalized, deduplicated terms
    """
    # Expand compound terms first
    expanded: list[str] = []
    for term in terms:
        lower = term.lower().strip()
        if lower in COMPOUND_SPLITS:
            expanded.extend(COMPOUND_SPLITS[lower])
        else:
            expanded.append(term)

    seen: set[str] = set()
    result: list[str] = []

    for term in expanded:
        normalized = normalize_term(term)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result


def extract_and_normalize_genres(metadata: dict | None) -> list[str]:
    """Extract and normalize genres and tags from item metadata.

    Args:
        metadata: Item metadata dict

    Returns:
        List of normalized genre/tag terms
    """
    if not metadata:
        return []

    terms = []

    # Extract from genre field
    if "genre" in metadata and metadata["genre"]:
        genre = metadata["genre"]
        if isinstance(genre, str):
            terms.append(genre)
        elif isinstance(genre, list):
            terms.extend(genre)

    # Extract from genres field
    if "genres" in metadata and metadata["genres"]:
        genres = metadata["genres"]
        if isinstance(genres, str):
            # Could be comma-separated or JSON
            if genres.startswith("["):
                import json

                try:
                    terms.extend(json.loads(genres))
                except json.JSONDecodeError:
                    terms.extend(g.strip() for g in genres.split(","))
            else:
                terms.extend(g.strip() for g in genres.split(","))
        elif isinstance(genres, list):
            terms.extend(genres)

    # Extract from tags field
    if "tags" in metadata and metadata["tags"]:
        tags = metadata["tags"]
        if isinstance(tags, str):
            if tags.startswith("["):
                import json

                try:
                    terms.extend(json.loads(tags))
                except json.JSONDecodeError:
                    terms.extend(t.strip() for t in tags.split(","))
            else:
                terms.extend(t.strip() for t in tags.split(","))
        elif isinstance(tags, list):
            terms.extend(tags)

    return normalize_terms(terms)
