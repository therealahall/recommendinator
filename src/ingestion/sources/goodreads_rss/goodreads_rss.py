"""The three default shelves (``read``, ``currently-reading``, ``to-read``) are
mutually exclusive, but users may add custom shelves that overlap them, so
:meth:`GoodreadsRssPlugin.fetch` deduplicates within a single run and keeps the
strongest consumption status (``completed`` > ``currently_consuming`` > ``unread``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as ET
import requests
from defusedxml.common import DefusedXmlException

from src import __version__ as APP_VERSION
from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.request_errors import scrub_request_error
from src.utils.series import split_series_from_title
from src.utils.text import exception_for_log, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

GOODREADS_BASE = "https://www.goodreads.com"

# The only feed path Goodreads still serves to a signed-out client. The newer
# ``/review/list/<id>.rss`` answers 302 to the sign-in page for every profile,
# public ones included.
FEED_PATH = "/review/list_rss"

# Where that redirect lands. ``requests`` follows it to a 200 HTML page, so
# without this check the parser blames Goodreads' markup for an access refusal.
SIGN_IN_PATH = "/user/sign_in"

DEFAULT_SHELVES = ["read", "currently-reading", "to-read"]

# Goodreads paginates RSS feeds; request the max page size and walk pages
# until one comes back empty.
PER_PAGE = 100

# Hard ceiling on pages walked per shelf. At PER_PAGE=100 this covers 50,000
# books on a single shelf; hitting it means a broken feed or a redirect loop,
# so we fail loudly instead of paginating forever.
MAX_PAGES = 500

REQUEST_TIMEOUT = 30

# Goodreads sits behind Amazon's edge, which answers the ``requests`` default
# ``python-requests/x.y`` with 403. Say who is calling rather than impersonate
# a browser: a block should be a decision about this app.
USER_AGENT = (
    f"Recommendinator/{APP_VERSION} "
    "(+https://github.com/therealahall/recommendinator)"
)

# ``*/*`` because Goodreads' negotiation has answered 406 to an Accept naming
# only RSS and XML, and nothing here needs a particular type back.
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}

# Consumption-status precedence for cross-shelf deduplication.
_STATUS_RANK = {
    ConsumptionStatus.UNREAD.value: 0,
    ConsumptionStatus.CURRENTLY_CONSUMING.value: 1,
    ConsumptionStatus.COMPLETED.value: 2,
}


class GoodreadsRssError(SourceError):
    """Messages are scrubbed of the request URL to avoid leaking the user's profile
    identifier into logs.
    """


def parse_goodreads_user_id(raw: str) -> str:
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
    if shelf == "read":
        return ConsumptionStatus.COMPLETED
    if shelf == "currently-reading":
        return ConsumptionStatus.CURRENTLY_CONSUMING
    return ConsumptionStatus.UNREAD


def _child_text(item: Element, tag: str) -> str:
    text = item.findtext(tag)
    return text.strip() if text else ""


def _pages(item: Element) -> str | None:
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
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return parsed.date() if parsed is not None else None


class GoodreadsRssPlugin(SourcePlugin):
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
        raw_user_id = config.get("user_id")
        user_id_str = str(raw_user_id).strip() if raw_user_id is not None else ""
        try:
            user_id = parse_goodreads_user_id(user_id_str)
        except ValueError as error:
            raise GoodreadsRssError(self.name, str(error)) from error

        # ``.get(key, default)`` only returns the default when the key is
        # absent; an explicit ``shelves: null`` yields ``None``, which is not iterable.
        shelves = config.get("shelves")
        if shelves is None:
            shelves = DEFAULT_SHELVES

        # Accumulate before yielding: custom shelves can overlap the defaults,
        # so a book must be collapsed to its strongest status before emission.
        items_by_key: dict[str, ContentItem] = {}
        order: list[str] = []

        for shelf in shelves:
            status = _status_for_shelf(shelf)
            for element in self._iter_shelf_items(user_id, shelf):
                content = self._build_item(element, shelf, status)
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
        url = f"{GOODREADS_BASE}{FEED_PATH}/{user_id}"
        params: dict[str, str | int] = {
            "shelf": shelf,
            "per_page": PER_PAGE,
            "page": page,
        }
        try:
            response = requests.get(
                url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
        except requests.RequestException as error:
            scrubbed = scrub_request_error(error)
            logger.error(
                "Error fetching Goodreads shelf '%s': %s",
                sanitize_for_log(shelf),
                exception_for_log(error),
            )
            raise GoodreadsRssError(
                self.name, f"Failed to fetch shelf '{shelf}': {scrubbed}"
            ) from error
        if urlsplit(response.url).path == SIGN_IN_PATH:
            raise GoodreadsRssError(
                self.name,
                f"Goodreads sent shelf '{shelf}' to its sign-in page: the "
                "profile's shelves are not public",
            )
        return response.text

    def _parse_items(self, xml_text: str, shelf: str) -> list[Element]:
        """Uses ``defusedxml`` rather than the stdlib parser: the feed is
        untrusted remote XML, and defusedxml blocks both XXE (external entity)
        attacks and entity-expansion denial-of-service (billion-laughs /
        quadratic-blowup).
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
    ) -> ContentItem | None:
        """RSS cannot supply ``isbn13`` or ``publisher``, so those keys are omitted
        entirely rather than set to ``None``.
        """
        raw_title = _child_text(element, "title")
        if not raw_title:
            return None

        title, series = split_series_from_title(raw_title)
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
            **series,
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
        )
