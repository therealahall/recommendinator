"""Filling and reading the cover cache."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.service import cover_cache_dir
from src.covers import cache
from src.covers.fetch import CoverUnavailable, fetch_cover
from src.ingestion.urls import UrlOrigin, url_origin
from src.models.content import ContentItem
from src.sources.service import resolve_inputs
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

#: Nothing was tried, so nothing is cleared: the item simply has no art.
NO_COVER = CoverUnavailable("this item has no cover art", permanent=False)


@dataclass(frozen=True)
class SourceAccess:
    auth: tuple[str, str] | None
    verify_ssl: bool


def source_access_by_origin(
    storage: StorageManager, user_id: int = 1
) -> dict[UrlOrigin, SourceAccess]:
    """Calibre-Web's ``/opds/*`` sits behind basic auth a browser ``<img>`` never
    sends, so a cover on a source's origin is fetched with that source's own.
    """
    access: dict[UrlOrigin, SourceAccess] = {}
    for entry in resolve_inputs(storage, user_id):
        base_url = entry.config.get("url")
        origin = url_origin(base_url) if isinstance(base_url, str) else None
        if not isinstance(origin, UrlOrigin):
            continue
        username = entry.config.get("username") or ""
        password = entry.config.get("password") or ""
        access[origin] = SourceAccess(
            auth=(username, password) if username and password else None,
            verify_ssl=bool(entry.config.get("verify_ssl", True)),
        )
    return access


def fill_cover(
    storage: StorageManager,
    config: dict[str, Any],
    item: ContentItem,
    *,
    user_id: int = 1,
) -> Path | CoverUnavailable:
    """The cached file for *item*'s cover, fetched once if it is not there yet."""
    if item.db_id is None or not item.cover_url:
        return NO_COVER

    path = cache.cache_path(cover_cache_dir(config), item.db_id, item.cover_url)
    if path.exists():
        return path

    outcome = _fetch(item.cover_url, source_access_by_origin(storage, user_id))
    if isinstance(outcome, CoverUnavailable):
        if outcome.permanent and storage.clear_cover_url(item.db_id):
            # The provider that dead cover outranked is what can refill it.
            storage.enrichment.requeue(item.db_id)
        logger.info("No cover for %s: %s", sanitize_for_log(item.title), outcome.reason)
        return outcome

    try:
        cache.store(path, outcome)
    except OSError:
        logger.exception("Could not cache the cover for item %d", item.db_id)
        return CoverUnavailable("the cover could not be cached", permanent=False)
    return path


def _fetch(
    cover_url: str, sources: dict[UrlOrigin, SourceAccess]
) -> bytes | CoverUnavailable:
    origin = url_origin(cover_url)
    access = sources.get(origin) if isinstance(origin, UrlOrigin) else None
    if access is None:
        return fetch_cover(cover_url)
    return fetch_cover(
        cover_url,
        auth=access.auth,
        verify=access.verify_ssl,
        # A LAN Calibre-Web is the one private host this app should reach.
        private_allowed=True,
    )
