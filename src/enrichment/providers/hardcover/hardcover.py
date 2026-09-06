import logging
import math
import re
from typing import Any
from urllib.parse import urljoin

import requests

from src.enrichment.matching import (
    MINIMUM_TITLE_SIMILARITY,
    best_match_index,
    title_similarity,
)
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    ProviderError,
    SeriesOrdinal,
    log_search_title,
)
from src.ingestion.urls import (
    MAX_SAME_ORIGIN_REDIRECTS,
    REDIRECT_STATUSES,
    REQUEST_TIMEOUT,
    same_origin,
)
from src.models.content import ContentItem, ContentType
from src.utils.request_errors import scrub_request_error
from src.utils.series import split_series_from_title
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"

_SERIES_QUERY = """
query BookSeriesPosition($where: books_bool_exp!, $limit: Int!) {
  books(where: $where, limit: $limit, order_by: {users_count: desc}) {
    title
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
    if stated is None:
        return None
    try:
        position = float(stated)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(position):
        return None
    name = str((membership.get("series") or {}).get("name") or "").strip()
    return SeriesOrdinal(position=position, series=name or None)


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
        return "Series positions for books from Hardcover"

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

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            logger.debug("Hardcover is enabled with no token stored; stating nothing")
            return None

        book = self._match(item, api_key)
        return _series_ordinal(book) if book is not None else None

    def _match(self, item: ContentItem, api_key: str) -> dict[str, Any] | None:
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
            "query": _SERIES_QUERY,
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
