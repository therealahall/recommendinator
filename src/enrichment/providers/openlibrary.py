"""OpenLibrary enrichment provider for books.

Open Library provides comprehensive book metadata without requiring
an API key.
"""

import logging
import re
from typing import Any

import requests

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
)
from src.models.content import ContentItem, ContentType

logger = logging.getLogger(__name__)

# Open Library API base URL
OPENLIBRARY_API_BASE = "https://openlibrary.org"

# Pattern to match series info in titles like "(Series Name, #1)" or "(Series Name #1)"
SERIES_PATTERN = re.compile(r"\s*\([^)]*#\d+[^)]*\)\s*$")

# Maximum character length for a subject string to be accepted as a genre
# without requiring a keyword match. Keeps broad categories ("mystery")
# while filtering verbose library subject headings.
_MAX_SHORT_SUBJECT_LENGTH = 25


def clean_title_for_search(title: str) -> str:
    """Remove series info from title for better search matching.

    Examples:
        "For We Are Many (Bobiverse, #2)" -> "For We Are Many"
        "The Name of the Wind (The Kingkiller Chronicle #1)" -> "The Name of the Wind"

    Args:
        title: Original book title

    Returns:
        Cleaned title without series info
    """
    cleaned = SERIES_PATTERN.sub("", title).strip()
    return cleaned if cleaned else title


