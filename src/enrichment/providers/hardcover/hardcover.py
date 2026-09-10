import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    SeriesOrdinal,
    log_search_title,
    pinned_record,
)
from src.ingestion.urls import (
    MAX_SAME_ORIGIN_REDIRECTS,
    REDIRECT_STATUSES,
    REQUEST_TIMEOUT,
    same_origin,
)
from src.models.content import ContentItem, ContentType, get_enum_value
from src.utils.matching import (
    MINIMUM_TITLE_SIMILARITY,
    Candidate,
    best_match_index,
    title_similarity,
    year_of,
)
from src.utils.request_errors import scrub_request_error
from src.utils.series import split_series_from_title, valid_series_position
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

_BOOK_QUERY = """
query Books($where: books_bool_exp!, $limit: Int!) {
  books(where: $where, limit: $limit, order_by: {users_count: desc}) {
    id
    title
    description
    release_year
    image { url }
    cached_tags
    contributions { author { name } }
    featured_book_series { position series { name } }
  }
}
"""

# Hardcover points a merged duplicate at the record with a null canonical_id.
_ONLY_THE_CANONICAL_RECORD = {"canonical_id": {"_is_null": True}}

_CANDIDATE_LIMIT = 5

_ISBN_FIELDS = {10: "isbn_10", 13: "isbn_13"}

_LIKE_METACHARACTERS = str.maketrans({"\\": r"\\", "%": r"\%", "_": r"\_"})

_AUTHOR_SEPARATORS = re.compile(r"\s*(?:[,;&]|\band\b)\s*")

#: ``cached_tags`` is keyed by category. The others — "Mood", "Tag" and
#: "Content Warning" — rate a reading experience rather than name a genre, and
#: no cluster in genre_clusters.py reaches them.
_GENRE_CATEGORY = "Genre"


def _normalized_isbn(raw: Any) -> str:
    return "".join(char for char in str(raw or "") if char.isalnum()).upper()


