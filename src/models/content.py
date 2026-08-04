"""Content type models."""

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Default user ID used across the application when no user is specified
DEFAULT_USER_ID = 1

# Longest ``review`` the three surfaces that bound one accept: ``library edit``
# and the request models behind ``PATCH /api/items/{id}`` and
# ``POST /api/complete``. It lives here, with the field it bounds, so those
# three cannot drift apart — a bound one interface applies and another does not
# is a review the user can write in one place and not the other, and one stored
# row the other cannot round-trip. CLI ``complete`` and chat check only that a
# review is not blank, so a longer one still reaches the column through them.
# 10000 characters is far longer than any review worth writing and short enough
# that a pasted document is refused rather than stored whole.
MAX_REVIEW_LENGTH = 10000

# Enrichment-state filter for content listings. ``None`` (no filter) returns
# every item; the two states partition the library.
EnrichmentFilter = Literal["enriched", "not_enriched"]


class ContentType(str, Enum):
    """Supported content types."""

    BOOK = "book"
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    VIDEO_GAME = "video_game"

    @classmethod
    def from_string(cls, value: str) -> "ContentType":
        """Convert a string value to a ContentType enum member.

        Args:
            value: String representation (e.g. "book", "tv_show").

        Returns:
            Corresponding ContentType enum member.

        Raises:
            ValueError: If the value doesn't match any content type.
        """
        try:
            return cls(value.lower())
        except ValueError:
            valid = ", ".join(member.value for member in cls)
            raise ValueError(
                f"Invalid content type: '{value}'. Valid types: {valid}"
            ) from None


def get_enum_value(value: "Enum | str") -> str:
    """Extract the string value from an enum member or pass through strings.

    Handles the common pattern where a value may be either an Enum instance
    (with a .value attribute) or already a plain string (e.g. due to Pydantic's
    use_enum_values=True).

    Args:
        value: An Enum member, string, or other value.

    Returns:
        The string value.
    """
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


class ConsumptionStatus(str, Enum):
    """Status of content consumption."""

    UNREAD = "unread"
    CURRENTLY_CONSUMING = "currently_consuming"
    COMPLETED = "completed"


class ContentItem(BaseModel):
    """Represents a piece of content (book, movie, etc.)."""

    model_config = ConfigDict(use_enum_values=True)

    # User association
    user_id: int = DEFAULT_USER_ID

    # Core fields
    id: str | None = None  # External ID from source (Goodreads ID, Steam app ID, etc.)
    db_id: int | None = None  # Internal database ID (populated when loaded from DB)
    title: str
    content_type: ContentType
    status: ConsumptionStatus

    # Optional fields
    # The type's creator, whichever word that type uses for it: a book's
    # author, a movie's director, a show's creators, a game's developer. It
    # crosses the storage boundary here rather than in ``metadata``.
    author: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    review: str | None = None
    date_completed: date | None = None

    # Source tracking - which plugin/source this came from
    source: str | None = None  # e.g., "goodreads_csv", "steam", "manual"

    # Runtime-only: parent item ID (e.g., TV show ID for a season item).
    # Set during recommendation expansion, not persisted.
    parent_id: str | None = None

    # Runtime-only: whether the item has been enriched (clean enrichment_status
    # row). Populated when read from storage; None when the state is unknown.
    enriched: bool | None = None

    # Whether this item is ignored (excluded from recommendations).
    # None means "not specified by this source" — the existing database
    # value is preserved on update.  True/False explicitly sets the flag.
    ignored: bool | None = None

    # Flexible metadata for type-specific fields
    metadata: dict[str, Any] = Field(default_factory=dict)
