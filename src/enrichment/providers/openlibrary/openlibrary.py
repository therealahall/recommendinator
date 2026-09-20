import logging
import re
from typing import Any

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    link_record,
    log_search_title,
    pinned_record,
)
from src.ingestion.urls import (
    RedirectRefused,
    fixed_endpoint_refusal,
    request_within_origin,
)
from src.models.content import ContentItem, ContentType, get_enum_value
from src.utils.matching import Candidate, year_of
from src.utils.request_errors import scrub_request_error
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

OPENLIBRARY_API_BASE = "https://openlibrary.org"

SERIES_PATTERN = re.compile(r"\s*\([^)]*#\d+[^)]*\)\s*$")

# Keeps broad categories ("mystery") while filtering verbose library subject headings.
_MAX_SHORT_SUBJECT_LENGTH = 25


_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

#: An Open Library work key. Matched whole because the key is spliced into the
#: ``/works/<key>`` path, where "../" would address another endpoint and
#: "@evil.example/x" another host. ``re.ASCII`` refuses "OL٦٠٣W", which no
#: lookup could ever resolve.
_WORK_KEY = re.compile(r"OL\d+W", re.ASCII)

#: An imported `isbn` column holds whatever the operator's catalogue exported,
#: and it is spliced into the ``/isbn/<value>`` path the same way.
_ISBN = re.compile(r"[0-9X]+", re.ASCII | re.IGNORECASE)

#: An edition key off a pasted link, spliced into ``/books/<key>.json`` as above.
_EDITION_KEY = re.compile(r"OL\d+M", re.ASCII)

#: An author key off a work record, spliced into ``/authors/<key>.json`` as above.
_AUTHOR_KEY = re.compile(r"OL\d+A", re.ASCII)

_OPENLIBRARY_HOSTS = frozenset({"openlibrary.org"})

#: Each link path is also the endpoint resolving the id it names.
_LINK_IDS = {"works": _WORK_KEY, "books": _EDITION_KEY, "isbn": _ISBN}


def _named_record(url: str) -> tuple[str, str] | None:
    named = link_record(url, _OPENLIBRARY_HOSTS, frozenset(_LINK_IDS))
    if named is None or not _LINK_IDS[named[0]].fullmatch(named[1]):
        return None
    return named


def _work_id(key: Any) -> str | None:
    """Open Library names the work in its own responses, and that name is spliced
    into the request path — so only the last segment, and only a work key."""
    record_id = str(key or "").rsplit("/", 1)[-1]
    return record_id if _WORK_KEY.fullmatch(record_id) else None


def _first_work_id(works: Any) -> str | None:
    for entry in works if isinstance(works, list) else []:
        work_id = _work_id(entry.get("key")) if isinstance(entry, dict) else None
        if work_id:
            return work_id
    return None


def _first_author_id(work: dict[str, Any]) -> str | None:
    for entry in work.get("authors") or []:
        author = entry.get("author") if isinstance(entry, dict) else None
        key = author.get("key") if isinstance(author, dict) else None
        author_id = str(key or "").rsplit("/", 1)[-1]
        if _AUTHOR_KEY.fullmatch(author_id):
            return author_id
    return None


def clean_title_for_search(title: str) -> str:
    cleaned = SERIES_PATTERN.sub("", title).strip()
    return cleaned if cleaned else title


def _cover_from_id(cover_id: Any) -> str | None:
    """Open Library records "no cover known" as a negative id."""
    if not isinstance(cover_id, int) or cover_id <= 0:
        return None
    return _COVER_URL.format(cover_id=cover_id)


def _doc_candidate(doc: dict[str, Any]) -> Candidate:
    authors = doc.get("author_name") or []
    return Candidate(
        record_id=str(doc.get("key") or "").split("/")[-1],
        title=str(doc.get("title") or ""),
        year=year_of(doc.get("first_publish_year")),
        creator=str(authors[0]) if authors else None,
        cover_url=_cover_from_id(doc.get("cover_i")),
    )


def _stated(error: ProviderError | requests.RequestException) -> str:
    if isinstance(error, ProviderError):
        return error.message
    return scrub_request_error(error)


def _cover_url(payload: dict[str, Any]) -> str | None:
    covers = payload.get("covers")
    if not isinstance(covers, list):
        return None
    return next((url for url in map(_cover_from_id, covers) if url), None)


