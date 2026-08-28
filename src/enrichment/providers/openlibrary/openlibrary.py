import logging
import re
from typing import Any

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    log_search_title,
)
from src.models.content import ContentItem, ContentType
from src.utils.request_errors import scrub_request_error
from src.utils.text import sanitize_for_log

logger = logging.getLogger(__name__)

OPENLIBRARY_API_BASE = "https://openlibrary.org"

# Pattern to match series info in titles like "(Series Name, #1)" or "(Series Name #1)"
SERIES_PATTERN = re.compile(r"\s*\([^)]*#\d+[^)]*\)\s*$")

# Keeps broad categories ("mystery") while filtering verbose library subject headings.
_MAX_SHORT_SUBJECT_LENGTH = 25


def clean_title_for_search(title: str) -> str:
    cleaned = SERIES_PATTERN.sub("", title).strip()
    return cleaned if cleaned else title


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
    def rate_limit_requests_per_second(self) -> float:
        return 1.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="enabled",
                field_type=bool,
                required=False,
                default=False,
                description="Enable Open Library enrichment",
            ),
        ]

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

        metadata = item.metadata or {}
        isbn = metadata.get("isbn13") or metadata.get("isbn")

        if isbn:
            result = self._lookup_by_isbn(isbn)
            if result and result.match_quality != "not_found":
                return result

        return self._search_book(item)

    def _lookup_by_isbn(self, isbn: str) -> EnrichmentResult | None:
        clean_isbn = isbn.replace("-", "").strip()

        try:
            response = requests.get(
                f"{OPENLIBRARY_API_BASE}/isbn/{clean_isbn}.json",
                timeout=10,
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            edition = response.json()

            works = edition.get("works", [])
            if works:
                work_key = works[0].get("key")
                if work_key:
                    return self._fetch_work_details(work_key, edition)

            return self._build_result_from_edition(edition)

        except requests.RequestException as error:
            # The isbn is an imported metadata column, and the error embeds the
            # URL built from it, so neither reaches the log as written.
            logger.warning(
                "ISBN lookup failed for %s: %s",
                sanitize_for_log(isbn),
                scrub_request_error(error),
            )
            return None

    def _search_book(self, item: ContentItem) -> EnrichmentResult:
        search_title = clean_title_for_search(item.title)
        log_search_title(logger, item.title, search_title)

        params: dict[str, Any] = {
            "title": search_title,
            "limit": 5,
        }

        if item.author:
            params["author"] = item.author

        try:
            response = requests.get(
                f"{OPENLIBRARY_API_BASE}/search.json",
                params=params,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            docs = data.get("docs", [])
            if not docs:
                if item.author and "author" in params:
                    del params["author"]
                    response = requests.get(
                        f"{OPENLIBRARY_API_BASE}/search.json",
                        params=params,
                        timeout=15,
                    )
                    response.raise_for_status()
                    data = response.json()
                    docs = data.get("docs", [])

            if not docs:
                return EnrichmentResult(
                    match_quality="not_found",
                    provider=self.name,
                )

            doc = docs[0]
            work_key = doc.get("key")

            if work_key:
                return self._fetch_work_details(work_key)

            return self._build_result_from_search(doc)

        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to search Open Library: {error}"
            ) from error

    def _fetch_work_details(
        self,
        work_key: str,
        edition: dict[str, Any] | None = None,
    ) -> EnrichmentResult:
        try:
            response = requests.get(
                f"{OPENLIBRARY_API_BASE}{work_key}.json",
                timeout=10,
            )
            response.raise_for_status()
            work = response.json()

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

            work_id = work_key.split("/")[-1] if work_key else None

            # Also set tags from genres for cross-content-type matching
            return EnrichmentResult(
                external_id=f"openlibrary:{work_id}" if work_id else None,
                genres=genres if genres else None,
                tags=genres if genres else None,
                description=description,
                extra_metadata=extra_metadata,
                match_quality="high",
                provider=self.name,
            )

        except requests.RequestException as error:
            raise ProviderError(
                self.name, f"Failed to fetch work details: {error}"
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

        edition_key = edition.get("key", "").split("/")[-1]

        return EnrichmentResult(
            external_id=f"openlibrary:{edition_key}" if edition_key else None,
            genres=genres if genres else None,
            tags=genres if genres else None,
            extra_metadata=extra_metadata,
            match_quality="medium",
            provider=self.name,
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

        work_key = doc.get("key", "").split("/")[-1]

        return EnrichmentResult(
            external_id=f"openlibrary:{work_key}" if work_key else None,
            genres=genres if genres else None,
            tags=genres if genres else None,
            extra_metadata=extra_metadata,
            match_quality="medium",
            provider=self.name,
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
