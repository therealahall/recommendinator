from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_USER_ID = 1

# Longest ``review`` all four writing surfaces accept (both CLI commands, both
# endpoints), so a review one of them stores is never one another refuses to
# re-save. Far longer than a review worth writing, short enough to refuse a
# pasted document.
MAX_REVIEW_LENGTH = 10000

# The manual-metadata bounds `library edit` and ``PATCH /api/items/{id}`` share.
# The CLI writes past Pydantic, so a description it stored over the web's bound
# leaves that item unsavable in the edit dialog, which resends it every save.
MAX_GENRES = 50
MAX_TAGS = 100
MAX_GENRE_TAG_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 10000

MIN_RELEASE_YEAR = 1800
MAX_RELEASE_YEAR = 2200
MAX_CREATOR_LENGTH = 500
MAX_TITLE_LENGTH = 500

# Enrichment-state filter for content listings. ``None`` (no filter) returns
# every item; the two states partition the library.
EnrichmentFilter = Literal["enriched", "not_enriched"]


class ContentType(str, Enum):
    BOOK = "book"
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    VIDEO_GAME = "video_game"

    @classmethod
    def from_string(cls, value: str) -> "ContentType":
        try:
            return cls(value.lower())
        except ValueError:
            valid = ", ".join(member.value for member in cls)
            raise ValueError(
                f"Invalid content type: '{value}'. Valid types: {valid}"
            ) from None


def get_enum_value(value: "Enum | str") -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


class ConsumptionStatus(str, Enum):
    UNREAD = "unread"
    CURRENTLY_CONSUMING = "currently_consuming"
    COMPLETED = "completed"


class ExternalId(BaseModel):
    source: str
    external_id: str


class ContentItem(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    user_id: int = DEFAULT_USER_ID

    # The id of the source that is saving or saved this item — a save keys on
    # ``(source, id)``. An item collects other sources' ids too; ``external_ids``
    # is the whole set, and is what a reader should report.
    id: str | None = None
    db_id: int | None = None  # Internal database ID (populated when loaded from DB)
    title: str
    content_type: ContentType
    status: ConsumptionStatus

    author: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    review: str | None = None
    date_completed: date | None = None

    source: str | None = None  # e.g., "goodreads_csv", "steam", "manual"

    # Runtime-only: every source's id for this item, populated on read. Empty
    # for an item that has not come back from storage.
    external_ids: list[ExternalId] = Field(default_factory=list)

    # Runtime-only: parent item ID (e.g., TV show ID for a season item).
    # Set during recommendation expansion, not persisted.
    parent_id: str | None = None

    # Runtime-only: whether the item has been enriched (clean enrichment_status
    # row). Populated when read from storage; None when the state is unknown.
    enriched: bool | None = None

    # Runtime-only: enriched by the ``manual`` provider, the state an edit to
    # genres, tags or description writes and only a reset undoes.
    manually_enriched: bool | None = None

    # None means "not specified by this source" — the existing database
    # value is preserved on update.
    ignored: bool | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)
