"""Unified storage manager for the SQLite library."""

from __future__ import annotations

import functools
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
)
from src.models.user_preferences import UserPreferenceConfig
from src.storage.accounts import (
    AccountRecord,
    account_is_claimed,
    claim_account,
    create_session,
    describe_account,
    lookup_session,
    normalize_account_name,
    purge_expired_sessions,
    revoke_all_sessions,
    revoke_other_sessions,
    revoke_session,
    set_password,
    verify_password,
)
from src.storage.global_secrets import GLOBAL_SECRET_USER_ID, secret_ref
from src.storage.schema import (
    EnrichmentStatusDict,
    SourceConfigDict,
    UserDict,
    credential_row_exists,
    delete_credential,
    delete_credentials_for_source,
    delete_setting,
    delete_source_config,
    get_all_users,
    get_credential,
    get_credentials_for_source,
    get_enrichment_stats,
    get_enrichment_status,
    get_preference_profile,
    get_setting,
    get_source_config,
    get_user_by_id,
    list_settings,
    list_source_configs,
    mark_enrichment_complete,
    mark_enrichment_failed,
    mark_item_needs_enrichment,
    reset_enrichment_status,
    save_credential,
    save_preference_profile,
    set_setting,
    set_source_config_enabled,
    update_user_identity,
    update_user_settings,
    upsert_source_config,
)

# Re-exported so consumers import from storage.manager rather than the
# internal sqlite_db module.  The `as <name>` form marks each one as an
# intentional public re-export for type checkers.
from src.storage.sqlite_db import UNSET as UNSET
from src.storage.sqlite_db import VALID_SORT_OPTIONS as VALID_SORT_OPTIONS
from src.storage.sqlite_db import (
    FutureCompletionDateError as FutureCompletionDateError,
)
from src.storage.sqlite_db import SQLiteDB
from src.storage.sqlite_db import Unset as Unset
from src.storage.sqlite_db import unset_if_none as unset_if_none

if TYPE_CHECKING:
    from src.storage.encryption import CredentialEncryptor

logger = logging.getLogger(__name__)


class UnknownUserError(LookupError):
    """A write named a user id no ``users`` row carries."""


