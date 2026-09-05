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
from src.models.detail_fields import PROVIDER_OWNED_METADATA_KEYS
from src.storage.enrichment_jobs import EnrichmentJobRecord
from src.storage.global_secrets import read_secret
from src.utils.request_errors import scrub_request_error
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Every other 4xx is the caller's own configuration — a revoked or
# mistyped API key above all — and repeating it on every run is a flood aimed
# at the provider that can never come out differently.
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429})

_MAX_CONSECUTIVE_REJECTIONS = 5


@dataclass(frozen=True)
class _ProviderFailure:
    """``reason`` is built only from values this module derives, never from the
    provider's message, because it is persisted to the database.
    """

    provider: str
    reason: str
    retryable: bool

    def __str__(self) -> str:
        return f"{self.provider}: {self.reason}"


def _underlying_request_error(error: Exception) -> requests.RequestException | None:
    """TMDB and RAWG raise ``from None``, so a suppressed chain is still read."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, requests.RequestException):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _classify_failure(provider_name: str, error: Exception) -> _ProviderFailure:
    """A provider is free to interpolate the failing request URL — API key and
    all — into its own message, and enrichment errors are written to the
    database, so the description is assembled here instead.
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
    """Transport failures (connection refused, timeouts) and server-side or
    throttling statuses clear on their own.
    """
    if isinstance(error, requests.HTTPError) and error.response is not None:
        status = error.response.status_code
        return status >= 500 or status in _RETRYABLE_HTTP_STATUSES
    return True


@dataclass
class EnrichmentJobStatus:
    running: bool = False
    completed: bool = False
    cancelled: bool = False

    items_processed: int = 0
    items_enriched: int = 0
    items_failed: int = 0
    items_not_found: int = 0
    total_items: int = 0

    current_item: str = ""

    content_type: str | None = None

    errors: list[str] = field(default_factory=list)

    started_at: float | None = None
    completed_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    @property
    def progress_percent(self) -> float:
        if self.total_items == 0:
            return 0.0
        return (self.items_processed / self.total_items) * 100


#: Whole up to here; past it the count is the useful part.
MAX_RECORDED_ERRORS = 50

_POLL_INTERVAL_SECONDS = 1.0


def _to_status(record: EnrichmentJobRecord) -> EnrichmentJobStatus:
    return EnrichmentJobStatus(
        running=record.running,
        completed=record.completed,
        cancelled=record.cancelled,
        items_processed=record.items_processed,
        items_enriched=record.items_enriched,
        items_failed=record.items_failed,
        items_not_found=record.items_not_found,
        total_items=record.total_items,
        current_item=record.current_item,
        content_type=record.content_type,
        errors=list(record.errors),
        started_at=(
            record.started_at.timestamp() if record.started_at is not None else None
        ),
        completed_at=(
            record.finished_at.timestamp() if record.finished_at is not None else None
        ),
    )


def job_status(storage_manager: StorageManager) -> EnrichmentJobStatus:
    """The live job, for a caller with no reason to build a manager."""
    return _to_status(storage_manager.enrichment_jobs.read())


