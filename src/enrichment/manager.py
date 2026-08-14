"""Background enrichment manager for processing content items.

The EnrichmentManager coordinates the enrichment process, running providers
in a background thread to fill gaps in content metadata.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests

from src.enrichment.provider_base import EnrichmentResult
from src.enrichment.rate_limiter import RateLimiter
from src.enrichment.registry import EnrichmentRegistry, get_enrichment_registry
from src.models.content import ContentItem, ContentType, get_enum_value
from src.storage.global_secrets import read_secret
from src.utils.request_errors import scrub_request_error
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# HTTP statuses that mean "ask again later" rather than "your request is
# wrong". Every other 4xx is the caller's own configuration — a revoked or
# mistyped API key above all — and repeating it on every run is a flood aimed
# at the provider that can never come out differently.
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429})


@dataclass(frozen=True)
class _ProviderFailure:
    """One provider's failure on one item, described without its own words.

    ``reason`` is built only from values this module derives, never from the
    provider's message, because it is persisted to the database.
    """

    provider: str
    reason: str
    retryable: bool

    def __str__(self) -> str:
        return f"{self.provider}: {self.reason}"


def _underlying_request_error(error: Exception) -> requests.RequestException | None:
    """Find the ``requests`` failure underneath a provider's exception.

    Every raise form leaves it reachable: ``from error`` on ``__cause__``,
    implicit chaining and ``from None`` on ``__context__``. TMDB and RAWG
    raise ``from None``, so a suppressed chain is still read.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, requests.RequestException):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _classify_failure(provider_name: str, error: Exception) -> _ProviderFailure:
    """Describe a provider failure safely enough to store it.

    A provider is free to interpolate the failing request URL — API key and
    all — into its own message, and enrichment errors are written to the
    database, so the description is assembled here instead: the provider name,
    plus the HTTP status or transport error class ``requests`` reported, or the
    exception type when no ``requests`` failure is on the chain.

    Args:
        provider_name: Name of the provider that failed.
        error: The exception it raised.

    Returns:
        The failure, flagged retryable unless it is positively identifiable as
        the provider rejecting the request itself.
    """
    request_error = _underlying_request_error(error)
    if request_error is None:
        return _ProviderFailure(provider_name, type(error).__name__, retryable=True)
    return _ProviderFailure(
        provider_name,
        scrub_request_error(request_error),
        retryable=_is_retryable(request_error),
    )


def _is_retryable(error: requests.RequestException) -> bool:
    """Whether repeating this request could plausibly succeed later.

    Transport failures (connection refused, timeouts) and server-side or
    throttling statuses clear on their own. Any other client error is the
    request being wrong, and will be answered identically every time.
    """
    if isinstance(error, requests.HTTPError) and error.response is not None:
        status = error.response.status_code
        return status >= 500 or status in _RETRYABLE_HTTP_STATUSES
    return True


