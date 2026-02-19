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
    # Provider compound genres (TMDB, Goodreads, etc.)
    "sci-fi & fantasy": ["science fiction", "fantasy"],
    "sci-fi and fantasy": ["science fiction", "fantasy"],
    "science fiction & fantasy": ["science fiction", "fantasy"],
    "science fiction and fantasy": ["science fiction", "fantasy"],
    "action & adventure": ["action", "adventure"],
    "action and adventure": ["action", "adventure"],
    "war & politics": ["war", "politics"],
    "war and politics": ["war", "politics"],
    "mystery & thriller": ["mystery", "thriller"],
    "mystery and thriller": ["mystery", "thriller"],
    "crime & thriller": ["crime", "thriller"],
    "crime and thriller": ["crime", "thriller"],
    "horror & thriller": ["horror", "thriller"],
    "horror and thriller": ["horror", "thriller"],
    "romance & fantasy": ["romance", "fantasy"],
    "romance and fantasy": ["romance", "fantasy"],
    "comedy & drama": ["comedy", "drama"],
    "comedy and drama": ["comedy", "drama"],
    "drama & romance": ["drama", "romance"],
    "drama and romance": ["drama", "romance"],
    "sci-fi & horror": ["science fiction", "horror"],
    "sci-fi and horror": ["science fiction", "horror"],
    "science fiction & horror": ["science fiction", "horror"],
    "science fiction and horror": ["science fiction", "horror"],
    "horror & fantasy": ["horror", "fantasy"],
    "horror and fantasy": ["horror", "fantasy"],
    "history & politics": ["history", "politics"],
    "history and politics": ["history", "politics"],
}

# Normalization mappings (maps variations to canonical form)
NORMALIZATIONS = {
    # Science Fiction variations
    "sci-fi": "science fiction",
    "scifi": "science fiction",
    "sci fi": "science fiction",
    "sf": "science fiction",
    "science-fiction": "science fiction",
    # Fantasy variations (preserve meaningful subgenres, only collapse noise)
    "fantasy fiction": "fantasy",
    # Horror variations
    "horror fiction": "horror",
    "lovecraftian": "cosmic horror",
    "lovecraftian horror": "cosmic horror",
    "cthulhu mythos": "cosmic horror",
    # Mystery/Thriller
    "mystery fiction": "mystery",
    "thriller fiction": "thriller",
    "suspense fiction": "thriller",
    "suspense": "thriller",
    "whodunnit": "whodunit",
    "who done it": "whodunit",
    "hard-boiled": "hardboiled",
    "hard boiled": "hardboiled",
    "hard-boiled detective": "hardboiled",
    # Action/Adventure
    "adventure fiction": "adventure",
    "action-adventure": "action adventure",
    # Romance
    "romance fiction": "romance",
    "romantic fiction": "romance",
    "love stories": "romance",
    "rom-com": "romantic comedy",
    "romcom": "romantic comedy",
    # Historical
    "historical fiction": "historical",
    "history fiction": "historical",
    "alternative history": "alternate history",
    # Western
    "swords and sorcery": "sword and sorcery",
    "sword & sorcery": "sword and sorcery",
    # Other normalizations
    "biographical": "biography",
    "biographies": "biography",
    "biographical fiction": "biography",
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
    "post apocalyptic": "post-apocalyptic",
    "post apocalypse": "post-apocalyptic",
    "postapocalyptic": "post-apocalyptic",
    "dystopia": "dystopian",
    "post cyberpunk": "post-cyberpunk",
    "self discovery": "self-discovery",
    # Anime normalizations
    "shounen": "shonen",
    "shoujo": "shojo",
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
    "role-playing game": "rpg",
    "roleplaying": "rpg",
    "fps": "first person shooter",
    "first-person shooter": "first person shooter",
    "tower defence": "tower defense",
    "turn based": "turn-based",
    "real time strategy": "rts",
    "real-time strategy": "rts",
}