class StorageManager:
    """Unified storage manager for the SQLite library."""

    def __init__(self, sqlite_path: Path) -> None:
        """Initialize storage manager.

        Args:
            sqlite_path: Path to SQLite database file
        """
        self.sqlite_db = SQLiteDB(sqlite_path)
        self._credential_key_path = self._resolve_key_path(sqlite_path)
        # Serialises every read-then-write on this manager: each `with` site
        # below leaves a gap between read and write that WAL's concurrent
        # readers do not close, and the callers that collide there are parallel
        # sync workers and FastAPI threadpool workers alike.
        self._save_lock = threading.Lock()

    @staticmethod
    def _resolve_key_path(sqlite_path: Path) -> Path:
        """Determine the credential encryption key file path.

        Uses the ``RECOMMENDINATOR_KEY_PATH`` environment variable if set,
        otherwise defaults to the same directory as the SQLite database.
        Co-locating the key with the database ensures both survive container
        restarts when ``data/`` is on a persistent volume.

        Operators who want key-database separation (e.g., for backup
        isolation) can set ``RECOMMENDINATOR_KEY_PATH`` to a separate path.

        Args:
            sqlite_path: Path to the SQLite database file.

        Returns:
            Resolved Path for the key file.
        """
        env_path = os.environ.get("RECOMMENDINATOR_KEY_PATH")
        if env_path:
            return Path(env_path)
        return Path(sqlite_path).parent / ".credential_key"

    @functools.cached_property
    def _encryptor(self) -> CredentialEncryptor:
        """Lazy-loaded credential encryptor.

        Deferred so that StorageManager construction does not touch the
        filesystem or import cryptography until credentials are accessed.
        """
        from src.storage.encryption import CredentialEncryptor

        return CredentialEncryptor(self._credential_key_path)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a managed SQLite connection.

        Delegates to the underlying SQLiteDB connection context manager.
        """
        with self.sqlite_db.connection() as conn:
            yield conn

    def save_content_item(self, item: ContentItem, user_id: int | None = None) -> int:
        """Save a content item.

        Upsert-by-external-id and cross-source dedup by normalized title are
        both handled by :meth:`SQLiteDB.save_content_item`; this wrapper
        serialises that read-then-write under ``_save_lock``.

        Args:
            item: ContentItem to save
            user_id: User ID (defaults to item.user_id)

        Returns:
            Database ID of the saved item
        """
        with self._save_lock:
            return self.sqlite_db.save_content_item(item, user_id=user_id)

    def complete_content_item(
        self, item: ContentItem, user_id: int | None = None
    ) -> int:
        """Record an explicit completion, adding the item if it is new.

        The single entry point behind every completion — the ``complete`` CLI
        command and ``POST /api/complete``:
        :meth:`SQLiteDB.complete_content_item` finds or creates the row and
        applies the user's rating, review and completion date in one
        transaction, and this wrapper serialises it under ``_save_lock``.

        Args:
            item: ContentItem being completed
            user_id: User ID (defaults to item.user_id)

        Returns:
            Database ID of the completed item

        Raises:
            FutureCompletionDateError: ``item.date_completed`` is a day nobody
                has lived yet. Nothing is written.
        """
        with self._save_lock:
            return self.sqlite_db.complete_content_item(item, user_id=user_id)

    def get_content_item(
        self, db_id: int, user_id: int | None = None
    ) -> ContentItem | None:
        """Get a content item by database ID.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter

        Returns:
            ContentItem if found, None otherwise
        """
        return self.sqlite_db.get_content_item(db_id, user_id=user_id)

    def get_content_items_by_db_ids(self, db_ids: list[int]) -> list[ContentItem]:
        """Get multiple content items by their database IDs in a single query.

        Args:
            db_ids: List of database IDs to fetch

        Returns:
            List of ContentItem objects found
        """
        return self.sqlite_db.get_content_items_by_db_ids(db_ids)

    def get_content_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | list[ConsumptionStatus] | None = None,
        min_rating: int | None = None,
        unrated_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
        sort_by: str = "title",
        include_ignored: bool = True,
        enrichment: EnrichmentFilter | None = None,
        search: str | None = None,
    ) -> list[ContentItem]:
        """Get content items with optional filters.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            status: Filter by consumption status (single value or list for
                IN-clause filtering)
            min_rating: Minimum rating (inclusive)
            unrated_only: When True, only return items with no rating set
                (rating IS NULL)
            limit: Maximum number of results
            offset: Number of results to skip (for pagination)
            sort_by: Sort order - "title" (default, ignores articles),
                "updated_at", "rating", or "created_at"
            include_ignored: Whether to include ignored items (default True
                for backward compatibility)
            enrichment: Filter by enrichment state ("enriched" or
                "not_enriched"). None returns all items.
            search: Optional search term matched against title and creator

        Returns:
            List of ContentItem objects
        """
        return self.sqlite_db.get_content_items(
            user_id=user_id,
            content_type=content_type,
            status=status,
            min_rating=min_rating,
            unrated_only=unrated_only,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            include_ignored=include_ignored,
            enrichment=enrichment,
            search=search,
        )

    def get_unconsumed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        """Get unconsumed items (status = UNREAD or CURRENTLY_CONSUMING).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            limit: Maximum number of results
            include_ignored: Whether to include ignored items (default True)

        Returns:
            List of unconsumed ContentItem objects
        """
        return self.sqlite_db.get_unconsumed_items(
            user_id=user_id,
            content_type=content_type,
            limit=limit,
            include_ignored=include_ignored,
        )

    def get_completed_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        min_rating: int | None = None,
        limit: int | None = None,
        include_ignored: bool = True,
    ) -> list[ContentItem]:
        """Get completed items (status = COMPLETED or CURRENTLY_CONSUMING).

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            min_rating: Minimum rating (inclusive)
            limit: Maximum number of results
            include_ignored: Whether to include ignored items (default True)

        Returns:
            List of completed ContentItem objects
        """
        return self.sqlite_db.get_completed_items(
            user_id=user_id,
            content_type=content_type,
            min_rating=min_rating,
            limit=limit,
            include_ignored=include_ignored,
        )

    def get_signal_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get taste-signal items: completed, rated, and not ignored.

        This is the single source of truth for the set of items that may shape
        *taste* — preference analysis, scoring, similarity seeds, and
        explanation references. Ignored items are excluded by the user, and
        completed-but-unrated items carry no taste signal, so neither may shape
        recommendations (issue #99). The "not ignored" constraint is expressed
        once, via the SQL ``include_ignored=False`` predicate; only the rating
        floor is applied in Python because it has no dedicated query parameter.

        This is deliberately distinct from two consumption fetches. Series
        ordering uses the full completed set, because whether the user has
        *consumed* an earlier entry is independent of rating and ignore state.
        Genre fatigue uses :meth:`get_consumption_items`, because finishing
        something causes fatigue whether or not the user rated it.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            limit: Maximum number of completed (non-ignored) items to consider.
                Applied by ``get_completed_items`` before the Python rating
                filter, so a caller passing ``limit`` may receive fewer than
                ``limit`` signal items when some are unrated.

        Returns:
            List of completed, rated, non-ignored ContentItem objects
        """
        completed = self.get_completed_items(
            user_id=user_id,
            content_type=content_type,
            limit=limit,
            include_ignored=False,
        )
        return [item for item in completed if item.rating is not None]

    def get_consumption_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> list[ContentItem]:
        """Get consumption items: non-ignored, rating irrelevant.

        What the user has actually consumed, which is what genre fatigue reacts
        to. Deliberately wider than :meth:`get_signal_items`: an unrated
        completion says nothing about taste, but a user who finishes six
        fantasy novels has still had six fantasy novels. Ignored items stay out
        of both — ignoring something says the user wants less of it, so letting
        it claim a variety-ladder rung would invert that.

        Inherits :meth:`get_completed_items`' status set, so items the user is
        *currently consuming* are included alongside finished ones. The variety
        ladder narrows that itself, counting an in-progress item only when it is
        an ongoing show with a finished season; a caller wanting finished-only
        must filter for it.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            limit: Maximum number of results, applied by
                ``get_completed_items`` over a title-sorted set — so a caller
                bounding a recency-based read gets an alphabetical prefix, not
                the most recent items.

        Returns:
            List of non-ignored completed and in-progress ContentItem objects
        """
        return self.get_completed_items(
            user_id=user_id,
            content_type=content_type,
            limit=limit,
            include_ignored=False,
        )

    def delete_content_item(self, db_id: int, user_id: int | None = None) -> bool:
        """Delete a content item.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter

        Returns:
            True if item was deleted, False if not found
        """
        return self.sqlite_db.delete_content_item(db_id, user_id=user_id)

    def set_item_ignored(
        self, db_id: int, ignored: bool, user_id: int | None = None
    ) -> bool:
        """Set the ignored status of a content item.

        Ignored items are excluded from recommendations.

        Args:
            db_id: Database ID of the item
            ignored: Whether the item should be ignored
            user_id: Optional user ID filter (for security)

        Returns:
            True if item was updated, False if not found
        """
        return self.sqlite_db.set_item_ignored(db_id, ignored, user_id=user_id)

    def update_item_from_ui(
        self,
        db_id: int,
        status: str | Unset = UNSET,
        rating: int | None | Unset = UNSET,
        review: str | None | Unset = UNSET,
        seasons_watched: list[int] | None = None,
        genres: list[str] | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
        user_id: int | None = None,
    ) -> bool:
        """Update a content item from an explicit user action (unrestricted).

        Delegates to SQLiteDB.update_item_from_ui, the explicit-user-action
        door: it writes only the fields the caller supplied and may overwrite
        them freely, without the fill-only constraints save_content_item
        applies to sync.

        Args:
            db_id: Database ID of the item to update.
            status: New status value, UNSET to leave unchanged. For a TV show
                it also derives the season list, and is derived from it: see
                SQLiteDB.update_item_from_ui.
            rating: New rating (1-5), None to clear, UNSET to leave unchanged.
            review: New review text, None or blank to clear, UNSET to leave
                unchanged.
            seasons_watched: List of watched season numbers (TV shows only).
            genres: Manual genres to set (overwrite). None leaves them as-is.
            tags: Manual tags to set (overwrite). None leaves them as-is.
            description: Manual description to set. None leaves it as-is.
            user_id: Optional user ID filter for authorization.

        Returns:
            True if item was updated, False if not found.
        """
        return self.sqlite_db.update_item_from_ui(
            db_id=db_id,
            status=status,
            rating=rating,
            review=review,
            seasons_watched=seasons_watched,
            genres=genres,
            tags=tags,
            description=description,
            user_id=user_id,
        )

    def count_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
    ) -> int:
        """Count content items with optional filters.

        Args:
            user_id: Filter by user ID
            content_type: Filter by content type
            status: Filter by consumption status

        Returns:
            Number of matching items
        """
        return self.sqlite_db.count_items(
            user_id=user_id, content_type=content_type, status=status
        )

    def get_content_item_by_external_id(
        self,
        external_id: str,
        content_type: ContentType,
        user_id: int | None = None,
    ) -> ContentItem | None:
        """Get a content item by external ID and content type.

        Args:
            external_id: External ID from source
            content_type: Content type
            user_id: Filter by user ID

        Returns:
            ContentItem if found, None otherwise
        """
        return self.sqlite_db.get_content_item_by_external_id(
            external_id=external_id,
            content_type=content_type,
            user_id=user_id,
        )

    def get_all_users(self) -> list[UserDict]:
        """Get all users.

        Returns:
            List of user dicts ordered by id.
        """
        with self.sqlite_db.connection() as conn:
            return get_all_users(conn)

    def update_user_identity(
        self, user_id: int, username: str, display_name: str | None
    ) -> UserDict:
        """Rename a user, returning the renamed row.

        Normalized here: ``schema.py`` cannot import ``accounts`` without a cycle.

        Raises:
            AccountNameError: The username is blank or over-long.
            UnknownUserError: Nobody carries *user_id*; nothing was written.
            sqlite3.IntegrityError: Another user holds *username*.
        """
        with self.sqlite_db.connection() as conn:
            renamed = update_user_identity(
                conn,
                user_id,
                normalize_account_name(username, required=True),
                normalize_account_name(display_name, required=False) or None,
            )
        if renamed is None:
            raise UnknownUserError(f"No user with id {user_id}.")
        return renamed

    # Account and session methods

    def account_is_claimed(self) -> bool:
        """Report whether anyone has set a password on this instance."""
        with self.sqlite_db.connection() as conn:
            return account_is_claimed(conn)

    def describe_account(self, user_id: int) -> AccountRecord | None:
        """Report a user's names and the state of its password, or None."""
        with self.sqlite_db.connection() as conn:
            return describe_account(conn, user_id)

    def claim_account(
        self, username: str, display_name: str | None, plaintext_password: str
    ) -> UserDict:
        """Claim the instance: name the account and give it a password.

        Raises:
            AccountAlreadyClaimedError: The account already has a password.
            PasswordTooShortError: The password is under the floor.
        """
        with self.sqlite_db.connection() as conn:
            return claim_account(conn, username, display_name, plaintext_password)

    def set_password(self, user_id: int, plaintext: str) -> None:
        """Replace a user's password.

        Raises:
            PasswordTooShortError: The password is under the floor.
        """
        with self.sqlite_db.connection() as conn:
            set_password(conn, user_id, plaintext)

    def verify_password(self, username: str, plaintext: str) -> UserDict | None:
        """Return the user *plaintext* logs *username* in as, or None."""
        with self.sqlite_db.connection() as conn:
            return verify_password(conn, username, plaintext)

    def create_session(self, user_id: int) -> str:
        """Open a session and return its token, the only copy in plaintext."""
        with self.sqlite_db.connection() as conn:
            return create_session(conn, user_id)

    def lookup_session(self, token: str) -> UserDict | None:
        """Return the user *token* is signed in as, extending the session."""
        with self.sqlite_db.connection() as conn:
            return lookup_session(conn, token)

    def revoke_session(self, token: str) -> None:
        """End one session."""
        with self.sqlite_db.connection() as conn:
            revoke_session(conn, token)

    def revoke_other_sessions(self, user_id: int, keep_token: str) -> None:
        """End every session a user holds but the one making the request."""
        with self.sqlite_db.connection() as conn:
            revoke_other_sessions(conn, user_id, keep_token)

    def revoke_all_sessions(self, user_id: int) -> None:
        """End every session a user holds, on every device."""
        with self.sqlite_db.connection() as conn:
            revoke_all_sessions(conn, user_id)

    def purge_expired_sessions(self) -> int:
        """Delete the lapsed sessions, returning how many were deleted."""
        with self.sqlite_db.connection() as conn:
            return purge_expired_sessions(conn)

    def get_user_preference_config(self, user_id: int) -> UserPreferenceConfig:
        """Load user preference config from DB.

        Returns defaults if no preference config is stored for the user.

        Args:
            user_id: User ID to look up.

        Returns:
            UserPreferenceConfig for the user.
        """
        with self.sqlite_db.connection() as conn:
            user = get_user_by_id(conn, user_id)
            if user is not None:
                settings = user["settings"]
                if settings and "preference_config" in settings:
                    return UserPreferenceConfig.from_dict(settings["preference_config"])
            return UserPreferenceConfig()

    def save_user_preference_config(
        self, user_id: int, preference_config: UserPreferenceConfig
    ) -> None:
        """Save user preference config to DB.

        Merges into the ``users.settings`` blob under ``"preference_config"``
        without clobbering other settings.

        Raises:
            PreferenceValidationError: A config no later read survives.
            UnknownUserError: No user carries *user_id*; nothing was written.
        """
        with self._save_lock:
            self._write_preference_config(user_id, preference_config)

    def _write_preference_config(
        self, user_id: int, preference_config: UserPreferenceConfig
    ) -> None:
        """The one site every preference write passes through.

        Both interfaces reach it, so a rule enforced here closes the CLI's
        door and the API's at once rather than once per command.
        """
        preference_config.raise_if_unstorable()
        with self.sqlite_db.connection() as conn:
            written = update_user_settings(
                conn, user_id, {"preference_config": preference_config.to_dict()}
            )
        if not written:
            raise UnknownUserError(f"No user with id {user_id}.")

    def merge_user_preference_config(
        self, user_id: int, apply: Callable[[UserPreferenceConfig], None]
    ) -> UserPreferenceConfig:
        """Apply *apply* to the user's preferences and save the result.

        Read, edit and write as one operation: separate calls lose a concurrent
        write's ``users.settings`` blob.

        Args:
            user_id: User ID.
            apply: Edits the config in place. Must not call another
                ``_save_lock`` writer: the lock is not reentrant, so that
                wedges the worker for good, silently.

        Returns:
            The saved config.

        Raises:
            PreferenceValidationError: See ``save_user_preference_config``.
            UnknownUserError: See ``save_user_preference_config``.
        """
        with self._save_lock:
            preference_config = self.get_user_preference_config(user_id)
            apply(preference_config)
            self._write_preference_config(user_id, preference_config)
            return preference_config

    # Enrichment status methods

    def get_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
        limit: int = 100,
        include_not_found: bool = False,
        after_db_id: int | None = None,
    ) -> list[tuple[int, ContentItem]]:
        """Get content items that need enrichment.

        Returns items where no enrichment_status record exists (new items)
        or where needs_enrichment = TRUE.

        Args:
            content_type: Optional filter by content type
            user_id: Filter by user ID
            limit: Maximum number of items to return
            include_not_found: Also include items previously marked as not_found
            after_db_id: Only return items with a database ID above this one,
                for callers paging forward through the queue.

        Returns:
            List of (db_id, ContentItem) tuples
        """
        return self.sqlite_db.get_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
            limit=limit,
            include_not_found=include_not_found,
            after_db_id=after_db_id,
        )

    def count_items_needing_enrichment(
        self,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> int:
        """Count content items that need enrichment.

        Args:
            content_type: Optional filter by content type
            user_id: Filter by user ID (defaults to the default user when None)

        Returns:
            Number of items matching the enrichment filter.
        """
        return self.sqlite_db.count_items_needing_enrichment(
            content_type=content_type,
            user_id=user_id,
        )

    def get_enrichment_status(
        self, content_item_id: int
    ) -> EnrichmentStatusDict | None:
        """Get enrichment status for a content item.

        Args:
            content_item_id: Content item database ID

        Returns:
            Enrichment status dict or None if not found
        """
        with self.sqlite_db.connection() as conn:
            return get_enrichment_status(conn, content_item_id)

    def mark_enrichment_complete(
        self,
        content_item_id: int,
        provider: str,
        quality: str,
    ) -> None:
        """Mark an item as successfully enriched.

        Args:
            content_item_id: Content item database ID
            provider: Name of the provider that enriched the item
            quality: Match quality ("high", "medium", "not_found")
        """
        with self.sqlite_db.connection() as conn:
            mark_enrichment_complete(conn, content_item_id, provider, quality)

    def mark_enrichment_failed(
        self,
        content_item_id: int,
        error: str,
    ) -> None:
        """Mark an item's enrichment as failed, leaving it queued for retry.

        A failure is an unknown outcome, not a settled miss, so the item stays
        in the enrichment queue and the next run tries it again.

        Args:
            content_item_id: Content item database ID
            error: Error message describing the failure
        """
        with self.sqlite_db.connection() as conn:
            mark_enrichment_failed(conn, content_item_id, error)

    def mark_item_needs_enrichment(self, content_item_id: int) -> None:
        """Mark an item as needing enrichment.

        Args:
            content_item_id: Content item database ID
        """
        with self.sqlite_db.connection() as conn:
            mark_item_needs_enrichment(conn, content_item_id)

    def reset_enrichment_status(
        self,
        provider: str | None = None,
        content_type: ContentType | None = None,
        user_id: int | None = None,
    ) -> int:
        """Reset enrichment status for items to allow re-enrichment.

        Args:
            provider: If specified, only reset items enriched by this provider.
                      If None, reset all items.
            content_type: If specified, only reset items of this content type.
            user_id: If specified, only reset items for this user.

        Returns:
            Number of items reset
        """
        with self.sqlite_db.connection() as conn:
            content_type_str = content_type.value if content_type else None
            return reset_enrichment_status(conn, provider, content_type_str, user_id)

    def get_enrichment_stats(
        self, user_id: int | None = None
    ) -> dict[str, int | dict[str, int]]:
        """Get overall enrichment statistics.

        Args:
            user_id: If specified, only count items for this user.

        Returns:
            Dict with enrichment statistics including:
            - total: Total content items
            - enriched: Successfully enriched items
            - pending: Items queued whose last attempt did not error
            - not_found: Items where no match was found
            - failed: Items whose last attempt errored (queued for retry)
            - by_provider: Breakdown by provider
            - by_quality: Breakdown by match quality
        """
        with self.sqlite_db.connection() as conn:
            return get_enrichment_stats(conn, user_id)

    def get_content_item_db_id(
        self,
        external_id: str,
        content_type: ContentType,
        user_id: int | None = None,
    ) -> int | None:
        """Get the database ID of a content item by external ID.

        Args:
            external_id: External ID from source
            content_type: Content type
            user_id: Filter by user ID

        Returns:
            Database ID if found, None otherwise
        """
        return self.sqlite_db.get_content_item_db_id(
            external_id=external_id,
            content_type=content_type,
            user_id=user_id,
        )

    # Preference profile methods

    def get_preference_profile(self, user_id: int) -> dict | None:
        """Get the preference profile for a user.

        Args:
            user_id: User ID

        Returns:
            Profile dict or None if not found
        """
        with self.sqlite_db.connection() as conn:
            return get_preference_profile(conn, user_id)

    def save_preference_profile(self, user_id: int, profile_json: str) -> int:
        """Save or update a preference profile.

        Args:
            user_id: User ID
            profile_json: JSON string of the profile

        Returns:
            Profile ID
        """
        with self.sqlite_db.connection() as conn:
            return save_preference_profile(conn, user_id, profile_json)

    # Credential methods (encrypted at rest)

    def get_credential(self, user_id: int, source_id: str, key: str) -> str | None:
        """Get a decrypted credential value.

        Args:
            user_id: User ID.
            source_id: Source identifier (e.g. "gog").
            key: Credential field name (e.g. "refresh_token").

        Returns:
            Decrypted plaintext value, or None if not found.
        """
        with self.sqlite_db.connection() as conn:
            encrypted = get_credential(conn, user_id, source_id, key)
        if encrypted is None:
            return None
        from cryptography.fernet import InvalidToken

        try:
            return self._encryptor.decrypt(encrypted)
        except InvalidToken:
            logger.error(
                "Failed to decrypt credential for source=%s key=%s — "
                "possible key mismatch or data corruption",
                source_id,
                key,
            )
            return None

    def save_credential(
        self, user_id: int, source_id: str, key: str, value: str
    ) -> None:
        """Encrypt and save a credential value.

        Args:
            user_id: User ID.
            source_id: Source identifier.
            key: Credential field name.
            value: Plaintext value to encrypt and store.
        """
        encrypted = self._encryptor.encrypt(value)
        with self._save_lock, self.sqlite_db.connection() as conn:
            save_credential(conn, user_id, source_id, key, encrypted)

    def get_credentials_for_source(
        self, user_id: int, source_id: str
    ) -> dict[str, str]:
        """Get all decrypted credentials for a source.

        Args:
            user_id: User ID.
            source_id: Source identifier.

        Returns:
            Dict mapping credential key to decrypted plaintext value.
        """
        with self.sqlite_db.connection() as conn:
            encrypted_map = get_credentials_for_source(conn, user_id, source_id)
        from cryptography.fernet import InvalidToken

        result: dict[str, str] = {}
        for k, v in encrypted_map.items():
            try:
                result[k] = self._encryptor.decrypt(v)
            except InvalidToken:
                logger.error(
                    "Failed to decrypt credential key=%s for source=%s",
                    k,
                    source_id,
                )
        return result

    def credential_row_exists(self, user_id: int, source_id: str, key: str) -> bool:
        """Check if a credential row exists in the DB (without decrypting).

        Args:
            user_id: User ID.
            source_id: Source identifier.
            key: Credential field name.

        Returns:
            True if a row exists in the credentials table.
        """
        with self.sqlite_db.connection() as conn:
            return credential_row_exists(conn, user_id, source_id, key)

    def delete_credential(self, user_id: int, source_id: str, key: str) -> bool:
        """Delete a credential row.

        Args:
            user_id: User ID.
            source_id: Source identifier.
            key: Credential field name.

        Returns:
            True if a row was deleted, False if not found.
        """
        with self.sqlite_db.connection() as conn:
            return delete_credential(conn, user_id, source_id, key)

    def delete_credentials_for_source(self, user_id: int, source_id: str) -> int:
        """Delete every stored credential for a source.

        Keyed by source, not by a plugin's current schema: an unregistered
        plugin or a no-longer-sensitive field must not leave a row behind.

        Returns:
            Number of credential rows deleted.
        """
        with self.sqlite_db.connection() as conn:
            return delete_credentials_for_source(conn, user_id, source_id)

    # Global secret methods (encrypted; write-only surface for settings UI/CLI)

    def set_global_secret(self, key: str, value: str) -> None:
        """Encrypt and store a global settings secret by its registry key.

        Routes through the encrypted ``credentials`` table under the reserved
        ``settings:`` namespace (see :mod:`src.storage.global_secrets`).

        Args:
            key: Dotted registry leaf key (e.g. ``enrichment.providers.tmdb.api_key``).
            value: Plaintext secret to encrypt and store.
        """
        source_id, credential_key = secret_ref(key)
        self.save_credential(GLOBAL_SECRET_USER_ID, source_id, credential_key, value)

    def clear_global_secret(self, key: str) -> bool:
        """Delete a global settings secret.

        Args:
            key: Dotted registry leaf key.

        Returns:
            True if a stored secret was removed, False if none existed.
        """
        source_id, credential_key = secret_ref(key)
        return self.delete_credential(GLOBAL_SECRET_USER_ID, source_id, credential_key)

    def has_global_secret(self, key: str) -> bool:
        """Return True when a global settings secret is stored (no decryption).

        Args:
            key: Dotted registry leaf key.

        Returns:
            True if a credential row exists for the secret.
        """
        source_id, credential_key = secret_ref(key)
        return self.credential_row_exists(
            GLOBAL_SECRET_USER_ID, source_id, credential_key
        )

    # Source config methods (DB-backed per-source config after migration)

    @staticmethod
    def _row_to_source_config_dict(row: Any) -> SourceConfigDict:
        return SourceConfigDict(
            source_id=row["source_id"],
            plugin=row["plugin"],
            config=json.loads(row["config_json"]),
            enabled=bool(row["enabled"]),
            migrated_at=row["migrated_at"],
            updated_at=row["updated_at"],
        )

    def get_source_config(
        self, user_id: int, source_id: str
    ) -> SourceConfigDict | None:
        """Return the migrated source config dict, or ``None`` if not migrated."""
        with self.sqlite_db.connection() as conn:
            row = get_source_config(conn, user_id, source_id)
        return self._row_to_source_config_dict(row) if row else None

    def upsert_source_config(
        self,
        user_id: int,
        source_id: str,
        plugin: str,
        config: dict[str, Any],
        enabled: bool = True,
    ) -> None:
        """Insert or update a migrated source config.

        Serialises *config* to JSON. Sensitive values must NOT be passed in
        *config* — they live in the encrypted ``credentials`` table.
        """
        config_json = json.dumps(config, sort_keys=True)
        with self.sqlite_db.connection() as conn:
            upsert_source_config(conn, user_id, source_id, plugin, config_json, enabled)

    def set_source_config_enabled(
        self, user_id: int, source_id: str, enabled: bool
    ) -> bool:
        """Toggle enabled flag on an already-migrated source.

        Returns ``True`` when a row was updated, ``False`` when the source
        has not been migrated yet.
        """
        with self.sqlite_db.connection() as conn:
            return set_source_config_enabled(conn, user_id, source_id, enabled)

    def delete_source_config(self, user_id: int, source_id: str) -> bool:
        """Remove a migrated source config row. Returns True when deleted."""
        with self.sqlite_db.connection() as conn:
            return delete_source_config(conn, user_id, source_id)

    def list_source_configs(self, user_id: int) -> list[SourceConfigDict]:
        """Return every migrated source config for a user."""
        with self.sqlite_db.connection() as conn:
            rows = list_source_configs(conn, user_id)
        return [self._row_to_source_config_dict(row) for row in rows]

    # Settings methods (global/system config, JSON-encoded values)

    def get_setting(self, key: str) -> Any | None:
        """Return the decoded value for a settings key, or ``None`` if unset.

        Returns ``None`` for BOTH a missing key and a stored null value; a
        stored null is still returned by ``list_settings``.
        """
        with self.sqlite_db.connection() as conn:
            value_json = get_setting(conn, key)
        return json.loads(value_json) if value_json is not None else None

    def set_setting(self, key: str, value: Any) -> None:
        """JSON-encode and persist a settings value (UPSERT)."""
        value_json = json.dumps(value, sort_keys=True)
        with self.sqlite_db.connection() as conn:
            set_setting(conn, key, value_json)

    def list_settings(self) -> dict[str, Any]:
        """Return every stored setting as a key -> decoded value mapping."""
        with self.sqlite_db.connection() as conn:
            raw = list_settings(conn)
        return {key: json.loads(value_json) for key, value_json in raw.items()}

    def delete_setting(self, key: str) -> None:
        """Delete a settings leaf so it falls back to the YAML/const layers.

        No-op when the key is not stored — used to reset a leaf to default.
        """
        with self.sqlite_db.connection() as conn:
            delete_setting(conn, key)
