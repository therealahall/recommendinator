"""Calibre-Web book import plugin (OPDS Atom feed)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# OPDS sub-feeds exposed by Calibre-Web.
# /opds/new (Calibre-Web's feed_new view) paginates the ENTIRE Books table
# ordered newest-first via fill_indexpage: every page carries a rel="next"
# link until the whole library is exhausted, so it is a complete catalog feed
# (not a fixed "recently added" window). Walking its rel="next" chain yields
# every book exactly once.
# /opds/readbooks is the acquisition feed of books on the "Read Books" shelf.
_BOOKS_FEED_PATH = "/opds/new"
_READ_BOOKS_FEED_PATH = "/opds/readbooks"

_REQUEST_TIMEOUT = 30

# Atom / OPDS / Dublin Core / schema.org XML namespaces used in
# Calibre-Web OPDS feeds. Calibre-Web emits series metadata using the
# schema.org vocabulary (``schema:Series`` / ``schema:position``).
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/terms/",
    "opds": "http://opds-spec.org/2010/catalog",
    "schema": "http://schema.org/",
}

# OPDS acquisition link relation prefix for cover/thumbnail images.
_IMAGE_REL = "http://opds-spec.org/image"
_THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"


class CalibreWebPlugin(SourcePlugin):
    """Plugin for importing books from a Calibre-Web instance via OPDS.

    Calibre-Web serves an OPDS Atom catalog at ``/opds``. This plugin walks
    the full acquisition feed, paginating through ``rel="next"`` links, and
    yields one book per OPDS entry. The whole library is imported as backlog
    (UNREAD); books on the Calibre-Web "Read Books" shelf are marked
    COMPLETED.

    Authentication uses HTTP basic auth with the Calibre-Web login
    credentials. The password is stored in the encrypted credential database,
    not in ``config.yaml``.
    """

    @property
    def name(self) -> str:
        return "calibre_web"

    @property
    def display_name(self) -> str:
        return "Calibre-Web"

    @property
    def description(self) -> str:
        return "Import books from a Calibre-Web library"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def requires_network(self) -> bool:
        return True

    @classmethod
    def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Strip and normalise Calibre-Web YAML config."""
        return {
            "url": (raw_config.get("url") or "").strip().rstrip("/"),
            "username": (raw_config.get("username") or "").strip(),
            "password": (raw_config.get("password") or "").strip(),
            "verify_ssl": raw_config.get("verify_ssl", True),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                description="Calibre-Web base URL (e.g. http://localhost:8083)",
            ),
            ConfigField(
                name="username",
                field_type=str,
                required=True,
                description="Calibre-Web login username",
            ),
            ConfigField(
                name="password",
                field_type=str,
                required=True,
                sensitive=True,
                description="Calibre-Web login password",
            ),
            ConfigField(
                name="verify_ssl",
                field_type=bool,
                required=False,
                default=True,
                description="Verify the TLS certificate (disable for self-signed)",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []

        if not (config.get("url") or "").strip():
            errors.append("'url' is required")
        if not (config.get("username") or "").strip():
            errors.append("'username' is required")

        if not (config.get("password") or "").strip():
            # The password may live in the encrypted credential store rather
            # than config.yaml; only flag it if it is missing from both.
            source_id = config.get("_source_id", self.name)
            stored_password = ""
            if storage is not None:
                db_creds = storage.get_credentials_for_source(user_id, source_id)
                # get_credentials_for_source returns dict[str, str] today, but
                # guard None defensively so a stub/alt store can't AttributeError.
                stored_password = ((db_creds or {}).get("password") or "").strip()
            if not stored_password:
                errors.append("'password' is required")

        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch books from a Calibre-Web library via OPDS.

        Args:
            config: Must contain 'url', 'username', 'password'. Optional
                'verify_ssl' (default True).
            progress_callback: Optional callback for progress updates.

        Yields:
            ContentItem for each book in the library.

        Raises:
            SourceError: On network failure, auth failure, or malformed XML.
        """
        # Re-normalize defensively: callers may pass raw (untransformed) config.
        base_url = (config.get("url") or "").strip().rstrip("/")
        username = (config.get("username") or "").strip()
        password = (config.get("password") or "").strip()
        verify_ssl = config.get("verify_ssl", True)
        auth = (username, password)
        source = self.get_source_identifier(config)

        read_ids = self._fetch_read_book_ids(base_url, auth, verify_ssl)

        processed = 0
        feed_url: str | None = urljoin(base_url + "/", _BOOKS_FEED_PATH.lstrip("/"))
        while feed_url:
            root = self._get_feed(feed_url, auth, verify_ssl)

            entries = root.findall("atom:entry", _NS)
            for entry in entries:
                item = self._parse_entry(entry, read_ids, source)
                if item is None:
                    continue
                processed += 1
                if progress_callback:
                    progress_callback(processed, None, item.title)
                yield item

            feed_url = _resolve_next_url(feed_url, _find_next_link(root), base_url)

        logger.info("Imported %d books from Calibre-Web", processed)

    def _fetch_read_book_ids(
        self,
        base_url: str,
        auth: tuple[str, str],
        verify_ssl: bool,
    ) -> set[str]:
        """Fetch the set of external ids on the Calibre-Web "Read Books" shelf.

        Walks the read-books acquisition feed (following pagination) and
        collects the namespaced external id of each entry. Returns an empty
        set if the instance does not expose the shelf, so books default to
        UNREAD rather than being guessed COMPLETED.

        Args:
            base_url: Calibre-Web base URL (no trailing slash).
            auth: (username, password) tuple for basic auth.
            verify_ssl: Whether to verify the TLS certificate.

        Returns:
            Set of namespaced external ids that are marked read.
        """
        read_ids: set[str] = set()
        feed_url: str | None = urljoin(
            base_url + "/", _READ_BOOKS_FEED_PATH.lstrip("/")
        )
        is_first_page = True
        while feed_url:
            try:
                root = self._get_feed(feed_url, auth, verify_ssl)
            except SourceError:
                if is_first_page:
                    # The shelf simply doesn't exist on this instance; default
                    # everything to unread rather than guessing COMPLETED.
                    logger.info(
                        "Calibre-Web read-books shelf unavailable; "
                        "defaulting all books to unread"
                    )
                    return set()
                # A later page failed after we already collected read ids.
                # Keep what we have so books confirmed read on earlier pages
                # are still marked COMPLETED (a first sync persists nothing, so
                # discarding them would yield them UNREAD with no way back).
                logger.warning(
                    "Calibre-Web read-books pagination failed after %d ids; "
                    "read status may be incomplete for this sync",
                    len(read_ids),
                )
                return read_ids

            for entry in root.findall("atom:entry", _NS):
                external_id = _build_external_id(entry)
                if external_id:
                    read_ids.add(external_id)

            feed_url = _resolve_next_url(feed_url, _find_next_link(root), base_url)
            is_first_page = False

        return read_ids

    def _get_feed(
        self,
        url: str,
        auth: tuple[str, str],
        verify_ssl: bool,
    ) -> ElementTree.Element:
        """Fetch and parse a single OPDS feed page.

        Args:
            url: Absolute feed URL.
            auth: (username, password) tuple for basic auth.
            verify_ssl: Whether to verify the TLS certificate.

        Returns:
            Parsed root XML element of the feed.

        Raises:
            SourceError: On network failure, auth failure, or malformed XML.
        """
        try:
            response = requests.get(
                url, auth=auth, timeout=_REQUEST_TIMEOUT, verify=verify_ssl
            )
        except requests.RequestException as error:
            raise SourceError(
                self.name, f"Failed to connect to Calibre-Web at {url}: {error}"
            ) from error

        if response.status_code == 401:
            raise SourceError(
                self.name,
                "Authentication failed (401). Check the Calibre-Web "
                "username and password.",
            )

        try:
            response.raise_for_status()
        except requests.RequestException as error:
            raise SourceError(
                self.name, f"Calibre-Web returned an error for {url}: {error}"
            ) from error

        try:
            return _parse_opds_xml(response.content)
        except ElementTree.ParseError as error:
            raise SourceError(
                self.name, f"Failed to parse OPDS feed from {url}: {error}"
            ) from error

    def _parse_entry(
        self,
        entry: ElementTree.Element,
        read_ids: set[str],
        source: str,
    ) -> ContentItem | None:
        """Build a ContentItem from a single OPDS entry.

        Args:
            entry: OPDS ``<entry>`` element.
            read_ids: Set of external ids on the read-books shelf.
            source: Source identifier for the ContentItem.

        Returns:
            A ContentItem, or None if the entry has no usable title.
        """
        title = _text(entry.find("atom:title", _NS))
        if not title:
            return None

        external_id = _build_external_id(entry)

        author = _text(entry.find("atom:author/atom:name", _NS))

        status = (
            ConsumptionStatus.COMPLETED
            if external_id and external_id in read_ids
            else ConsumptionStatus.UNREAD
        )

        rating = self.normalize_rating(_parse_rating(entry))

        metadata = _build_metadata(entry)

        return ContentItem(
            id=external_id,
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            rating=rating,
            status=status,
            metadata=metadata,
            source=source,
        )


class _NoDoctypeTreeBuilder(ElementTree.TreeBuilder):
    """TreeBuilder that refuses any DOCTYPE declaration.

    Custom internal entities (billion-laughs) and external DTDs (XXE) both
    require a DOCTYPE. OPDS feeds never carry one, so rejecting it neutralises
    both attack classes without a third-party dependency.
    """

    def doctype(self, name: str, pubid: str, system: str) -> None:
        raise ElementTree.ParseError("DOCTYPE declarations are not allowed")


def _parse_opds_xml(content: bytes) -> ElementTree.Element:
    """Parse OPDS Atom XML with XXE and billion-laughs protection.

    Args:
        content: Raw XML bytes from the OPDS response.

    Returns:
        Parsed root XML element.

    Raises:
        ElementTree.ParseError: If the XML is malformed or contains a DOCTYPE.
    """
    parser = ElementTree.XMLParser(target=_NoDoctypeTreeBuilder())
    parser.feed(content)
    return parser.close()


def _text(element: ElementTree.Element | None) -> str | None:
    """Return stripped element text, or None if absent/empty."""
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def _build_external_id(entry: ElementTree.Element) -> str | None:
    """Build a stable namespaced external id from an OPDS entry.

    Calibre-Web entry ids look like ``urn:uuid:<uuid>`` or ``urn:calibre:<id>``.
    We strip the ``urn:`` / ``urn:uuid:`` prefix and namespace the result as
    ``calibre:<id>`` so it is stable across syncs and unique to this source.

    Args:
        entry: OPDS ``<entry>`` element.

    Returns:
        Namespaced external id (e.g. ``calibre:<uuid>``) or None.
    """
    raw_id = _text(entry.find("atom:id", _NS))
    if not raw_id:
        return None

    identifier = raw_id
    for prefix in ("urn:uuid:", "urn:calibre:", "urn:"):
        if identifier.startswith(prefix):
            identifier = identifier[len(prefix) :]
            break

    identifier = identifier.strip()
    if not identifier:
        return None
    return f"calibre:{identifier}"


# A ``<category>`` is only treated as a rating when its ``scheme`` positively
# identifies it as one. Calibre-Web tags carry content schemes (e.g. BISAC)
# rather than a rating scheme, so a bare numeric label such as a publication
# year is never mistaken for a star count.
_RATING_SCHEME_MARKER = "rating"


def _parse_rating(entry: ElementTree.Element) -> int | None:
    """Extract a 0-5 star rating from an OPDS entry.

    A rating is only derived from a genuine ratings signal:

    - a ``<rating>`` element, or
    - a ``<category>`` whose ``scheme`` identifies it as a rating (its scheme
      contains ``"rating"``).

    Bare numeric ``<category>`` labels without a rating scheme (e.g. a
    publication-year facet like ``2008``) are NOT treated as ratings; they are
    preserved as tags by :func:`_parse_tags`. Calibre stores ratings on a 0-10
    scale (steps of 2 = 0-5 stars), so values above 5 are halved.

    Args:
        entry: OPDS ``<entry>`` element.

    Returns:
        The raw 0-5 star integer (including ``0`` for an explicit zero-star
        rating), or None only when no rating signal is present or the value is
        non-numeric. The ``0`` -> "no rating" conversion is finalized later by
        :meth:`SourcePlugin.normalize_rating`, not here.
    """
    raw = _text(entry.find("atom:rating", _NS))
    if raw is None:
        for category in entry.findall("atom:category", _NS):
            if not _is_rating_category(category):
                continue
            label = category.get("label") or category.get("term")
            if label and label.strip():
                raw = label.strip()
                break

    if raw is None:
        return None

    try:
        value = float(raw)
    except ValueError:
        return None

    # Calibre's native 0-10 scale (steps of 2) maps to 0-5 stars.
    if value > 5:
        value = value / 2
    return int(round(value))


def _is_rating_category(category: ElementTree.Element) -> bool:
    """Return True if a ``<category>`` is a Calibre-Web rating category."""
    scheme = category.get("scheme") or ""
    return _RATING_SCHEME_MARKER in scheme.lower()


def _build_metadata(entry: ElementTree.Element) -> dict[str, Any]:
    """Build the metadata dict for a book from its OPDS entry.

    Populates only the fields the entry actually provides: isbn, series and
    series index, tags/categories, language, publisher, published date,
    cover/thumbnail url, and summary.

    Args:
        entry: OPDS ``<entry>`` element.

    Returns:
        Metadata dictionary (omitting absent fields).
    """
    metadata: dict[str, Any] = {}

    summary = _text(entry.find("atom:summary", _NS)) or _text(
        entry.find("atom:content", _NS)
    )
    if summary:
        metadata["summary"] = summary

    publisher = _text(entry.find("dc:publisher", _NS))
    if publisher:
        metadata["publisher"] = publisher

    language = _text(entry.find("dc:language", _NS))
    if language:
        metadata["language"] = language

    published = _text(entry.find("atom:published", _NS)) or _text(
        entry.find("dc:issued", _NS)
    )
    if published:
        metadata["published"] = published

    isbn = _parse_isbn(entry)
    if isbn:
        metadata["isbn"] = isbn

    series, series_index = _parse_series(entry)
    if series:
        metadata["series"] = series
    if series_index is not None:
        metadata["series_index"] = series_index

    tags = _parse_tags(entry)
    if tags:
        metadata["tags"] = tags

    cover_url = _find_link_href(entry, _IMAGE_REL)
    if cover_url:
        metadata["cover_url"] = cover_url
    thumbnail_url = _find_link_href(entry, _THUMBNAIL_REL)
    if thumbnail_url:
        metadata["thumbnail_url"] = thumbnail_url

    return metadata


def _parse_isbn(entry: ElementTree.Element) -> str | None:
    """Extract an ISBN from an OPDS entry's dc:identifier elements."""
    for identifier in entry.findall("dc:identifier", _NS):
        value = _text(identifier)
        if value and value.lower().startswith("isbn:"):
            return value.split(":", 1)[1].strip() or None
    return None


def _parse_series(entry: ElementTree.Element) -> tuple[str | None, float | None]:
    """Extract series name and index from an OPDS entry.

    Calibre-Web emits series metadata using the schema.org vocabulary as a
    ``<schema:Series>`` element whose name and position live in
    ``schema:name`` / ``schema:position`` attributes (the position may instead
    appear as a child element on some versions)::

        <schema:Series schema:name="Middle-earth" schema:position="1"/>

    A bare ``<series>`` / ``<series_index>`` child pair is read as a harmless
    fallback for non-standard feeds.

    Args:
        entry: OPDS ``<entry>`` element.

    Returns:
        Tuple of (series name or None, series index or None).
    """
    series_el = entry.find("schema:Series", _NS)
    if series_el is not None:
        name_attr = "{http://schema.org/}name"
        position_attr = "{http://schema.org/}position"
        series = (series_el.get(name_attr) or "").strip() or None
        index_raw = series_el.get(position_attr)
        if index_raw is None:
            index_raw = _text(series_el.find("schema:position", _NS))
        return series, _to_index(index_raw)

    series = _text(entry.find("atom:series", _NS))
    index_raw = _text(entry.find("atom:series_index", _NS))
    return series, _to_index(index_raw)


def _to_index(index_raw: str | None) -> float | None:
    """Parse a series index string into a float, or None if absent/invalid."""
    if index_raw is None:
        return None
    try:
        return float(index_raw.strip())
    except ValueError:
        return None


def _parse_tags(entry: ElementTree.Element) -> list[str]:
    """Extract category labels as tags.

    Every ``<category>`` becomes a tag except rating categories (identified by
    scheme), whose label is a star count rather than a genuine tag. Numeric
    labels with no rating scheme (e.g. a publication-year facet like ``2008``)
    are preserved here so they are never silently dropped.
    """
    tags: list[str] = []
    for category in entry.findall("atom:category", _NS):
        if _is_rating_category(category):
            continue
        label = category.get("label") or category.get("term")
        if label:
            cleaned = label.strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
    return tags


def _find_link_href(entry: ElementTree.Element, rel: str) -> str | None:
    """Return the href of the first ``<link>`` with the given rel."""
    for link in entry.findall("atom:link", _NS):
        if link.get("rel") == rel:
            href = link.get("href")
            return href.strip() if href else None
    return None


def _find_next_link(root: ElementTree.Element) -> str | None:
    """Return the href of the feed-level ``rel="next"`` link, if any."""
    for link in root.findall("atom:link", _NS):
        if link.get("rel") == "next":
            href = link.get("href")
            return href.strip() if href else None
    return None


def _resolve_next_url(
    current_url: str, next_href: str | None, base_url: str
) -> str | None:
    """Resolve a ``rel="next"`` href, refusing to leave the configured host.

    The next-page URL is fetched with the user's basic-auth credentials, so a
    malicious or compromised feed could point ``rel="next"`` at an internal
    service (cloud metadata, localhost, etc.) to exfiltrate those credentials
    or perform SSRF. A same-host link that downgrades the scheme (HTTPS->HTTP)
    is just as dangerous: it would leak those credentials over plaintext. We
    resolve the href relative to the current page, then require BOTH its host
    and its scheme to match the configured ``base_url``; otherwise we log a
    warning and stop paginating (treat it as no next link).

    Args:
        current_url: Absolute URL of the page the link was found on.
        next_href: Raw ``rel="next"`` href, or None when absent.
        base_url: Configured Calibre-Web base URL.

    Returns:
        The resolved absolute next URL, or None to stop paginating.
    """
    if not next_href:
        return None
    next_parts = urlparse(urljoin(current_url, next_href))
    base_parts = urlparse(base_url)
    if next_parts.netloc != base_parts.netloc or next_parts.scheme != base_parts.scheme:
        logger.warning(
            "Refusing to follow Calibre-Web rel=next link to a different "
            "origin (%s://%s); stopping pagination",
            next_parts.scheme,
            next_parts.netloc,
        )
        return None
    return next_parts.geturl()