class OpenLibraryProvider(EnrichmentProvider):
    @property
    def name(self) -> str:
        return "openlibrary"

    @property
    def display_name(self) -> str:
        return "Open Library"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def precedence(self) -> int:
        return 20

    @property
    def description(self) -> str:
        return "Genres, description and cover for books"

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 1.0

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )

        if content_type != ContentType.BOOK:
            logger.warning("OpenLibrary provider does not support %s", content_type)
            return None

        pinned = pinned_record(item, self.name)
        if pinned is not None and self.accepts_record_id(pinned):
            return self._fetch_work_details(pinned)

        metadata = item.metadata or {}
        isbn = metadata.get("isbn13") or metadata.get("isbn")

        if isbn:
            result = self._lookup_by_isbn(isbn)
            if result and result.match_quality != "not_found":
                return result

        return self._search_book(item)

    def _lookup_by_isbn(self, isbn: str) -> EnrichmentResult | None:
        clean_isbn = isbn.replace("-", "").strip()
        if not _ISBN.fullmatch(clean_isbn):
            return None

        try:
            response = self._get(f"{OPENLIBRARY_API_BASE}/isbn/{clean_isbn}.json")

            if response.status_code == 404:
                return None

            response.raise_for_status()
            edition = self._record(response)

        except (ProviderError, requests.RequestException) as error:
            # The isbn is an imported metadata column, and the error embeds the
            # URL built from it, so neither reaches the log as written.
            logger.warning(
                "ISBN lookup failed for %s: %s",
                sanitize_for_log(isbn),
                _stated(error),
            )
            return None

        work_id = _first_work_id(edition.get("works"))
        if work_id:
            return self._fetch_work_details(work_id, edition)
        return self._build_result_from_edition(edition)

    def search(self, item: ContentItem, config: dict[str, Any]) -> list[Candidate]:
        if get_enum_value(item.content_type) != ContentType.BOOK.value:
            return []
        return [_doc_candidate(doc) for doc in self._search_docs(item)]

    def accepts_record_id(self, record_id: str) -> bool:
        return _WORK_KEY.fullmatch(record_id) is not None

    def claims_url(self, url: str) -> bool:
        return _named_record(url) is not None

    def candidate_from_url(
        self, item: ContentItem, url: str, config: dict[str, Any]
    ) -> Candidate | None:
        named = _named_record(url)
        if named is None:
            return None
        try:
            work_id = self._work_from_link(*named)
            if work_id is None:
                return None
            response = self._get(f"{OPENLIBRARY_API_BASE}/works/{work_id}.json")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            work = self._record(response)
        except requests.RequestException as error:
            raise ProviderError(
                self.name,
                f"Failed to read an Open Library link: {scrub_request_error(error)}",
            ) from None
        # A link is pasted because the search index failed the operator, so the
        # record itself labels the row, and the key the link named pins it.
        return Candidate(
            record_id=work_id,
            title=str(work.get("title") or work_id),
            year=year_of(work.get("first_publish_date")),
            creator=self._author_name(_first_author_id(work)),
            cover_url=_cover_url(work),
        )

    def _author_name(self, author_id: str | None) -> str | None:
        if author_id is None:
            return None
        try:
            response = self._get(f"{OPENLIBRARY_API_BASE}/authors/{author_id}.json")
            response.raise_for_status()
            name = self._record(response).get("name")
        except (ProviderError, requests.RequestException):
            # The author is one field of the row: neither a refused redirect nor
            # an unparseable body may cost the operator the record itself.
            return None
        return str(name) if name else None

    def _work_from_link(self, kind: str, identifier: str) -> str | None:
        """A works link names its work; an edition or isbn one is asked."""
        if kind == "works":
            return identifier
        response = self._get(f"{OPENLIBRARY_API_BASE}/{kind}/{identifier}.json")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _first_work_id(self._record(response).get("works"))

    def _record(self, response: requests.Response) -> dict[str, Any]:
        """A 200 whose body is valid JSON but not an object — an edge cache's
        error page — is a failure, not a record Open Library does not hold."""
        payload = response.json()
        if not isinstance(payload, dict):
            raise ProviderError(self.name, "Open Library answered with no record")
        return payload

    def _get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        try:
            return request_within_origin(
                requests.get,
                url,
                self.display_name,
                fixed_endpoint_refusal,
                params=params,
            )
        except RedirectRefused as refused:
            raise ProviderError(self.name, str(refused)) from None

    def _search_book(self, item: ContentItem) -> EnrichmentResult:
        docs = self._search_docs(item)
        if not docs:
            return EnrichmentResult(match_quality="not_found")

        doc = docs[0]
        work_id = _work_id(doc.get("key"))

        if work_id:
            return self._fetch_work_details(work_id)

        return self._build_result_from_search(doc)

    def _search_docs(self, item: ContentItem) -> list[dict[str, Any]]:
        search_title = clean_title_for_search(item.title)
        log_search_title(logger, item.title, search_title)

        params: dict[str, Any] = {
            "title": search_title,
            "limit": 5,
        }

        if item.author:
            params["author"] = item.author

        try:
            docs = self._request_docs(params)
            if not docs and "author" in params:
                del params["author"]
                docs = self._request_docs(params)
            return docs

        except requests.RequestException as error:
            raise ProviderError(
                self.name,
                f"Failed to search Open Library: {scrub_request_error(error)}",
            ) from error

    def _request_docs(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._get(f"{OPENLIBRARY_API_BASE}/search.json", params)
        response.raise_for_status()
        docs = self._record(response).get("docs")
        if not isinstance(docs, list):
            return []
        return [doc for doc in docs if isinstance(doc, dict)]

    def _fetch_work_details(
        self,
        work_id: str,
        edition: dict[str, Any] | None = None,
    ) -> EnrichmentResult:
        try:
            response = self._get(f"{OPENLIBRARY_API_BASE}/works/{work_id}.json")
            response.raise_for_status()
            work = self._record(response)

            subjects = work.get("subjects", [])
            genres = self._filter_subjects(subjects)

            description = None
            desc_data = work.get("description")
            if isinstance(desc_data, str):
                description = desc_data
            elif isinstance(desc_data, dict):
                description = desc_data.get("value")

            extra_metadata: dict[str, Any] = {}

            if edition:
                if edition.get("number_of_pages"):
                    extra_metadata["pages"] = edition["number_of_pages"]
                if edition.get("publishers"):
                    extra_metadata["publisher"] = edition["publishers"][0]
                if edition.get("publish_date"):
                    extra_metadata["publish_date"] = edition["publish_date"]
                    year = self._extract_year(edition["publish_date"])
                    if year:
                        extra_metadata["year_published"] = year

            if work.get("first_publish_date"):
                first_year = self._extract_year(work["first_publish_date"])
                if first_year and "year_published" not in extra_metadata:
                    extra_metadata["year_published"] = first_year

            # Also set tags from genres for cross-content-type matching
            return EnrichmentResult(
                genres=genres if genres else None,
                tags=genres if genres else None,
                description=description,
                cover_url=_cover_url(work),
                extra_metadata=extra_metadata,
                match_quality="high",
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to fetch work details: {scrub_request_error(error)}"
            ) from error

    def _build_result_from_edition(self, edition: dict[str, Any]) -> EnrichmentResult:
        extra_metadata: dict[str, Any] = {}

        if edition.get("number_of_pages"):
            extra_metadata["pages"] = edition["number_of_pages"]
        if edition.get("publishers"):
            extra_metadata["publisher"] = edition["publishers"][0]
        if edition.get("publish_date"):
            extra_metadata["publish_date"] = edition["publish_date"]
            year = self._extract_year(edition["publish_date"])
            if year:
                extra_metadata["year_published"] = year

        subjects = edition.get("subjects", [])
        genres = self._filter_subjects(subjects)

        return EnrichmentResult(
            genres=genres if genres else None,
            tags=genres if genres else None,
            cover_url=_cover_url(edition),
            extra_metadata=extra_metadata,
            match_quality="medium",
        )

    def _build_result_from_search(self, doc: dict[str, Any]) -> EnrichmentResult:
        extra_metadata: dict[str, Any] = {}

        if doc.get("number_of_pages_median"):
            extra_metadata["pages"] = doc["number_of_pages_median"]
        if doc.get("publisher"):
            extra_metadata["publisher"] = doc["publisher"][0]
        if doc.get("first_publish_year"):
            extra_metadata["year_published"] = doc["first_publish_year"]

        subjects = doc.get("subject", [])
        genres = self._filter_subjects(subjects)

        return EnrichmentResult(
            genres=genres if genres else None,
            tags=genres if genres else None,
            cover_url=_cover_from_id(doc.get("cover_i")),
            extra_metadata=extra_metadata,
            match_quality="medium",
        )

    def _filter_subjects(self, subjects: list[Any]) -> list[str]:
        """Open Library subjects can be very noisy."""
        if not subjects:
            return []

        genre_keywords = {
            "fiction",
            "non-fiction",
            "nonfiction",
            "mystery",
            "thriller",
            "romance",
            "fantasy",
            "science fiction",
            "horror",
            "biography",
            "history",
            "memoir",
            "poetry",
            "drama",
            "comedy",
            "adventure",
            "crime",
            "suspense",
            "historical",
            "literary",
            "young adult",
            "children",
            "classics",
            "philosophy",
            "psychology",
            "self-help",
            "business",
            "travel",
            "cooking",
            "art",
            "music",
            "religion",
            "spirituality",
            "sports",
            "science",
            "technology",
            "politics",
            "economics",
        }

        filtered = []
        seen = set()

        for subject in subjects[:50]:
            if not isinstance(subject, str):
                continue

            normalized = subject.lower().strip()

            if normalized in seen:
                continue
            seen.add(normalized)

            if any(kw in normalized for kw in genre_keywords):
                filtered.append(subject)
            elif len(normalized) < _MAX_SHORT_SUBJECT_LENGTH and " -- " not in subject:
                filtered.append(subject)

            if len(filtered) >= 10:
                break

        return filtered

    def _extract_year(self, date_str: str) -> int | None:
        match = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", date_str)
        if match:
            return int(match.group(1))
        return None
