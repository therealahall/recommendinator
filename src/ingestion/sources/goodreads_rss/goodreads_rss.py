"""Goodreads public-shelf sync via RSS.

Goodreads exposes every *public* profile's shelves as RSS feeds at
``https://www.goodreads.com/review/list/<user_id>.rss?shelf=<shelf>``. This
plugin fetches one feed per requested shelf, paginates through all results,
and maps each ``<item>`` to a :class:`ContentItem`. The ``metadata`` dict
shares ``book_id``/``isbn``/``pages``/``year_published`` with the sibling
:mod:`goodreads_csv` plugin and additionally carries ``average_rating``,
``description``, and ``shelf``. RSS cannot supply ``isbn13`` or ``publisher``,
so those keys are absent.

Unlike the CSV plugin there is no manual export step — the profile just has to
be public. The three default shelves (``read``, ``currently-reading``,
``to-read``) are mutually exclusive, but users may add custom shelves that
overlap them, so :meth:`GoodreadsRssPlugin.fetch` deduplicates within a single
run and keeps the strongest consumption status
(``completed`` > ``currently_consuming`` > ``unread``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as ET
import requests
from defusedxml.common import DefusedXmlException

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.request_errors import scrub_request_error

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Base host for Goodreads RSS feeds.
GOODREADS_BASE = "https://www.goodreads.com"

# Shelves synced when the user does not override the ``shelves`` config field.
DEFAULT_SHELVES = ["read", "currently-reading", "to-read"]

# Goodreads paginates RSS feeds; request the max page size and walk pages
# until one comes back empty.
PER_PAGE = 100

# Hard ceiling on pages walked per shelf. At PER_PAGE=100 this covers 50,000
# books on a single shelf; hitting it means a broken feed or a redirect loop,
# so we fail loudly instead of paginating forever.
MAX_PAGES = 500

# Per-request timeout in seconds.
REQUEST_TIMEOUT = 30

# Consumption-status precedence for cross-shelf deduplication. A book that
# appears on several requested shelves is yielded once with the strongest
# status.
_STATUS_RANK = {
    ConsumptionStatus.UNREAD.value: 0,
    ConsumptionStatus.CURRENTLY_CONSUMING.value: 1,
    ConsumptionStatus.COMPLETED.value: 2,
}


class GoodreadsRssError(SourceError):
    """Exception raised when the Goodreads RSS source fails.

    Subclasses :class:`SourceError` so the ingestion pipeline handles it like
    any other source failure. Messages are scrubbed of the request URL to
    avoid leaking the user's profile identifier into logs.
    """


def parse_goodreads_user_id(raw: str) -> str:
    """Extract the numeric Goodreads user ID from a raw config value.

    Accepts either a bare numeric ID (returned unchanged) or a Goodreads
    profile / review-list URL, from which the first digit run after
    ``/user/show/`` or ``/review/list/`` is extracted.

    Args:
        raw: A numeric ID or a Goodreads URL.

    Returns:
        The numeric user ID as a string.

    Raises:
        ValueError: If no numeric user ID can be extracted.
    """
    text = raw.strip()
    if not text:
        raise ValueError("Goodreads 'user_id' is empty")
    if text.isdigit():
        return text
    for marker in ("/user/show/", "/review/list/"):
        index = text.find(marker)
        if index == -1:
            continue
        match = re.match(r"\d+", text[index + len(marker) :])
        if match:
            return match.group(0)
    raise ValueError(f"Could not extract a Goodreads user ID from: {raw!r}")


def _coerce_string_list(value: Any, field_name: str) -> tuple[list[str], str | None]:
    """Coerce a YAML value into a list of strings.

    Returns ``(values, error)``. Error is non-None when *value* is not a list
    of strings.
    """
    if isinstance(value, str):
        return [], f"'{field_name}' must be a list, got string"
    if not isinstance(value, list):
        return [], f"'{field_name}' must be a list"
    coerced: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            return [], f"'{field_name}' entries must be strings"
        coerced.append(entry)
    return coerced, None


def _status_for_shelf(shelf: str) -> ConsumptionStatus:
    """Map a Goodreads shelf name to a consumption status.

    ``read`` -> completed, ``currently-reading`` -> currently consuming;
    ``to-read`` and any custom shelf -> unread.
    """
    if shelf == "read":
        return ConsumptionStatus.COMPLETED
    if shelf == "currently-reading":
        return ConsumptionStatus.CURRENTLY_CONSUMING
    return ConsumptionStatus.UNREAD


def _child_text(item: Element, tag: str) -> str:
    """Return the stripped text of a direct child element, or ``""``."""
    text = item.findtext(tag)
    return text.strip() if text else ""


def _pages(item: Element) -> str | None:
    """Extract page count from ``<num_pages>`` or nested ``<book><num_pages>``."""
    top = _child_text(item, "num_pages")
    if top:
        return top
    book = item.find("book")
    if book is not None:
        nested = book.findtext("num_pages")
        if nested and nested.strip():
            return nested.strip()
    return None


def _parse_rss_date(raw: str) -> date | None:
    """Parse an RFC 822 Goodreads timestamp into a ``date`` (``None`` on failure)."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return parsed.date() if parsed is not None else None