class OpenLibraryProvider(EnrichmentProvider):
    """Enrichment provider using Open Library API.

    Enriches books with:
    - Genres (from subjects)
    - Description
    - Additional metadata (publishers, publish year, page count)

    No API key is required.

    Matching strategy:
    1. ISBN lookup if available
    2. Title + author search
    3. Title-only search as fallback
    """

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
        # Be polite - 1 request per second
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
        # No required config for Open Library
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        """Enrich a book with Open Library metadata.

        Args:
            item: ContentItem to enrich (must be BOOK)
            config: Provider configuration

        Returns:
            EnrichmentResult with metadata, or None if not found

        Raises:
            ProviderError: If API request fails
        """
        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )

        if content_type != ContentType.BOOK:
            logger.warning(f"OpenLibrary provider does not support {content_type}")
            return None

        # Try ISBN lookup first
        metadata = item.metadata or {}
        isbn = metadata.get("isbn13") or metadata.get("isbn")

        if isbn:
            result = self._lookup_by_isbn(isbn)
            if result and result.match_quality != "not_found":
                return result

        # Fall back to title + author search
        return self._search_book(item)

    def _lookup_by_isbn(self, isbn: str) -> EnrichmentResult | None:
        """Look up a book by ISBN.

        Args:
            isbn: ISBN-10 or ISBN-13

        Returns:
            EnrichmentResult or None if not found
        """
        # Clean ISBN (remove hyphens)
        clean_isbn = isbn.replace("-", "").strip()

        try:
            # First get the edition info
            response = requests.get(
                f"{OPENLIBRARY_API_BASE}/isbn/{clean_isbn}.json",
                timeout=10,
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            edition = response.json()

            # Get the work key for more details
            works = edition.get("works", [])
            if works:
                work_key = works[0].get("key")
                if work_key:
                    return self._fetch_work_details(work_key, edition)

            # No work reference, use edition data
            return self._build_result_from_edition(edition)

        except requests.RequestException as error:
            logger.warning(f"ISBN lookup failed for {isbn}: {error}")
            return None

    def _search_book(self, item: ContentItem) -> EnrichmentResult:
        """Search for a book by title and author.

        Args:
            item: ContentItem with title and optional author

        Returns:
            EnrichmentResult
        """
        # Clean title to remove series info like "(Bobiverse, #2)"
        search_title = clean_title_for_search(item.title)
        if search_title != item.title:
            logger.debug(
                f"Cleaned title for search: '{item.title}' -> '{search_title}'"
            )

        params: dict[str, Any] = {
            "title": search_title,
            "limit": 5,
        }

        # Add author if available
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
                # Try without author if we had one
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

            # Use the first result
            doc = docs[0]
            work_key = doc.get("key")

            if work_key:
                # Fetch full work details
                return self._fetch_work_details(work_key)

            # Build result from search data
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
        """Fetch detailed work information.

        Args:
            work_key: Open Library work key (e.g., "/works/OL123W")
            edition: Optional edition data for additional metadata

        Returns:
            EnrichmentResult
        """
        try:
            response = requests.get(
                f"{OPENLIBRARY_API_BASE}{work_key}.json",
                timeout=10,
            )
            response.raise_for_status()
            work = response.json()

            # Extract subjects as genres (limit to most relevant)
            subjects = work.get("subjects", [])
            genres = self._filter_subjects(subjects)

            # Get description
            description = None
            desc_data = work.get("description")
            if isinstance(desc_data, str):
                description = desc_data
            elif isinstance(desc_data, dict):
                description = desc_data.get("value")

            # Build extra metadata
            extra_metadata: dict[str, Any] = {}

            if edition:
                # Add edition-specific data
                if edition.get("number_of_pages"):
                    extra_metadata["pages"] = edition["number_of_pages"]
                if edition.get("publishers"):
                    extra_metadata["publisher"] = edition["publishers"][0]
                if edition.get("publish_date"):
                    extra_metadata["publish_date"] = edition["publish_date"]
                    # Try to extract year
                    year = self._extract_year(edition["publish_date"])
                    if year:
                        extra_metadata["year_published"] = year

            # Extract work-level metadata
            if work.get("first_publish_date"):
                first_year = self._extract_year(work["first_publish_date"])
                if first_year and "year_published" not in extra_metadata:
                    extra_metadata["year_published"] = first_year

            # Work ID
            work_id = work_key.split("/")[-1] if work_key else None

            # Also set tags from genres for cross-content-type matching
            # This allows book genres like "Science Fiction" to match with
            # movie/game tags and genres
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
        """Build result from edition data only.

        Args:
            edition: Edition API response

        Returns:
            EnrichmentResult
        """
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

        # Subjects from edition
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
        """Build result from search result data.

        Args:
            doc: Search result document

        Returns:
            EnrichmentResult
        """
        extra_metadata: dict[str, Any] = {}

        if doc.get("number_of_pages_median"):
            extra_metadata["pages"] = doc["number_of_pages_median"]
        if doc.get("publisher"):
            extra_metadata["publisher"] = doc["publisher"][0]
        if doc.get("first_publish_year"):
            extra_metadata["year_published"] = doc["first_publish_year"]

        # Subjects from search
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
        """Filter and clean subject list to get genres.

        Open Library subjects can be very noisy. This filters to get
        the most useful genre-like categories.

        Args:
            subjects: List of subject strings

        Returns:
            Filtered list of genres (max 10)
        """
        if not subjects:
            return []

        # Common genre categories to keep
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

        for subject in subjects[:50]:  # Limit input
            if not isinstance(subject, str):
                continue

            # Normalize
            normalized = subject.lower().strip()

            # Skip duplicates
            if normalized in seen:
                continue
            seen.add(normalized)

            # Keep if it matches a genre keyword or is short enough
            if any(kw in normalized for kw in genre_keywords):
                filtered.append(subject)
            elif len(normalized) < _MAX_SHORT_SUBJECT_LENGTH and " -- " not in subject:
                # Short subjects without subdivisions
                filtered.append(subject)

            if len(filtered) >= 10:
                break

        return filtered

    def _extract_year(self, date_str: str) -> int | None:
        """Extract year from a date string.

        Args:
            date_str: Date string in various formats

        Returns:
            Year as integer, or None if not found
        """
        # Try to find a 4-digit year
        match = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", date_str)
        if match:
            return int(match.group(1))
        return None
