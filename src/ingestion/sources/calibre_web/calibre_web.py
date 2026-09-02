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
from src.ingestion.urls import source_url_error
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# /opds/new (Calibre-Web's feed_new view) paginates the ENTIRE Books table
# ordered newest-first via fill_indexpage: every page carries a rel="next"
# link until the whole library is exhausted, so it is a complete catalog feed
# (not a fixed "recently added" window).
_BOOKS_FEED_PATH = "/opds/new"
# /opds/readbooks is the acquisition feed of books on the "Read Books" shelf.
_READ_BOOKS_FEED_PATH = "/opds/readbooks"

_REQUEST_TIMEOUT = 30

# Calibre-Web emits series metadata using the schema.org vocabulary
# (``schema:Series`` / ``schema:position``).
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/terms/",
    "opds": "http://opds-spec.org/2010/catalog",
    "schema": "http://schema.org/",
}

_IMAGE_REL = "http://opds-spec.org/image"


class CalibreWebPlugin(SourcePlugin):
    """The whole library is imported as backlog (UNREAD); books on the Calibre-Web
    "Read Books" shelf are marked COMPLETED.
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
    def transform_fields(cls, raw_fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": (raw_fields.get("url") or "").strip().rstrip("/"),
            "username": (raw_fields.get("username") or "").strip(),
            "password": (raw_fields.get("password") or "").strip(),
            "verify_ssl": raw_fields.get("verify_ssl", True),
        }

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="url",
                field_type=str,
                required=True,
                credential_bound=True,
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
                description=(
                    "Verify the TLS certificate (disable for a self-signed "
                    "certificate or a private CA)"
                ),
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []

        url = (config.get("url") or "").strip()
        if not url:
            errors.append("'url' is required")
        else:
            url_error = source_url_error(url)
            if url_error is not None:
                errors.append(url_error)
        if not (config.get("username") or "").strip():
            errors.append("'username' is required")

        if not (config.get("password") or "").strip():
            # The password may live in the encrypted credential store rather
            # than config.yaml; only flag it if it is missing from both.
            source_id = config.get("_source_id", self.name)
            stored_password = ""
            if storage is not None:
                db_creds = storage.credentials.get_for_source(user_id, source_id)
                # get_for_source returns dict[str, str] today, but
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
        # Re-normalize defensively: callers may pass raw (untransformed) config.
        base_url = (config.get("url") or "").strip().rstrip("/")
        username = (config.get("username") or "").strip()
        password = (config.get("password") or "").strip()
        verify_ssl = config.get("verify_ssl", True)
        auth = (username, password)

        # A scheduled sync skips validate_config, so the basic-auth
        # password would otherwise reach whatever host the config now names.
        url_error = source_url_error(base_url)
        if url_error is not None:
            raise SourceError(self.name, url_error)

        read_ids = self._fetch_read_book_ids(base_url, auth, verify_ssl)

        processed = 0
        feed_url: str | None = urljoin(base_url + "/", _BOOKS_FEED_PATH.lstrip("/"))
        while feed_url:
            root = self._get_feed(feed_url, auth, verify_ssl)

            entries = root.findall("atom:entry", _NS)
            for entry in entries:
                item = self._parse_entry(entry, read_ids, base_url, feed_url)
                if item is None:
                    continue
                processed += 1
                if progress_callback:
                    progress_callback(processed, None, item.title)
                yield item

            feed_url = _resolve_feed_url(
                feed_url, _find_next_link(root), base_url, "rel=next"
            )

        logger.info("Imported %d books from Calibre-Web", processed)

    def _fetch_read_book_ids(
        self,
        base_url: str,
        auth: tuple[str, str],
        verify_ssl: bool,
    ) -> set[str]:
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

            feed_url = _resolve_feed_url(
                feed_url, _find_next_link(root), base_url, "rel=next"
            )
            is_first_page = False

        return read_ids

    def _get_feed(
        self,
        url: str,
        auth: tuple[str, str],
        verify_ssl: bool,
    ) -> ElementTree.Element:
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
        base_url: str,
        feed_url: str,
    ) -> ContentItem | None:
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

        metadata = _build_metadata(entry)

        return ContentItem(
            id=external_id,
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            # Calibre's star rating is a community average, not the user's own
            # rating, so it is never imported; ratings are left for the user.
            rating=None,
            status=status,
            cover_url=_resolve_feed_url(
                feed_url, _find_link_href(entry, _IMAGE_REL), base_url, "cover"
            ),
            metadata=metadata,
        )


class _NoDoctypeTreeBuilder(ElementTree.TreeBuilder):
    """Custom internal entities (billion-laughs) and external DTDs (XXE) both
    require a DOCTYPE.
    """

    def doctype(self, name: str, pubid: str, system: str) -> None:
        raise ElementTree.ParseError("DOCTYPE declarations are not allowed")


def _parse_opds_xml(content: bytes) -> ElementTree.Element:
    parser = ElementTree.XMLParser(target=_NoDoctypeTreeBuilder())
    parser.feed(content)
    return parser.close()


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def _build_external_id(entry: ElementTree.Element) -> str | None:
    """Calibre-Web entry ids look like ``urn:uuid:<uuid>`` or ``urn:calibre:<id>``."""
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


# A ``<category>`` is identified as a Calibre-Web rating only when its
# ``scheme`` positively marks it as one. Such categories carry a star count as
# their label, not a genuine tag, so :func:`_parse_tags` excludes them.
_RATING_SCHEME_MARKER = "rating"


def _is_rating_category(category: ElementTree.Element) -> bool:
    scheme = category.get("scheme") or ""
    return _RATING_SCHEME_MARKER in scheme.lower()


def _build_metadata(entry: ElementTree.Element) -> dict[str, Any]:
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

    return metadata


def _parse_isbn(entry: ElementTree.Element) -> str | None:
    for identifier in entry.findall("dc:identifier", _NS):
        value = _text(identifier)
        if value and value.lower().startswith("isbn:"):
            return value.split(":", 1)[1].strip() or None
    return None


def _parse_series(entry: ElementTree.Element) -> tuple[str | None, float | None]:
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
    if index_raw is None:
        return None
    try:
        return float(index_raw.strip())
    except ValueError:
        return None


def _parse_tags(entry: ElementTree.Element) -> list[str]:
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
    for link in entry.findall("atom:link", _NS):
        if link.get("rel") == rel:
            href = link.get("href")
            return href.strip() if href else None
    return None


def _find_next_link(root: ElementTree.Element) -> str | None:
    for link in root.findall("atom:link", _NS):
        if link.get("rel") == "next":
            href = link.get("href")
            return href.strip() if href else None
    return None


def _resolve_feed_url(
    document_url: str, href: str | None, base_url: str, kind: str
) -> str | None:
    """An off-origin href is refused whatever it is for: the next page is fetched
    with the user's basic-auth credentials (SSRF onto cloud metadata, localhost),
    and a cover lands on a fill-only column no later source can replace.
    """
    if not href:
        return None
    parts = urlparse(urljoin(document_url, href))
    base_parts = urlparse(base_url)
    if parts.netloc != base_parts.netloc or parts.scheme != base_parts.scheme:
        logger.warning(
            "Refusing a Calibre-Web %s link to a different origin (%s://%s)",
            kind,
            sanitize_for_log(parts.scheme),
            sanitize_for_log(parts.netloc),
        )
        return None
    return parts.geturl()
