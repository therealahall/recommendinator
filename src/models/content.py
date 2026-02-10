"""Content type models."""

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Default user ID used across the application when no user is specified
DEFAULT_USER_ID = 1


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


def get_enum_value(value: "Enum | str | Any") -> str:
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
    author: str | None = (
        None  # Primary creator (author for books, kept for convenience)
    )
    rating: int | None = Field(None, ge=1, le=5)
    review: str | None = None
    date_completed: date | None = None

    # Source tracking - which plugin/source this came from
    source: str | None = None  # e.g., "goodreads", "steam", "manual"

    # Runtime-only: parent item ID (e.g., TV show ID for a season item).
    # Set during recommendation expansion, not persisted.
    parent_id: str | None = None

    # Whether this item is ignored (excluded from recommendations)
    ignored: bool = False

    # Flexible metadata for type-specific fields
    metadata: dict[str, Any] = Field(default_factory=dict)
