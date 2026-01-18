"""Content type models."""

from enum import Enum
from typing import Optional
from datetime import date
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

    id: Optional[str] = None
    title: str
    author: Optional[str] = None
    content_type: ContentType
    rating: Optional[int] = Field(None, ge=1, le=5)
    review: Optional[str] = None
    status: ConsumptionStatus
    date_completed: Optional[date] = None
    metadata: dict = Field(default_factory=dict)