def _isbn_where(metadata: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("isbn13", "isbn"):
        isbn = _normalized_isbn(metadata.get(key))
        field = _ISBN_FIELDS.get(len(isbn))
        if field:
            return {"editions": {field: {"_eq": isbn}}}
    return None


def _title_where(title: str) -> dict[str, Any]:
    return {"title": {"_ilike": f"%{title.translate(_LIKE_METACHARACTERS)}%"}}


def _states_the_author(book: dict[str, Any], author: str) -> bool:
    """One co-written book is one `author` field here and one contributor each
    at Hardcover, so a whole "A, B" string never resembles either name.
    """
    stated = [
        str(((contribution or {}).get("author") or {}).get("name") or "")
        for contribution in book.get("contributions") or []
    ]
    return any(
        title_similarity(credited, name) >= MINIMUM_TITLE_SIMILARITY
        for credited in _AUTHOR_SEPARATORS.split(author)
        for name in stated
    )


def _candidate(book: dict[str, Any]) -> Candidate:
    credited = [
        str(((contribution or {}).get("author") or {}).get("name") or "")
        for contribution in book.get("contributions") or []
    ]
    return Candidate(
        record_id=str(book.get("id") or ""),
        title=str(book.get("title") or ""),
        year=year_of(book.get("release_year")),
        creator=", ".join(name for name in credited if name) or None,
        cover_url=(book.get("image") or {}).get("url"),
    )


def _sole_close_title(
    searched: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Refused rather than guessed: an authored ordinal replaces a title marker."""
    titles: list[tuple[list[str], int | None]] = [
        ([str(book.get("title") or "")], None) for book in candidates
    ]
    index = best_match_index(searched, None, titles)
    if index is None:
        return None
    others = titles[:index] + titles[index + 1 :]
    if best_match_index(searched, None, others) is not None:
        return None
    return candidates[index]


def _series_ordinal(book: dict[str, Any]) -> SeriesOrdinal | None:
    membership: dict[str, Any] = book.get("featured_book_series") or {}
    stated = membership.get("position")
    # Hardcover features a novella's own sub-series, so an unnamed position has
    # nothing to say it counts in the series another provider stored.
    name = str((membership.get("series") or {}).get("name") or "").strip()
    if stated is None or not name:
        return None
    try:
        position = float(stated)
    except (TypeError, ValueError):
        return None
    if not valid_series_position(position):
        return None
    return SeriesOrdinal(position=position, series_name=name)


def _genres(book: dict[str, Any]) -> list[str]:
    categories = book.get("cached_tags")
    if not isinstance(categories, dict):
        return []
    return [
        name
        for entry in categories.get(_GENRE_CATEGORY) or []
        if isinstance(entry, dict) and (name := str(entry.get("tag") or "").strip())
    ]


def _result(book: dict[str, Any]) -> EnrichmentResult:
    """``year_published`` is the only year column a book has, so Hardcover's
    work-level release year lands there (see models/detail_fields.py).
    """
    extra: dict[str, Any] = {}
    year = year_of(book.get("release_year"))
    if year is not None:
        extra["year_published"] = year
    ordinal = _series_ordinal(book)
    if ordinal is not None:
        extra.update(ordinal.as_metadata())

    genres = _genres(book)
    return EnrichmentResult(
        genres=genres or None,
        tags=genres or None,
        description=str(book.get("description") or "").strip() or None,
        cover_url=(book.get("image") or {}).get("url"),
        extra_metadata=extra,
        match_quality="high",
    )


def _refusals(errors: Any) -> str:
    codes = {
        sanitize_for_log(str((error.get("extensions") or {}).get("code") or "unknown"))
        for error in errors
        if isinstance(error, dict)
    }
    return ", ".join(sorted(codes)) or "unknown"


class HardcoverProvider(EnrichmentProvider):
    @property
    def name(self) -> str:
        return "hardcover"

    @property
    def display_name(self) -> str:
        return "Hardcover"

    @property
    def description(self) -> str:
        return "Genres, description, cover and series position for books"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 1.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description=(
                    "Hardcover personal access token "
                    "(get from https://hardcover.app/account/api)"
                ),
                sensitive=True,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required for Hardcover provider")
        return errors

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        api_key = str(config.get("api_key") or "").strip()
        if not api_key or get_enum_value(item.content_type) != ContentType.BOOK.value:
            return None

        book = self._match(item, api_key)
        if book is None:
            return EnrichmentResult(match_quality="not_found")
        return _result(book)

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            logger.debug("Hardcover is enabled with no token stored; stating nothing")
            return None

        book = self._match(item, api_key)
        return _series_ordinal(book) if book is not None else None

    def search(self, item: ContentItem, config: dict[str, Any]) -> list[Candidate]:
        api_key = str(config.get("api_key") or "").strip()
        if not api_key or get_enum_value(item.content_type) != ContentType.BOOK.value:
            return []
        searched, _series = split_series_from_title(item.title)
        log_search_title(logger, item.title, searched)
        return [
            _candidate(book) for book in self._books(_title_where(searched), api_key)
        ]

    def accepts_record_id(self, record_id: str) -> bool:
        return record_id.isdigit()

    def _match(self, item: ContentItem, api_key: str) -> dict[str, Any] | None:
        pinned = pinned_record(item, self.name)
        if pinned is not None:
            books = self._books({"id": {"_eq": pinned}}, api_key)
            return books[0] if books else None

        isbn_clause = _isbn_where(item.metadata)
        if isbn_clause is not None:
            editions = self._books(isbn_clause, api_key)
            if editions:
                return editions[0] if len(editions) == 1 else None

        searched, _series = split_series_from_title(item.title)
        log_search_title(logger, item.title, searched)
        candidates = self._books(_title_where(searched), api_key)
        if item.author:
            candidates = [
                book for book in candidates if _states_the_author(book, item.author)
            ]
        return _sole_close_title(searched, candidates)

    def _books(self, clause: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
        payload = {
            "query": _BOOK_QUERY,
            "variables": {
                "where": {**_ONLY_THE_CANONICAL_RECORD, **clause},
                "limit": _CANDIDATE_LIMIT,
            },
        }
        try:
            response = self._post(payload, api_key)
            response.raise_for_status()
        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Hardcover request failed: {scrub_request_error(error)}"
            ) from error

        body = response.json()
        if body.get("errors"):
            raise ProviderError(
                self.name, f"Hardcover refused the query: {_refusals(body['errors'])}"
            )
        books = (body.get("data") or {}).get("books")
        return books if isinstance(books, list) else []

    def _post(self, payload: dict[str, Any], api_key: str) -> requests.Response:
        """``requests`` replays the Authorization header onto a redirect's host."""
        current = HARDCOVER_API_URL
        for _ in range(MAX_SAME_ORIGIN_REDIRECTS):
            response = requests.post(
                current,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            if response.status_code not in REDIRECT_STATUSES:
                return response

            location = response.headers.get("Location")
            if not location:
                return response
            target = urljoin(current, location)
            if not same_origin(HARDCOVER_API_URL, target):
                raise ProviderError(
                    self.name,
                    f"Refused a redirect to {sanitize_for_log(target)}: it leaves "
                    "the Hardcover origin the token is sent to.",
                )
            current = target

        raise ProviderError(
            self.name,
            f"Hardcover redirected more than {MAX_SAME_ORIGIN_REDIRECTS} times.",
        )
