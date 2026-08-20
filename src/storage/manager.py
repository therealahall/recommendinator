"""Unified storage manager for the SQLite library."""

from __future__ import annotations

import functools
import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
)
from src.models.user_preferences import UserPreferenceConfig
from src.storage.accounts import AccountStore, normalize_account_name
from src.storage.credentials import CredentialStore
from src.storage.duplicates import SUGGESTION_PAGE_DEFAULT as SUGGESTION_PAGE_DEFAULT
from src.storage.duplicates import SUGGESTION_PAGE_MAX as SUGGESTION_PAGE_MAX
from src.storage.duplicates import DeclinedPair as DeclinedPair
from src.storage.duplicates import DuplicateSide as DuplicateSide
from src.storage.duplicates import DuplicateSuggestion as DuplicateSuggestion
from src.storage.duplicates import SuggestionEvidence as SuggestionEvidence
from src.storage.duplicates import SuggestionPage as SuggestionPage
from src.storage.enrichment_status import EnrichmentStore
from src.storage.global_secrets import SecretStore
from src.storage.item_merges import MergeError as MergeError
from src.storage.item_merges import MergeEvidence as MergeEvidence
from src.storage.item_merges import MergeRecord as MergeRecord
from src.storage.profiles import ProfileStore
from src.storage.schema import (
    UserDict,
    get_all_users,
    get_user_by_id,
    update_user_identity,
    update_user_settings,
)
from src.storage.settings_store import SettingsStore
from src.storage.source_configs import SourceConfigStore

# Re-exported so consumers import from storage.manager rather than the
# internal sqlite_db module.  The `as <name>` form marks each one as an
# intentional public re-export for type checkers.
from src.storage.sqlite_db import UNSET as UNSET
from src.storage.sqlite_db import VALID_SORT_OPTIONS as VALID_SORT_OPTIONS
from src.storage.sqlite_db import (
    FutureCompletionDateError as FutureCompletionDateError,
)
from src.storage.sqlite_db import SaveCounts as SaveCounts
from src.storage.sqlite_db import SavedItem as SavedItem
from src.storage.sqlite_db import SaveOutcome as SaveOutcome
from src.storage.sqlite_db import SQLiteDB
from src.storage.sqlite_db import Unset as Unset
from src.storage.sqlite_db import unset_if_none as unset_if_none
from src.storage.sync_runs import SyncRunStore


class UnknownUserError(LookupError):
    """A write named a user id no ``users`` row carries."""


