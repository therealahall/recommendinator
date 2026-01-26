"""Content type models."""

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ContentType(str, Enum):
    """Supported content types."""

    BOOK = "book"
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    VIDEO_GAME = "video_game"


class ConsumptionStatus(str, Enum):
    """Status of content consumption."""

    UNREAD = "unread"
    CURRENTLY_CONSUMING = "currently_consuming"
    COMPLETED = "completed"


class ContentItem(BaseModel):
    """Represents a piece of content (book, movie, etc.)."""

    model_config = ConfigDict(use_enum_values=True)

    # User association
    user_id: int = 1  # Default to default user

    # Core fields
    id: str | None = None  # External ID from source (Goodreads ID, Steam app ID, etc.)
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

    # Flexible metadata for type-specific fields
    metadata: dict = Field(default_factory=dict)