class GoodreadsRssPlugin(SourcePlugin):
    """Plugin for syncing public Goodreads shelves via RSS.

    Fetches one RSS feed per requested shelf, paginates through every result,
    and yields deduplicated :class:`ContentItem` objects. Requires the target
    profile to be public; no API key is needed.
    """

    @property
    def name(self) -> str:
        return "goodreads_rss"

    @property
    def display_name(self) -> str:
        return "Goodreads (Public Shelves via RSS)"

    @property
    def description(self) -> str:
        return "Sync books from public Goodreads shelves via RSS"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def requires_network(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="user_id",
                field_type=str,
                required=True,
                description="Goodreads numeric user ID or public profile URL",
            ),
            ConfigField(
                name="shelves",
                field_type=list,
                required=False,
                default=list(DEFAULT_SHELVES),
                description=(
                    "Shelves to sync (default: read, currently-reading, "
                    "to-read). Custom shelf names are treated as unread."
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

        raw_user_id = config.get("user_id")
        user_id_str = str(raw_user_id).strip() if raw_user_id is not None else ""
        if not user_id_str:
            errors.append(
                "'user_id' is required (Goodreads numeric user ID or profile URL)"
            )
        else:
            try:
                parse_goodreads_user_id(user_id_str)
            except ValueError as error:
                errors.append(str(error))

        shelves_raw = config.get("shelves")
        if shelves_raw is not None:
            shelves, shelves_error = _coerce_string_list(shelves_raw, "shelves")
            if shelves_error is not None:
                errors.append(shelves_error)
            elif any(not shelf.strip() for shelf in shelves):
                errors.append("'shelves' entries must be non-empty strings")

        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Fetch books from a user's public Goodreads shelves.

        Args:
            config: Must contain 'user_id'; optional 'shelves' list.
            progress_callback: Optional callback for progress updates.

        Yields:
            ContentItem for each unique book across the requested shelves.

        Raises:
            GoodreadsRssError: On network failure or malformed RSS.
        """
        raw_user_id = config.get("user_id")
        user_id_str = str(raw_user_id).strip() if raw_user_id is not None else ""
        try:
            user_id = parse_goodreads_user_id(user_id_str)
        except ValueError as error:
            raise GoodreadsRssError(self.name, str(error)) from error

        # ``.get(key, default)`` only returns the default when the key is
        # absent; an explicit ``shelves: null`` yields ``None``, which is not
        # iterable. Fall back to defaults for both cases while leaving an
        # explicit empty list intact so ``[]`` still means "sync nothing".
        shelves = config.get("shelves")
        if shelves is None:
            shelves = DEFAULT_SHELVES
        source = self.get_source_identifier(config)

        # Accumulate before yielding: custom shelves can overlap the defaults,
        # so a book must be collapsed to its strongest status before emission.
        items_by_key: dict[str, ContentItem] = {}
        order: list[str] = []

        for shelf in shelves:
            status = _status_for_shelf(shelf)
            for element in self._iter_shelf_items(user_id, shelf):
                content = self._build_item(element, shelf, status, source)
                if content is None:
                    continue
                key = content.id or f"{content.title}{content.author}"
                existing = items_by_key.get(key)
                if existing is None:
                    items_by_key[key] = content
                    order.append(key)
                elif _STATUS_RANK[content.status] > _STATUS_RANK[existing.status]:
                    items_by_key[key] = content

        total = len(order)
        logger.info(
            "Collected %d unique books across %d Goodreads shelves",
            total,
            len(shelves),
        )
        for index, key in enumerate(order):
            content = items_by_key[key]
            if progress_callback:
                progress_callback(index + 1, total, content.title)
            yield content

    def _iter_shelf_items(self, user_id: str, shelf: str) -> Iterator[Element]:
        """Yield every ``<item>`` element on a shelf, walking all RSS pages.

        Terminates on the first empty page. Raises :class:`GoodreadsRssError`
        if a shelf exceeds :data:`MAX_PAGES`, which signals a broken feed or a
        redirect loop rather than a genuinely huge shelf.
        """
        for page in range(1, MAX_PAGES + 1):
            xml_text = self._fetch_page(user_id, shelf, page)
            items = self._parse_items(xml_text, shelf)
            if not items:
                return
            yield from items
        raise GoodreadsRssError(
            self.name,
            f"Shelf '{shelf}' exceeded the {MAX_PAGES}-page fetch limit",
        )

    def _fetch_page(self, user_id: str, shelf: str, page: int) -> str:
        """GET a single RSS page for a shelf, returning the response body."""
        url = f"{GOODREADS_BASE}/review/list/{user_id}.rss"
        params: dict[str, str | int] = {
            "shelf": shelf,
            "per_page": PER_PAGE,
            "page": page,
        }
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as error:
            scrubbed = scrub_request_error(error)
            logger.error("Error fetching Goodreads shelf '%s': %s", shelf, scrubbed)
            raise GoodreadsRssError(
                self.name, f"Failed to fetch shelf '{shelf}': {scrubbed}"
            ) from error
        return response.text

    def _parse_items(self, xml_text: str, shelf: str) -> list[Element]:
        """Parse an RSS page into its ``<item>`` elements.

        Uses ``defusedxml`` rather than the stdlib parser: the feed is
        untrusted remote XML, and defusedxml blocks both XXE (external entity)
        attacks and entity-expansion denial-of-service (billion-laughs /
        quadratic-blowup).

        Both malformed XML (``ParseError``) and hostile entity/DTD payloads
        (``DefusedXmlException``) surface as :class:`GoodreadsRssError`. The
        defused-payload message is deliberately generic so the untrusted feed
        content, request URL, and user id never leak into logs or errors.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as error:
            raise GoodreadsRssError(
                self.name, f"Malformed RSS for shelf '{shelf}': {error}"
            ) from error
        except DefusedXmlException as error:
            raise GoodreadsRssError(
                self.name, f"Malformed or unsafe RSS for shelf '{shelf}'"
            ) from error
        return root.findall(".//item")

    def _build_item(
        self,
        element: Element,
        shelf: str,
        status: ConsumptionStatus,
        source: str,
    ) -> ContentItem | None:
        """Build a ContentItem from an RSS ``<item>`` element.

        Returns ``None`` for items without a title. The ``metadata`` dict shares
        ``book_id``/``isbn``/``pages``/``year_published`` with the goodreads_csv
        plugin and additionally carries ``average_rating``, ``description``, and
        ``shelf``. RSS cannot supply ``isbn13`` or ``publisher``, so those keys
        are omitted entirely rather than set to ``None``.
        """
        title = _child_text(element, "title")
        if not title:
            return None

        book_id = _child_text(element, "book_id") or None
        date_completed = None
        if status == ConsumptionStatus.COMPLETED:
            date_completed = _parse_rss_date(_child_text(element, "user_read_at"))

        metadata: dict[str, Any] = {
            "book_id": book_id or "",
            "isbn": _child_text(element, "isbn") or None,
            "pages": _pages(element),
            "year_published": _child_text(element, "book_published") or None,
            "average_rating": _child_text(element, "average_rating") or None,
            "description": _child_text(element, "book_description") or None,
            "shelf": shelf,
        }

        return ContentItem(
            id=book_id,
            title=title,
            author=_child_text(element, "author_name") or None,
            content_type=ContentType.BOOK,
            rating=self.normalize_rating(_child_text(element, "user_rating") or None),
            status=status,
            date_completed=date_completed,
            metadata=metadata,
            source=source,
        )
