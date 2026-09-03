"""Filling and reading the cover cache. One walk, so the web action and the CLI
command run the same backfill."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.service import cover_cache_dir
from src.covers import cache
from src.covers.fetch import CoverUnavailable, fetch_cover
from src.ingestion.sync import MAX_REPORTED_ERRORS
from src.ingestion.urls import UrlOrigin, url_origin
from src.models.content import ContentItem
from src.sources.service import resolve_inputs
from src.storage.cover_jobs import CoverBackfillRecord
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
    config: dict[str, Any], storage: StorageManager, user_id: int = 1
) -> dict[UrlOrigin, SourceAccess]:
    """Calibre-Web's ``/opds/*`` sits behind basic auth a browser ``<img>`` never
    sends, so a cover on a source's origin is fetched with that source's own.
    """
    access: dict[UrlOrigin, SourceAccess] = {}
    for entry in resolve_inputs(config, storage=storage, user_id=user_id):
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
    sources: dict[UrlOrigin, SourceAccess] | None = None,
    *,
    user_id: int = 1,
) -> Path | CoverUnavailable:
    """The cached file for *item*'s cover, fetched once if it is not there yet."""
    if item.db_id is None or not item.cover_url:
        return NO_COVER

    path = cache.cache_path(cover_cache_dir(config), item.db_id, item.cover_url)
    if path.exists():
        return path

    if sources is None:
        sources = source_access_by_origin(config, storage, user_id)
    outcome = _fetch(item.cover_url, sources)
    if isinstance(outcome, CoverUnavailable):
        if outcome.permanent and storage.clear_cover_url(item.db_id):
            # The provider that dead cover outranked is what can refill it.
            storage.enrichment.reset(content_item_id=item.db_id)
        logger.info("No cover for %s: %s", sanitize_for_log(item.title), outcome.reason)
        return outcome

    try:
        cache.store(path, outcome)
    except OSError:
        logger.exception("Could not cache the cover for item %d", item.db_id)
        return CoverUnavailable("the cover could not be cached", permanent=False)
    return path


def backfill_covers(
    storage: StorageManager,
    config: dict[str, Any],
    *,
    user_id: int = 1,
) -> CoverBackfillRecord:
    """Fetch every library cover that is not cached yet, publishing as it goes."""
    record = CoverBackfillRecord(running=True)
    cache_dir = cover_cache_dir(config)
    sources = source_access_by_origin(config, storage, user_id)
    pending = [
        item
        for item in storage.get_content_items(user_id=user_id)
        if item.db_id is not None
        and item.cover_url
        and not cache.cache_path(cache_dir, item.db_id, item.cover_url).exists()
    ]

    record.total = len(pending)
    record.without_cover = storage.enrichment.settled_without_cover(user_id)
    stopped = False
    for item in pending:
        if storage.cover_jobs.stop_requested():
            stopped = True
            break
        record.current_item = item.title
        storage.cover_jobs.heartbeat(record)
        outcome = fill_cover(storage, config, item, sources, user_id=user_id)
        record.processed += 1
        if isinstance(outcome, Path):
            record.cached += 1
        elif outcome.permanent:
            record.cleared += 1
        else:
            record.failed += 1
            if len(record.errors) < MAX_REPORTED_ERRORS:
                record.errors.append(f"{item.title}: {outcome.reason}")
    record.current_item = ""
    record.running = False
    record.completed = not stopped
    record.cancelled = stopped
    return record


def start_backfill(
    storage: StorageManager, config: dict[str, Any], *, user_id: int = 1
) -> CoverBackfillRecord | None:
    """None when a backfill is already running, whichever interface started it."""
    if not storage.cover_jobs.claim():
        return None

    def run() -> None:
        try:
            record = backfill_covers(storage, config, user_id=user_id)
        except Exception:
            logger.exception("Cover backfill failed")
            # Not the exception's words: they reach an HTTP body, with the path.
            record = storage.cover_jobs.read()
            record.errors.append("the backfill stopped on an error")
        storage.cover_jobs.finish(record)

    threading.Thread(target=run, daemon=True).start()
    return storage.cover_jobs.read()


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