class EnrichmentManager:
    def __init__(
        self,
        storage_manager: StorageManager,
        config: dict[str, Any],
        registry: EnrichmentRegistry | None = None,
    ) -> None:
        self.storage_manager = storage_manager
        self.config = config
        self.registry = registry or get_enrichment_registry()

        # Running, and asked-to-stop, are answers only the shared record can
        # give across two processes.
        self._jobs = storage_manager.enrichment_jobs
        self._status = EnrichmentJobStatus()
        self._dropped_errors = 0
        self._published_at = 0.0
        self._stop_checked_at = 0.0
        self._stop_cached = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._rejections: dict[str, int] = {}
        self._abandoned_providers: set[str] = set()
        self._skipped_for_abandonment = False

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
        with self._lock:
            # The claim is the mutual exclusion, and it spans processes: a
            # local flag let the CLI start a second job beside the server's.
            if not self._jobs.claim(content_type.value if content_type else None):
                logger.warning("Enrichment job already running")
                return False

            self._status = EnrichmentJobStatus(
                running=True,
                content_type=content_type.value if content_type else None,
            )
            self._rejections = {}
            self._abandoned_providers = set()
            self._skipped_for_abandonment = False

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

    def stop_enrichment(self) -> bool:
        """It stops after the current item."""
        asked = self._jobs.request_stop()
        if asked:
            logger.info("Requested enrichment job stop")
        return asked

    def _wait_for_completion(self, timeout: float = 5.0) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def get_status(self) -> EnrichmentJobStatus:
        """The job as the shared record has it, whoever started it."""
        return _to_status(self._jobs.read())

    def _record_error(self, rendered: str) -> None:
        with self._lock:
            errors = self._status.errors
            if len(errors) < MAX_RECORDED_ERRORS:
                errors.append(rendered)
                return
            self._dropped_errors += 1
            summary = f"… and {self._dropped_errors} more"
            if len(errors) == MAX_RECORDED_ERRORS:
                errors.append(summary)
            else:
                errors[-1] = summary

    def _stop_asked(self) -> bool:
        """An item matching no provider never touches the network, so a per-item
        read makes a wrong-type sweep thousands of round trips and nothing else.
        """
        now = time.monotonic()
        if now - self._stop_checked_at >= _POLL_INTERVAL_SECONDS:
            self._stop_cached = self._jobs.stop_requested()
            self._stop_checked_at = now
        return self._stop_cached

    def _publish(self, *, force: bool = False) -> None:
        """Mirror this run's tally into the record the other process reads."""
        now = time.monotonic()
        if not force and now - self._published_at < _POLL_INTERVAL_SECONDS:
            return
        self._published_at = now
        with self._lock:
            snapshot = EnrichmentJobStatus(
                items_processed=self._status.items_processed,
                items_enriched=self._status.items_enriched,
                items_failed=self._status.items_failed,
                items_not_found=self._status.items_not_found,
                total_items=self._status.total_items,
                current_item=self._status.current_item,
                errors=list(self._status.errors),
            )
        self._jobs.heartbeat(
            items_processed=snapshot.items_processed,
            items_enriched=snapshot.items_enriched,
            items_failed=snapshot.items_failed,
            items_not_found=snapshot.items_not_found,
            total_items=snapshot.total_items,
            current_item=snapshot.current_item,
            errors=snapshot.errors,
        )

    def _run_enrichment(
        self,
        content_type: ContentType | None,
        user_id: int | None,
        include_not_found: bool = False,
    ) -> None:
        try:
            with self._lock:
                self._status.started_at = time.time()

            enrichment_config = self.config.get("enrichment", {})
            batch_size = enrichment_config.get("batch_size", 50)

            # If retrying not_found items, collect their IDs first to avoid infinite loop
            not_found_ids: set[int] = set()
            if include_not_found:
                not_found_ids = set(
                    self.storage_manager.enrichment.not_found_ids(
                        content_type=content_type,
                        user_id=user_id,
                    )
                )
                logger.info(
                    "[ENRICHMENT] Found %d not_found items to retry",
                    len(not_found_ids),
                )

            # Status polling reads total_items mid-run, so it must be set
            # before the first batch starts. Querying upfront avoids the
            # previous "growing in batch_size steps" UI behavior.
            pending_count = self.storage_manager.enrichment.count_needing(
                content_type=content_type,
                user_id=user_id,
            )
            with self._lock:
                self._status.total_items = pending_count + len(not_found_ids)
            self._publish(force=True)

            # An item whose provider errored stays queued so a later run
            # retries it, which means this run's own query keeps returning it.
            after_db_id: int | None = None
            # A not_found item pulled in from the set below joins the queue
            # proper if it fails, and it sits behind the cursor rather than
            # ahead of it, so the cursor alone cannot keep it from coming back.
            retried_ids: set[int] = set()

            while not self._stop_asked() and not self._every_provider_abandoned(
                content_type
            ):
                fetched = self.storage_manager.enrichment.items_needing(
                    content_type=content_type,
                    user_id=user_id,
                    limit=batch_size,
                    after_db_id=after_db_id,
                )
                if fetched:
                    after_db_id = max(db_id for db_id, _item in fetched)
                items = [
                    (db_id, item) for db_id, item in fetched if db_id not in retried_ids
                ]

                if not_found_ids and len(items) < batch_size:
                    batch_ids = list(not_found_ids)[: batch_size - len(items)]
                    batch_items = self.storage_manager.get_content_items_by_db_ids(
                        batch_ids
                    )
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
                    break

                self._process_batch(items)

            # Uncached, unlike the loop's check: this one decides what the run
            # is recorded as.
            stopped = self._jobs.stop_requested()
            completed = not stopped and not self._skipped_for_abandonment
            with self._lock:
                self._status.running = False
                self._status.completed = completed
                self._status.cancelled = stopped
                self._status.completed_at = time.time()
                self._status.current_item = ""
                errors = list(self._status.errors)
            self._publish(force=True)
            self._jobs.finish(completed=completed, cancelled=stopped, errors=errors)

            if stopped:
                job_result = "cancelled"
            elif not completed:
                job_result = "stopped on an error"
            else:
                job_result = "completed"
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
            self._record_error(f"Job error: {rendered}")
            with self._lock:
                self._status.running = False
                errors = list(self._status.errors)
            # Neither completed nor cancelled: it stopped without finishing,
            # and the error is what says why. Releasing the claim is the point.
            self._jobs.finish(completed=False, cancelled=False, errors=errors)

    def _process_batch(self, items: list[tuple[int, ContentItem]]) -> None:
        for db_id, item in items:
            if self._stop_asked():
                return
            self._process_item(db_id, item)
            self._publish()

    def _process_item(self, db_id: int, item: ContentItem) -> None:
        with self._lock:
            self._status.current_item = item.title
            item_num = self._status.items_processed + 1
            total = self._status.total_items

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

        enabled_providers = self.registry.get_enabled_providers(self.config)
        matching_providers = [
            provider
            for provider in enabled_providers
            if content_type in provider.content_types
        ]

        if not matching_providers:
            logger.debug(
                "[ENRICHMENT] No providers for %s: %s", content_type_str, safe_title
            )
            self.storage_manager.enrichment.mark_complete(db_id, "none", "not_found")
            with self._lock:
                self._status.items_processed += 1
                self._status.items_not_found += 1
            return

        available_providers = [
            provider
            for provider in matching_providers
            if provider.name not in self._abandoned_providers
        ]
        if not available_providers:
            # Deliberately no write: the item keeps the status it arrived with,
            # so the run after the credential is fixed picks it up untouched.
            self._skipped_for_abandonment = True
            logger.debug(
                "[ENRICHMENT] Every provider for %s was abandoned this run: %s",
                content_type_str,
                safe_title,
            )
            return

        failures: list[_ProviderFailure] = []

        for provider in available_providers:
            try:
                limiter = self._get_rate_limiter(provider.name)
                limiter.acquire()

                provider_config = self._get_provider_config(provider.name)

                logger.debug(
                    "[ENRICHMENT] Trying %s for %s: %s",
                    provider.name,
                    content_type_str,
                    safe_title,
                )

                # The try covers the provider call and nothing else: a failed
                # save is ours, and blaming a provider re-fetched it forever.
                result = provider.enrich(item, provider_config)

            except Exception as error:
                # One branch for every exception, because a bare ValueError
                # quoting the item's title leaked through the catch-all that
                # used to sit beside the ProviderError one.
                failure = _classify_failure(provider.name, error)
                logger.warning(
                    "[ENRICHMENT] Provider %s failed: %s", provider.name, failure.reason
                )
                failures.append(failure)
                self._note_failure(failure)
                continue

            self._rejections.pop(provider.name, None)

            if result and result.match_quality != "not_found":
                try:
                    self._apply_enrichment(db_id, item, result)
                    self.storage_manager.enrichment.mark_complete(
                        db_id, provider.name, result.match_quality
                    )
                except Exception as error:
                    self._settle_storage_failure(db_id, safe_title, error)
                    return
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

            logger.debug(
                "[ENRICHMENT] %s returned not_found: %s",
                provider.name,
                safe_title,
            )

        if failures:
            reported = "; ".join(str(failure) for failure in failures)
            if any(failure.retryable for failure in failures):
                # A provider that blew up, timed out or could not be reached
                # never said whether it has this item, so "no match" is an
                # unknown rather than a settled miss.
                logger.info(
                    "[ENRICHMENT] Enrichment of %s failed, will retry: %s",
                    content_type_str,
                    safe_title,
                )
                self.storage_manager.enrichment.mark_failed(db_id, reported)
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
            logger.debug(
                "[ENRICHMENT] No match found for %s: %s", content_type_str, safe_title
            )

        self.storage_manager.enrichment.mark_complete(db_id, "none", "not_found")
        with self._lock:
            self._status.items_processed += 1
            self._status.items_not_found += 1

    def _settle_storage_failure(
        self, db_id: int, safe_title: str, error: Exception
    ) -> None:
        """Re-fetching spends a third party's quota on a failure of ours that
        repeating cannot clear.
        """
        logger.error(
            "[ENRICHMENT] Saving enrichment failed, not retrying: %s",
            safe_title,
            exc_info=True,
        )
        rendered = f"storage: {type(error).__name__}"
        self._record_error(rendered)
        self.storage_manager.enrichment.mark_settled_failure(db_id, rendered)
        with self._lock:
            self._status.items_processed += 1
            self._status.items_failed += 1

    def _note_failure(self, failure: _ProviderFailure) -> None:
        """A revoked key rejects every item alike: thousands of requests already
        answered no.
        """
        if failure.retryable:
            self._rejections.pop(failure.provider, None)
            self._record_error(str(failure))
            return
        count = self._rejections.get(failure.provider, 0) + 1
        self._rejections[failure.provider] = count
        if count == 1:
            self._record_error(str(failure))
        if count < _MAX_CONSECUTIVE_REJECTIONS:
            return
        self._abandoned_providers.add(failure.provider)
        logger.warning(
            "[ENRICHMENT] Abandoning %s for the rest of this run after %d "
            "consecutive rejections: %s",
            failure.provider,
            count,
            failure.reason,
        )
        self._record_error(
            f"{failure.provider}: abandoned for this run after {count} "
            f"consecutive rejections ({failure.reason}); "
            "the items it never reached are left queued"
        )

    def _every_provider_abandoned(self, content_type: ContentType | None) -> bool:
        if not self._abandoned_providers:
            return False
        return all(
            provider.name in self._abandoned_providers
            for provider in self.registry.get_enabled_providers(self.config)
            if content_type is None or content_type in provider.content_types
        )

    def _get_rate_limiter(self, provider_name: str) -> RateLimiter:
        if provider_name not in self._rate_limiters:
            provider = self.registry.get_provider(provider_name)
            rate = provider.rate_limit_requests_per_second if provider else 1.0
            self._rate_limiters[provider_name] = RateLimiter(requests_per_second=rate)
        return self._rate_limiters[provider_name]

    def _get_provider_config(self, provider_name: str) -> dict[str, Any]:
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
        """Enrichment resolves each provider's secrets for every content item, and
        each read is a SQLite query plus a Fernet decrypt.
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
        self.storage_manager.save_enrichment_metadata(
            db_id,
            item.model_copy(
                update={
                    "metadata": merge_enrichment(item.metadata, result),
                    "cover_url": item.cover_url or result.cover_url,
                }
            ),
        )


def merge_enrichment(
    existing_metadata: dict[str, Any],
    result: EnrichmentResult,
) -> dict[str, Any]:
    """Fills fields that are missing or empty, and replaces the provider-owned ones."""
    merged = dict(existing_metadata)

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

    if not merged.get("description") and result.description:
        merged["description"] = result.description

    for key, value in result.extra_metadata.items():
        if (
            key in PROVIDER_OWNED_METADATA_KEYS
            or key not in merged
            or merged[key] is None
            or merged[key] == ""
        ):
            merged[key] = value

    if result.external_id:
        merged["enrichment_id"] = result.external_id

    return merged