# Curated list of useful genres and tags to keep
# This is permissive but filters out truly useless terms
ALLOWED_TERMS = {
    # -----------------------------------------------------------------------
    # Core genres
    # -----------------------------------------------------------------------
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
    # -----------------------------------------------------------------------
    # Science fiction subgenres
    # -----------------------------------------------------------------------
    "hard science fiction",
    "soft science fiction",
    "military science fiction",
    "space opera",
    "space warfare",
    "space western",
    "cyberpunk",
    "steampunk",
    "biopunk",
    "dieselpunk",
    "solarpunk",
    "atompunk",
    "nanopunk",
    "clockpunk",
    "post-cyberpunk",
    "afrofuturism",
    "retrofuturism",
    "climate fiction",
    "science fantasy",
    "alternate history",
    "first contact",
    "generation ship",
    "colonization",
    "terraforming",
    "mecha",
    "galactic empire",
    "planetary romance",
    "dying earth",
    # Sci-fi themes
    "space",
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
    "spaceship",
    "space station",
    "space colony",
    "alien planet",
    "mars",
    "moon",
    # -----------------------------------------------------------------------
    # Fantasy subgenres
    # -----------------------------------------------------------------------
    "high fantasy",
    "epic fantasy",
    "low fantasy",
    "dark fantasy",
    "urban fantasy",
    "contemporary fantasy",
    "historical fantasy",
    "grimdark",
    "noblebright",
    "sword and sorcery",
    "portal fantasy",
    "gaslamp fantasy",
    "flintlock fantasy",
    "magical realism",
    "new weird",
    "weird fiction",
    "slipstream",
    "fairy tale retelling",
    "heroic fantasy",
    "comic fantasy",
    "cozy fantasy",
    "romantasy",
    "progression fantasy",
    "litrpg",
    "gamelit",
    "isekai",
    "wuxia",
    "xianxia",
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
    "fae",
    "changeling",
    "shapeshifter",
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
    "necromancy",
    "alchemy",
    "enchantment",
    "summoning",
    "elemental magic",
    "blood magic",
    "divine magic",
    "court intrigue",
    # -----------------------------------------------------------------------
    # Horror subgenres
    # -----------------------------------------------------------------------
    "cosmic horror",
    "eldritch",
    "body horror",
    "folk horror",
    "psychological horror",
    "supernatural horror",
    "southern gothic",
    "eco horror",
    "techno horror",
    "religious horror",
    "isolation horror",
    "cult horror",
    "splatterpunk",
    "creature feature",
    "found footage",
    "ghost story",
    "haunting",
    "possession",
    "demonic",
    "witchcraft",
    # Horror themes
    "gothic",
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
    "haunted house",
    "survival horror",
    # -----------------------------------------------------------------------
    # Mystery / thriller subgenres
    # -----------------------------------------------------------------------
    "cozy mystery",
    "police procedural",
    "amateur sleuth",
    "whodunit",
    "locked room mystery",
    "hardboiled",
    "private detective",
    "psychological thriller",
    "domestic thriller",
    "legal thriller",
    "medical thriller",
    "techno thriller",
    "political thriller",
    "financial thriller",
    "espionage",
    "caper",
    "cold case",
    "forensic",
    "organized crime",
    "noir",
    "neo-noir",
    "psychological",
    # -----------------------------------------------------------------------
    # Romance subgenres
    # -----------------------------------------------------------------------
    "contemporary romance",
    "historical romance",
    "regency romance",
    "paranormal romance",
    "romantic suspense",
    "romantic comedy",
    "dark romance",
    "erotic romance",
    "military romance",
    "slow burn",
    "enemies to lovers",
    "friends to lovers",
    "forbidden love",
    "second chance",
    "love triangle",
    "found family",
    "star-crossed",
    "arranged marriage",
    # -----------------------------------------------------------------------
    # Drama / literary subgenres
    # -----------------------------------------------------------------------
    "literary fiction",
    "family saga",
    "social commentary",
    "political fiction",
    "philosophical fiction",
    "epistolary",
    "metafiction",
    "satire",
    "social drama",
    "melodrama",
    "tragicomedy",
    "ensemble",
    "biographical fiction",
    "unreliable narrator",
    # -----------------------------------------------------------------------
    # Western subgenres
    # -----------------------------------------------------------------------
    "spaghetti western",
    "neo-western",
    "weird western",
    "outlaw",
    "gunslinger",
    "frontier",
    "marshal",
    # -----------------------------------------------------------------------
    # Nonfiction genres
    # -----------------------------------------------------------------------
    "memoir",
    "autobiography",
    "true crime",
    "narrative nonfiction",
    "popular science",
    "nature writing",
    "travel writing",
    "creative nonfiction",
    "biopic",
    "docudrama",
    "mockumentary",
    # -----------------------------------------------------------------------
    # Apocalyptic / dystopian
    # -----------------------------------------------------------------------
    "apocalyptic",
    "post-apocalyptic",
    "dystopian",
    "utopia",
    "pandemic",
    "nuclear",
    "disaster",
    "invasion",
    "survival",
    # -----------------------------------------------------------------------
    # Themes — narrative / thematic elements
    # -----------------------------------------------------------------------
    # Power and society
    "sacrifice",
    "power",
    "oppression",
    "rebellion",
    "revolution",
    "resistance",
    "freedom",
    "liberation",
    "class struggle",
    "inequality",
    "prejudice",
    "colonialism",
    "imperialism",
    "propaganda",
    "surveillance",
    "totalitarianism",
    "anarchy",
    # Personal / psychological
    "identity",
    "self-discovery",
    "transformation",
    "obsession",
    "jealousy",
    "grief",
    "loss",
    "guilt",
    "shame",
    "atonement",
    "trauma",
    "addiction",
    "healing",
    "mental health",
    "madness",
    # Relationships / social
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
    "isolation",
    "loneliness",
    "alienation",
    "belonging",
    "community",
    "loyalty",
    "honor",
    "duty",
    "ambition",
    "greed",
    "hubris",
    # Philosophical / existential
    "fate",
    "destiny",
    "free will",
    "immortality",
    "mortality",
    "innocence",
    "hope",
    "despair",
    "forgiveness",
    "memory",
    "tradition",
    "change",
    "progress",
    "moral ambiguity",
    "forbidden knowledge",
    "nihilism",
    "good versus evil",
    "order versus chaos",
    "man versus nature",
    "man versus machine",
    "man versus society",
    "legacy",
    "inheritance",
    "curse",
    "plague",
    # -----------------------------------------------------------------------
    # Narrative tropes / plot patterns
    # -----------------------------------------------------------------------
    "time loop",
    "hidden identity",
    "secret society",
    "ancient conspiracy",
    "fish out of water",
    "race against time",
    "undercover",
    "body swap",
    "amnesia",
    "prophecy fulfilled",
    "treasure hunt",
    "rescue mission",
    "fall from grace",
    "descent into madness",
    # -----------------------------------------------------------------------
    # Tone / mood / atmosphere
    # -----------------------------------------------------------------------
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
    "bleak",
    "brutal",
    "eerie",
    "creepy",
    "unsettling",
    "disturbing",
    "foreboding",
    "ominous",
    "claustrophobic",
    "oppressive",
    "ethereal",
    "dreamlike",
    "surreal",
    "whimsical",
    "lighthearted",
    "playful",
    "witty",
    "sardonic",
    "cynical",
    "ironic",
    "campy",
    "pulpy",
    "cozy",
    "comforting",
    "hopeful",
    "optimistic",
    "bittersweet",
    "wistful",
    "nostalgic",
    "poignant",
    "raw",
    "visceral",
    "lyrical",
    "meditative",
    "contemplative",
    "cerebral",
    "introspective",
    "cinematic",
    "grand",
    "sweeping",
    "intimate",
    "sparse",
    "minimalist",
    "adventurous",
    "thrilling",
    "fast-paced",
    "slow-paced",
    "frenetic",
    "chaotic",
    "somber",
    "brooding",
    "moody",
    "romantic",
    "passionate",
    # -----------------------------------------------------------------------
    # Settings / locations
    # -----------------------------------------------------------------------
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
    "underwater",
    "jungle",
    "desert",
    "arctic",
    "mountain",
    "forest",
    "ocean",
    "castle",
    "fortress",
    "mansion",
    "estate",
    "dungeon",
    "ruins",
    "wasteland",
    "cave",
    "underground",
    "swamp",
    "bayou",
    "tundra",
    "savanna",
    "wilderness",
    "countryside",
    "coast",
    "volcano",
    "monastery",
    "temple",
    "cathedral",
    "tavern",
    "inn",
    "laboratory",
    "factory",
    "bunker",
    "arena",
    "megacity",
    "slum",
    "skyscraper",
    "boarding school",
    "asylum",
    "cemetery",
    "carnival",
    "pirate ship",
    "submarine",
    "train",
    "airship",
    "cabin",
    "farmhouse",
    "library",
    "trench",
    "battlefield",
    "military base",
    "virtual world",
    "cyberspace",
    "dream world",
    "afterlife",
    # -----------------------------------------------------------------------
    # Time periods / eras
    # -----------------------------------------------------------------------
    "historical",
    "period piece",
    "prehistoric",
    "classical antiquity",
    "dark ages",
    "ancient",
    "renaissance",
    "elizabethan",
    "colonial era",
    "age of sail",
    "regency",
    "victorian",
    "edwardian",
    "belle epoque",
    "roaring twenties",
    "prohibition era",
    "great depression",
    "interwar period",
    "1920s",
    "1930s",
    "1940s",
    "1950s",
    "1960s",
    "1970s",
    "1980s",
    "1990s",
    "2000s",
    "2010s",
    "contemporary",
    "future",
    "near future",
    "far future",
    "world war",
    "world war i",
    "world war ii",
    "cold war",
    "vietnam war",
    # -----------------------------------------------------------------------
    # Character archetypes
    # -----------------------------------------------------------------------
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
    "reluctant hero",
    "tragic hero",
    "byronic hero",
    "everyman",
    "underdog",
    "outcast",
    "outsider",
    "rebel",
    "revolutionary",
    "vigilante",
    "rogue",
    "trickster",
    "mentor",
    "femme fatale",
    "con artist",
    "hacker",
    "mercenary",
    "bounty hunter",
    "explorer",
    "adventurer",
    "orphan",
    "heir",
    "prince",
    "princess",
    "king",
    "queen",
    "emperor",
    "monster hunter",
    "smuggler",
    "refugee",
    # -----------------------------------------------------------------------
    # Action / violence
    # -----------------------------------------------------------------------
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
    "swashbuckler",
    # -----------------------------------------------------------------------
    # Game genres
    # -----------------------------------------------------------------------
    "rpg",
    "action rpg",
    "jrpg",
    "crpg",
    "mmorpg",
    "tactical rpg",
    "strategy",
    "rts",
    "turn-based",
    "real-time",
    "grand strategy",
    "4x",
    "moba",
    "puzzle",
    "platformer",
    "shooter",
    "first person shooter",
    "third person shooter",
    "stealth",
    "roguelike",
    "roguelite",
    "metroidvania",
    "souls-like",
    "sandbox",
    "open world",
    "linear",
    "exploration",
    "crafting",
    "building",
    "base building",
    "management",
    "racing",
    "fighting",
    "beat em up",
    "hack and slash",
    "indie",
    "dungeon crawler",
    "visual novel",
    "walking simulator",
    "point and click",
    "interactive fiction",
    "immersive sim",
    "deckbuilder",
    "card game",
    "board game",
    "battle royale",
    "tower defense",
    "city builder",
    "farming sim",
    "life sim",
    "dating sim",
    "colony sim",
    "tycoon",
    "god game",
    "idle",
    "clicker",
    "bullet hell",
    "extraction shooter",
    "survival crafting",
    "auto battler",
    "party game",
    "rhythm game",
    "cozy game",
    "horror adventure",
    "procedural generation",
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
    "branching narrative",
    "nonlinear",
    "episodic",
    "serialized",
    "replayable",
    "difficult",
    "challenging",
    "casual",
    "relaxing",
    # -----------------------------------------------------------------------
    # Media types / formats
    # -----------------------------------------------------------------------
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
    # Anime / manga terms
    "shonen",
    "seinen",
    "shojo",
    "josei",
    "slice of life",
    "magical girl",
    # -----------------------------------------------------------------------
    # Adaptations
    # -----------------------------------------------------------------------
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
    # -----------------------------------------------------------------------
    # Age / audience
    # -----------------------------------------------------------------------
    "children",
    "kids",
    "family friendly",
    "young adult",
    "teen",
    "adult",
    "mature",
    "r-rated",
    # -----------------------------------------------------------------------
    # Awards / recognition
    # -----------------------------------------------------------------------
    "classic",
    "cult classic",
    "award winning",
    "critically acclaimed",
    # -----------------------------------------------------------------------
    # Miscellaneous useful
    # -----------------------------------------------------------------------
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
    "warfare",
    "guerrilla warfare",
    "tactical",
    "naval",
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
    "dinosaurs",
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