class StorageManager:
    """Unified storage manager for the SQLite library."""

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_db = SQLiteDB(sqlite_path)
        self._sqlite_path = sqlite_path
        # Serialises every read-then-write on this manager: each `with` site
        # below leaves a gap between read and write that WAL's concurrent
        # readers do not close, and the callers that collide there are parallel
        # sync workers and FastAPI threadpool workers alike.
        self._save_lock = threading.Lock()

    # Sub-facades, as properties rather than attributes assigned above so that
    # a ``Mock(spec=StorageManager)`` resolves them: mock reads the class.

    @functools.cached_property
    def credentials(self) -> CredentialStore:
        return CredentialStore(self.sqlite_db, self._sqlite_path, self._save_lock)

    @functools.cached_property
    def secrets(self) -> SecretStore:
        """The global settings secrets, kept in the same encrypted table."""
        return SecretStore(self.credentials)

    @functools.cached_property
    def accounts(self) -> AccountStore:
        return AccountStore(self.sqlite_db)

    @functools.cached_property
    def enrichment(self) -> EnrichmentStore:
        return EnrichmentStore(self.sqlite_db)

    @functools.cached_property
    def settings(self) -> SettingsStore:
        """The DB layer of the global config."""
        return SettingsStore(self.sqlite_db)

    @functools.cached_property
    def sources(self) -> SourceConfigStore:
        return SourceConfigStore(self.sqlite_db)

    @functools.cached_property
    def profiles(self) -> ProfileStore:
        return ProfileStore(self.sqlite_db)

    @functools.cached_property
    def sync_runs(self) -> SyncRunStore:
        return SyncRunStore(self.sqlite_db)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a managed SQLite connection."""
        with self.sqlite_db.connection() as conn:
            yield conn

    def save_content_item(self, item: ContentItem, user_id: int | None = None) -> int:
        """Save a content item, returning its database ID."""
        return self.save_content_item_outcome(item, user_id=user_id).db_id

    def save_content_item_outcome(
        self, item: ContentItem, user_id: int | None = None
    ) -> SavedItem:
        """Save a content item, reporting whether the write changed anything.

        Upsert-by-external-id and cross-source dedup are SQLiteDB's; this
        wrapper serialises that read-then-write under ``_save_lock``.
        """
        with self._save_lock:
            return self.sqlite_db.save_content_item_outcome(item, user_id=user_id)

    def save_enrichment_metadata(self, db_id: int, item: ContentItem) -> None:
        """Merge a provider's metadata into one row's detail table, by row id.

        Under ``_save_lock`` like the sync door: the merge reads the stored
        detail row and writes it back.
        """
        with self._save_lock:
            self.sqlite_db.save_enrichment_metadata(db_id, item)

    def complete_content_item(
        self, item: ContentItem, user_id: int | None = None
    ) -> int:
        """Record an explicit completion, adding the item if it is new.

        The one entry point behind the ``complete`` command and
        ``POST /api/complete``, serialised under ``_save_lock``. Raises
        ``FutureCompletionDateError``, writing nothing, for a completion date
        nobody has lived yet.
        """
        with self._save_lock:
            return self.sqlite_db.complete_content_item(item, user_id=user_id)

    def get_content_item(
        self, db_id: int, user_id: int | None = None
    ) -> ContentItem | None:
        """Get a content item by database ID."""
        return self.sqlite_db.get_content_item(db_id, user_id=user_id)

    def get_content_items_by_db_ids(self, db_ids: list[int]) -> list[ContentItem]:
        """Get multiple content items by their database IDs in a single query."""
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
        """Get content items with the given filters, ANDed together."""
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
        """Get unconsumed items (status = UNREAD or CURRENTLY_CONSUMING)."""
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
        """Get completed items (status = COMPLETED or CURRENTLY_CONSUMING)."""
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

        The one set that may shape *taste*: the user excluded ignored items,
        and an unrated completion carries no signal (issue #99). Deliberately
        narrower than :meth:`get_consumption_items`. *limit* bounds the
        completed read, before the rating filter, so a caller that passes one
        may get back fewer items than it asked for.
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

        What genre fatigue reacts to, wider than :meth:`get_signal_items`: an
        unrated completion says nothing about taste, but six fantasy novels
        read or reading are still six fantasy novels — what is in progress
        comes along too, and the variety ladder narrows that itself. *limit*
        cuts a title-sorted set, so it yields an alphabetical prefix rather
        than the most recent items. Series ordering reads the full completed
        set.
        """
        return self.get_completed_items(
            user_id=user_id,
            content_type=content_type,
            limit=limit,
            include_ignored=False,
        )

    def merge_content_items(
        self,
        survivor_id: int,
        absorbed_id: int,
        evidence: MergeEvidence,
        evidence_detail: str | None = None,
        user_id: int | None = None,
    ) -> MergeRecord:
        """Merge one item into another; raises ``MergeError`` for a refused pair."""
        with self._save_lock:
            return self.sqlite_db.merge_content_items(
                survivor_id,
                absorbed_id,
                evidence,
                evidence_detail=evidence_detail,
                user_id=user_id,
            )

    def unmerge_content_items(
        self, merge_id: int, user_id: int | None = None
    ) -> MergeRecord | None:
        """Undo one merge, newest into its survivor first; raises ``MergeError``
        for any other order, and returns ``None`` when there is no such merge."""
        with self._save_lock:
            return self.sqlite_db.unmerge_content_items(merge_id, user_id=user_id)

    def list_content_item_merges(self, user_id: int | None = None) -> list[MergeRecord]:
        """Every merge in force, naming what absorbed what and on what evidence."""
        return self.sqlite_db.list_content_item_merges(user_id=user_id)

    def list_duplicate_suggestions(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> SuggestionPage:
        """Undecided pairs of live rows that look like one work, and how many."""
        return self.sqlite_db.list_duplicate_suggestions(
            user_id=user_id, content_type=content_type, limit=limit
        )

    def decline_duplicate_suggestion(
        self, one_id: int, other_id: int, user_id: int | None = None
    ) -> DeclinedPair | None:
        """Refuse a suggested pair for good, returning it, or ``None`` if none."""
        with self._save_lock:
            return self.sqlite_db.decline_duplicate_suggestion(
                one_id, other_id, user_id=user_id
            )

    def list_declined_duplicates(
        self, user_id: int | None = None
    ) -> list[DeclinedPair]:
        """Every refusal in force, lowest id first."""
        return self.sqlite_db.list_declined_duplicates(user_id=user_id)

    def undecline_duplicate_suggestion(
        self, one_id: int, other_id: int, user_id: int | None = None
    ) -> DeclinedPair | None:
        """Lift a refusal, returning the pair, or ``None`` when none was in force."""
        with self._save_lock:
            return self.sqlite_db.undecline_duplicate_suggestion(
                one_id, other_id, user_id=user_id
            )

    def delete_content_item(self, db_id: int, user_id: int | None = None) -> bool:
        """Delete a content item, reporting whether there was one."""
        return self.sqlite_db.delete_content_item(db_id, user_id=user_id)

    def set_item_ignored(
        self, db_id: int, ignored: bool, user_id: int | None = None
    ) -> bool:
        """Ignore or un-ignore an item, keeping it out of recommendations."""
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
        """Update a content item from an explicit user action.

        The explicit-user-action door: it writes only the fields supplied and
        may overwrite them freely, without the fill-only constraints
        ``save_content_item`` applies to sync.
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
        """Count content items with optional filters."""
        return self.sqlite_db.count_items(
            user_id=user_id, content_type=content_type, status=status
        )

    def get_all_users(self) -> list[UserDict]:
        """Get all users, ordered by id."""
        with self.sqlite_db.connection() as conn:
            return get_all_users(conn)

    def update_user_identity(
        self, user_id: int, username: str, display_name: str | None
    ) -> UserDict:
        """Rename a user, returning the renamed row.

        Normalized here: ``schema.py`` cannot import ``accounts`` without a
        cycle. Raises ``AccountNameError`` for a blank or over-long username,
        ``UnknownUserError`` when nobody carries *user_id*, and
        ``sqlite3.IntegrityError`` when another user holds *username*.
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

    def get_user_preference_config(self, user_id: int) -> UserPreferenceConfig:
        """Load the user's preference config, or defaults when none is stored."""
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
        """Save the user's preference config, merged into ``users.settings``
        without clobbering the other keys there. Raises
        ``PreferenceValidationError`` for a config no later read survives, and
        ``UnknownUserError`` when nobody carries *user_id*."""
        with self._save_lock:
            self._write_preference_config(user_id, preference_config)

    def _write_preference_config(
        self, user_id: int, preference_config: UserPreferenceConfig
    ) -> None:
        """The one site every preference write passes through: a rule enforced
        here closes the CLI's door and the API's at once."""
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
        write's ``users.settings`` blob. *apply* edits the config in place, and
        must not call another ``_save_lock`` writer: the lock is not reentrant,
        so that wedges the worker for good, silently. Raises what
        ``save_user_preference_config`` raises.
        """
        with self._save_lock:
            preference_config = self.get_user_preference_config(user_id)
            apply(preference_config)
            self._write_preference_config(user_id, preference_config)
            return preference_config