@dataclass
class EnrichmentJobStatus:
    """Status of an enrichment job."""

    # Job state
    running: bool = False
    completed: bool = False
    cancelled: bool = False

    # Progress
    items_processed: int = 0
    items_enriched: int = 0
    items_failed: int = 0
    items_not_found: int = 0
    total_items: int = 0

    # Current item being processed
    current_item: str = ""

    # Content type filter (if any)
    content_type: str | None = None

    # Errors encountered
    errors: list[str] = field(default_factory=list)

    # Timing
    started_at: float | None = None
    completed_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds."""
        if self.started_at is None:
            return 0.0
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    @property
    def progress_percent(self) -> float:
        """Get progress as percentage (0-100)."""
        if self.total_items == 0:
            return 0.0
        return (self.items_processed / self.total_items) * 100


class EnrichmentManager:
    """Manages background enrichment of content items.

    Coordinates the enrichment process:
    1. Fetches items needing enrichment from storage
    2. Runs appropriate providers based on content type
    3. Merges results into content metadata (gap-filling only)
    4. Updates enrichment status in database

    Thread safety:
        All public methods are thread-safe. The enrichment job runs in
        a background thread and can be controlled via start/stop methods.

    Example usage:
        manager = EnrichmentManager(storage_manager, config)

        # Start enrichment for all types
        manager.start_enrichment()

        # Or start for a specific type
        manager.start_enrichment(content_type=ContentType.MOVIE)

        # Check status
        status = manager.get_status()
        print(f"Progress: {status.progress_percent:.1f}%")

        # Stop if needed
        manager.stop_enrichment()
    """

    def __init__(
        self,
        storage_manager: StorageManager,
        config: dict[str, Any],
        registry: EnrichmentRegistry | None = None,
    ) -> None:
        """Initialize enrichment manager.

        Args:
            storage_manager: StorageManager instance for database access
            config: Application configuration dict
            registry: Optional EnrichmentRegistry (uses global if not provided)
        """
        self.storage_manager = storage_manager
        self.config = config
        self.registry = registry or get_enrichment_registry()

        # Job state
        self._status = EnrichmentJobStatus()
        self._stop_requested = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Rate limiters per provider
        self._rate_limiters: dict[str, RateLimiter] = {}

        # Resolved global secrets, keyed by dotted registry key. Each secret is
        # a SQLite query + Fernet decrypt, so it is read once per manager
        # instance (one per run) and reused across every processed item.
        self._secret_cache: dict[str, str | None] = {}

    def start_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        include_not_found: bool = False,
    ) -> bool:
        """Start background enrichment job.

        Args:
            content_type: Optional filter to only enrich one content type
            user_id: User ID for filtering items
            include_not_found: Also retry items previously marked as not_found

        Returns:
            True if job started, False if already running
        """
        with self._lock:
            if self._status.running:
                logger.warning("Enrichment job already running")
                return False

            # Reset status
            self._status = EnrichmentJobStatus(
                running=True,
                content_type=content_type.value if content_type else None,
            )
            self._stop_requested = False

            # Start background thread
            self._thread = threading.Thread(
                target=self._run_enrichment,
                args=(content_type, user_id, include_not_found),
                daemon=True,
            )
            self._thread.start()

            type_msg = (
                f" for {content_type.value}" if content_type else " for all types"
            )
            retry_msg = " (including not_found)" if include_not_found else ""
            logger.info(
                "[ENRICHMENT] === Starting enrichment job%s%s ===",
                type_msg,
                retry_msg,
            )
            return True

    def stop_enrichment(self) -> None:
        """Request the enrichment job to stop.

        The job will stop after completing the current item.
        """
        with self._lock:
            if not self._status.running:
                return

            self._stop_requested = True
            logger.info("Requested enrichment job stop")

    def _wait_for_completion(self, timeout: float = 5.0) -> bool:
        """Wait for the background enrichment thread to finish.

        NOTE: This method exists for test synchronization.  Production code
        should use :meth:`get_status` to poll running state instead.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if the thread completed within the timeout, False otherwise.
        """
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def get_status(self) -> EnrichmentJobStatus:
        """Get current job status.

        Returns:
            Copy of current EnrichmentJobStatus
        """
        with self._lock:
            return EnrichmentJobStatus(
                running=self._status.running,
                completed=self._status.completed,
                cancelled=self._status.cancelled,
                items_processed=self._status.items_processed,
                items_enriched=self._status.items_enriched,
                items_failed=self._status.items_failed,
                items_not_found=self._status.items_not_found,
                total_items=self._status.total_items,
                current_item=self._status.current_item,
                content_type=self._status.content_type,
                errors=list(self._status.errors),
                started_at=self._status.started_at,
                completed_at=self._status.completed_at,
            )

    def _run_enrichment(
        self,
        content_type: ContentType | None,
        user_id: int | None,
        include_not_found: bool = False,
    ) -> None:
        """Run the enrichment job in background thread.

        Args:
            content_type: Optional content type filter
            user_id: User ID for filtering items
            include_not_found: Also retry items previously marked as not_found
        """
        try:
            with self._lock:
                self._status.started_at = time.time()

            # Get batch size from config
            enrichment_config = self.config.get("enrichment", {})
            batch_size = enrichment_config.get("batch_size", 50)

            # If retrying not_found items, collect their IDs first to avoid infinite loop
            not_found_ids: set[int] = set()
            if include_not_found:
                # Collect all not_found item IDs upfront
                not_found_items = self.storage_manager.get_items_needing_enrichment(
                    content_type=content_type,
                    user_id=user_id,
                    limit=10000,  # Get all not_found items
                    include_not_found=True,
                )
                # Filter to only those that are actually not_found (not new items)
                for db_id, _item in not_found_items:
                    status = self.storage_manager.get_enrichment_status(db_id)
                    if status and status.get("enrichment_quality") == "not_found":
                        not_found_ids.add(db_id)
                logger.info(
                    "[ENRICHMENT] Found %d not_found items to retry",
                    len(not_found_ids),
                )

            # Status polling reads total_items mid-run, so it must be set
            # before the first batch starts. Querying upfront avoids the
            # previous "growing in batch_size steps" UI behavior.
            pending_count = self.storage_manager.count_items_needing_enrichment(
                content_type=content_type,
                user_id=user_id,
            )
            with self._lock:
                self._status.total_items = pending_count + len(not_found_ids)

            # An item whose provider errored stays queued so a later run
            # retries it, which means this run's own query keeps returning it.
            # The queue is ordered by database ID, so walking a cursor forward
            # past the last row fetched reaches the items behind the failures
            # while keeping every fetch exactly one batch wide.
            after_db_id: int | None = None
            # A not_found item pulled in from the set below joins the queue
            # proper if it fails, and it sits behind the cursor rather than
            # ahead of it, so the cursor alone cannot keep it from coming back.
            retried_ids: set[int] = set()

            # Process items in batches
            while not self._stop_requested:
                # Fetch next batch of items (normal items only, not include_not_found)
                fetched = self.storage_manager.get_items_needing_enrichment(
                    content_type=content_type,
                    user_id=user_id,
                    limit=batch_size,
                    include_not_found=False,
                    after_db_id=after_db_id,
                )
                if fetched:
                    after_db_id = max(db_id for db_id, _item in fetched)
                items = [
                    (db_id, item) for db_id, item in fetched if db_id not in retried_ids
                ]

                # Add any remaining not_found items to this batch
                if not_found_ids and len(items) < batch_size:
                    # Fetch not_found items in a single batch query
                    batch_ids = list(not_found_ids)[: batch_size - len(items)]
                    batch_items = self.storage_manager.get_content_items_by_db_ids(
                        batch_ids
                    )
                    # Build a db_id -> item map from the batch results
                    fetched_map = {
                        item.db_id: item
                        for item in batch_items
                        if item.db_id is not None
                    }
                    for db_id in batch_ids:
                        if db_id in fetched_map:
                            items.append((db_id, fetched_map[db_id]))
                            not_found_ids.discard(db_id)
                            retried_ids.add(db_id)

                if not fetched and not items:
                    # Both queues are drained
                    break

                # Process each item
                self._process_batch(items)

            # Mark job as complete
            with self._lock:
                self._status.running = False
                self._status.completed = not self._stop_requested
                self._status.cancelled = self._stop_requested
                self._status.completed_at = time.time()
                self._status.current_item = ""

            job_result = "cancelled" if self._stop_requested else "completed"
            logger.info(
                "[ENRICHMENT] === Job %s === "
                "Processed: %d, Enriched: %d, Not found: %d, Failed: %d",
                job_result,
                self._status.items_processed,
                self._status.items_enriched,
                self._status.items_not_found,
                self._status.items_failed,
            )

        except Exception as error:
            # ``status.errors`` is served to clients, so it carries the type
            # name only. The log is the operator's own, and gets the traceback.
            rendered = type(error).__name__
            logger.error("Enrichment job failed: %s", rendered, exc_info=True)
            with self._lock:
                self._status.running = False
                self._status.errors.append(f"Job error: {rendered}")

    def _process_batch(self, items: list[tuple[int, ContentItem]]) -> None:
        """Process a batch of items.

        Args:
            items: List of (db_id, ContentItem) tuples
        """
        for db_id, item in items:
            if self._stop_requested:
                return
            self._process_item(db_id, item)

    def _process_item(self, db_id: int, item: ContentItem) -> None:
        """Process a single content item.

        Args:
            db_id: Database ID of the item
            item: ContentItem to enrich
        """
        with self._lock:
            self._status.current_item = item.title
            item_num = self._status.items_processed + 1
            total = self._status.total_items

        # Get content type
        content_type = (
            item.content_type
            if isinstance(item.content_type, ContentType)
            else ContentType(item.content_type)
        )
        content_type_str = get_enum_value(content_type)

        # Titles come from imported files and POST /api/complete, neither of
        # which restricts characters, so every one of them is escaped here.
        safe_title = sanitize_for_log(item.title)

        logger.debug(
            "[ENRICHMENT] Processing %s %d/%d - %s",
            content_type_str,
            item_num,
            total,
            safe_title,
        )

        # Find providers for this content type
        enabled_providers = self.registry.get_enabled_providers(self.config)
        matching_providers = [
            provider
            for provider in enabled_providers
            if content_type in provider.content_types
        ]

        if not matching_providers:
            # No providers available for this content type
            logger.debug(
                "[ENRICHMENT] No providers for %s: %s", content_type_str, safe_title
            )
            self.storage_manager.mark_enrichment_complete(db_id, "none", "not_found")
            with self._lock:
                self._status.items_processed += 1
                self._status.items_not_found += 1
            return

        # Providers that never gave an answer for this item because they raised.
        failures: list[_ProviderFailure] = []

        # Try each provider until one succeeds
        for provider in matching_providers:
            try:
                # Apply rate limiting
                limiter = self._get_rate_limiter(provider.name)
                limiter.acquire()

                # Get provider config
                provider_config = self._get_provider_config(provider.name)

                logger.debug(
                    "[ENRICHMENT] Trying %s for %s: %s",
                    provider.name,
                    content_type_str,
                    safe_title,
                )

                # Enrich
                result = provider.enrich(item, provider_config)

                if result and result.match_quality != "not_found":
                    # Success - merge and save
                    self._apply_enrichment(db_id, item, result)
                    self.storage_manager.mark_enrichment_complete(
                        db_id, provider.name, result.match_quality
                    )
                    logger.info(
                        "[ENRICHMENT] Enriched %s via %s (quality=%s): %s",
                        content_type_str,
                        provider.name,
                        result.match_quality,
                        safe_title,
                    )
                    with self._lock:
                        self._status.items_processed += 1
                        self._status.items_enriched += 1
                    return
                else:
                    logger.debug(
                        "[ENRICHMENT] %s returned not_found: %s",
                        provider.name,
                        safe_title,
                    )

            except Exception as error:
                # One branch for every exception, because a bare ValueError
                # quoting the item's title leaked through the catch-all that
                # used to sit beside the ProviderError one.
                failure = _classify_failure(provider.name, error)
                logger.warning(
                    "[ENRICHMENT] Provider %s failed: %s", provider.name, failure.reason
                )
                failures.append(failure)
                with self._lock:
                    self._status.errors.append(str(failure))

        if failures:
            reported = "; ".join(str(failure) for failure in failures)
            if any(failure.retryable for failure in failures):
                # A provider that blew up, timed out or could not be reached
                # never said whether it has this item, so "no match" is an
                # unknown rather than a settled miss. Record the failure, which
                # leaves the item queued for the next run instead of retiring
                # it as not_found.
                logger.info(
                    "[ENRICHMENT] Enrichment of %s failed, will retry: %s",
                    content_type_str,
                    safe_title,
                )
                self.storage_manager.mark_enrichment_failed(db_id, reported)
                with self._lock:
                    self._status.items_processed += 1
                    self._status.items_failed += 1
                return

            # Every provider rejected the request itself, so the next run would
            # be rejected the same way. Settle the item rather than queue the
            # whole library against a provider that answers none of it.
            logger.warning(
                "[ENRICHMENT] Every provider rejected %s, not retrying: %s",
                safe_title,
                reported,
            )
        else:
            # Every provider answered and none of them has this item
            logger.debug(
                "[ENRICHMENT] No match found for %s: %s", content_type_str, safe_title
            )

        self.storage_manager.mark_enrichment_complete(db_id, "none", "not_found")
        with self._lock:
            self._status.items_processed += 1
            self._status.items_not_found += 1

    def _get_rate_limiter(self, provider_name: str) -> RateLimiter:
        """Get or create rate limiter for a provider.

        Args:
            provider_name: Provider name

        Returns:
            RateLimiter for the provider
        """
        if provider_name not in self._rate_limiters:
            provider = self.registry.get_provider(provider_name)
            rate = provider.rate_limit_requests_per_second if provider else 1.0
            self._rate_limiters[provider_name] = RateLimiter(requests_per_second=rate)
        return self._rate_limiters[provider_name]

    def _get_provider_config(self, provider_name: str) -> dict[str, Any]:
        """Get configuration for a specific provider.

        Non-sensitive provider settings come from the assembled config; each
        sensitive field (e.g. ``api_key``) is overlaid from the encrypted
        ``credentials`` table, where it lives after boot migration instead of
        plaintext config. A missing secret leaves the field absent so the
        provider degrades gracefully.

        Args:
            provider_name: Provider name

        Returns:
            Provider-specific config dict (a fresh copy — never mutates config)
        """
        enrichment_config: dict[str, Any] = self.config.get("enrichment", {})
        providers_config: dict[str, Any] = enrichment_config.get("providers", {})
        provider_config: dict[str, Any] = dict(providers_config.get(provider_name, {}))

        provider = self.registry.get_provider(provider_name)
        if provider is not None:
            for config_field in provider.get_config_schema():
                if not config_field.sensitive:
                    continue
                secret = self._read_cached_secret(
                    f"enrichment.providers.{provider_name}.{config_field.name}"
                )
                if secret is not None:
                    provider_config[config_field.name] = secret
        return provider_config

    def _read_cached_secret(self, key: str) -> str | None:
        """Return the decrypted global secret for *key*, reading it at most once.

        Enrichment resolves each provider's secrets for every content item, and
        each read is a SQLite query plus a Fernet decrypt. Caching the result
        (including a missing ``None``) keeps that cost off the per-item hot loop.
        The manager is short-lived — one per run — so the cache never outlives
        the settings it captured.
        """
        if key not in self._secret_cache:
            self._secret_cache[key] = read_secret(self.storage_manager, key)
        return self._secret_cache[key]

    def _apply_enrichment(
        self,
        db_id: int,
        item: ContentItem,
        result: EnrichmentResult,
    ) -> None:
        """Apply enrichment result to item using gap-filling strategy.

        Only fills in missing fields - never overwrites existing data.

        Args:
            db_id: Database ID of the item
            item: Original ContentItem
            result: EnrichmentResult to apply
        """
        merged_metadata = merge_enrichment(item.metadata or {}, result)

        # Update the item with merged metadata
        updated_item = ContentItem(
            id=item.id,
            user_id=item.user_id,
            title=item.title,
            author=item.author,
            content_type=item.content_type,
            status=item.status,
            rating=item.rating,
            review=item.review,
            date_completed=item.date_completed,
            source=item.source,
            metadata=merged_metadata,
        )

        # Save back to storage
        self.storage_manager.save_content_item(updated_item)


def merge_enrichment(
    existing_metadata: dict[str, Any],
    result: EnrichmentResult,
) -> dict[str, Any]:
    """Merge enrichment result into existing metadata using gap-filling.

    Only fills in fields that are missing or empty in the existing metadata.
    Never overwrites existing data.

    Args:
        existing_metadata: Current metadata dict
        result: EnrichmentResult with new data

    Returns:
        Merged metadata dict
    """
    merged = dict(existing_metadata)

    # Merge genres - enrichment provides better genre data
    if result.genres:
        existing_genres = merged.get("genres", []) or []
        if isinstance(existing_genres, str):
            try:
                existing_genres = json.loads(existing_genres)
            except (json.JSONDecodeError, TypeError):
                existing_genres = [existing_genres] if existing_genres else []
        # Enrichment genres go first (they're more standardized), then existing
        combined = list(result.genres) + [
            g for g in existing_genres if g not in result.genres
        ]
        merged["genres"] = combined

    # Merge tags - combine enrichment tags with existing
    if result.tags:
        existing_tags = merged.get("tags", []) or []
        if isinstance(existing_tags, str):
            try:
                existing_tags = json.loads(existing_tags)
            except (json.JSONDecodeError, TypeError):
                existing_tags = [existing_tags] if existing_tags else []
        # Enrichment tags go first (thematic), then existing (may include platform tags)
        combined = list(result.tags) + [
            t for t in existing_tags if t not in result.tags
        ]
        merged["tags"] = combined

    # Fill description if missing
    if not merged.get("description") and result.description:
        merged["description"] = result.description

    # Fill extra_metadata fields (only if missing)
    for key, value in result.extra_metadata.items():
        if key not in merged or merged[key] is None or merged[key] == "":
            merged[key] = value

    # Store the enrichment source
    if result.external_id:
        merged["enrichment_id"] = result.external_id

    return merged
