from __future__ import annotations

import functools
import sqlite3
import threading
from collections.abc import Callable, Generator, Sequence
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
from src.storage.cover_jobs import CoverBackfillStore
from src.storage.credentials import CredentialStore
from src.storage.duplicates import MAX_DECLINE_OTHERS as MAX_DECLINE_OTHERS
from src.storage.duplicates import SUGGESTION_PAGE_DEFAULT as SUGGESTION_PAGE_DEFAULT
from src.storage.duplicates import SUGGESTION_PAGE_MAX as SUGGESTION_PAGE_MAX
from src.storage.duplicates import DeclinedPair as DeclinedPair
from src.storage.duplicates import DuplicateSide as DuplicateSide
from src.storage.duplicates import DuplicateSuggestion as DuplicateSuggestion
from src.storage.duplicates import SuggestionEvidence as SuggestionEvidence
from src.storage.duplicates import SuggestionPage as SuggestionPage
from src.storage.enrichment_jobs import EnrichmentJobStore
from src.storage.enrichment_status import EnrichmentStore
from src.storage.global_secrets import SecretStore
from src.storage.item_merges import MergeError as MergeError
from src.storage.item_merges import MergeEvidence as MergeEvidence
from src.storage.item_merges import MergeRecord as MergeRecord
from src.storage.profiles import ProfileStore
from src.storage.schema import UnknownUserError as UnknownUserError
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
from src.storage.sqlite_db import UncorrectableFieldError as UncorrectableFieldError
from src.storage.sqlite_db import Unset as Unset
from src.storage.sqlite_db import unset_if_none as unset_if_none
from src.storage.sync_runs import SyncRunStore
from src.storage.ui_settings import UiSettingsStore


class StorageManager:
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
        return SecretStore(self.credentials)

    @functools.cached_property
    def accounts(self) -> AccountStore:
        return AccountStore(self.sqlite_db)

    @functools.cached_property
    def enrichment(self) -> EnrichmentStore:
        return EnrichmentStore(self.sqlite_db)

    @functools.cached_property
    def enrichment_jobs(self) -> EnrichmentJobStore:
        return EnrichmentJobStore(self.sqlite_db)

    @functools.cached_property
    def cover_jobs(self) -> CoverBackfillStore:
        return CoverBackfillStore(self.sqlite_db)

    @functools.cached_property
    def settings(self) -> SettingsStore:
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

    @functools.cached_property
    def ui_settings(self) -> UiSettingsStore:
        return UiSettingsStore(self.sqlite_db)

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        with self.sqlite_db.connection() as conn:
            yield conn

    def save_content_item(self, item: ContentItem, user_id: int | None = None) -> int:
        return self.save_content_item_outcome(item, user_id=user_id).db_id

    def save_content_item_outcome(
        self, item: ContentItem, user_id: int | None = None
    ) -> SavedItem:
        with self._save_lock:
            return self.sqlite_db.save_content_item_outcome(item, user_id=user_id)

    def save_enrichment_metadata(self, db_id: int, item: ContentItem) -> None:
        with self._save_lock:
            self.sqlite_db.save_enrichment_metadata(db_id, item)

    def clear_cover_url(self, db_id: int) -> bool:
        with self._save_lock:
            return self.sqlite_db.clear_cover_url(db_id)

    def complete_content_item(
        self, item: ContentItem, user_id: int | None = None
    ) -> int:
        """Raises ``FutureCompletionDateError``, writing nothing, for a completion
        date nobody has lived yet.
        """
        with self._save_lock:
            return self.sqlite_db.complete_content_item(item, user_id=user_id)

    def get_content_item(
        self, db_id: int, user_id: int | None = None
    ) -> ContentItem | None:
        return self.sqlite_db.get_content_item(db_id, user_id=user_id)

    def get_content_items_by_db_ids(self, db_ids: list[int]) -> list[ContentItem]:
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
        """*limit* bounds the completed read, before the rating filter, so a
        caller that passes one may get back fewer items than it asked for.
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
        """*limit* cuts a title-sorted set, so it yields an alphabetical prefix
        rather than the most recent items.
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
        return self.sqlite_db.list_content_item_merges(user_id=user_id)

    def list_duplicate_suggestions(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        limit: int | None = None,
    ) -> SuggestionPage:
        return self.sqlite_db.list_duplicate_suggestions(
            user_id=user_id, content_type=content_type, limit=limit
        )

    def decline_duplicate_suggestion(
        self, one_id: int, other_ids: Sequence[int], user_id: int | None = None
    ) -> list[DeclinedPair]:
        with self._save_lock:
            return self.sqlite_db.decline_duplicate_suggestion(
                one_id, other_ids, user_id=user_id
            )

    def list_declined_duplicates(
        self, user_id: int | None = None
    ) -> list[DeclinedPair]:
        return self.sqlite_db.list_declined_duplicates(user_id=user_id)

    def undecline_duplicate_suggestion(
        self, one_id: int, other_id: int, user_id: int | None = None
    ) -> DeclinedPair | None:
        with self._save_lock:
            return self.sqlite_db.undecline_duplicate_suggestion(
                one_id, other_id, user_id=user_id
            )

    def set_item_ignored(
        self, db_id: int, ignored: bool, user_id: int | None = None
    ) -> bool:
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
        release_year: int | None = None,
        creator: str | None = None,
        user_id: int | None = None,
    ) -> bool:
        """The explicit-user-action door: it writes only the fields supplied and
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
            release_year=release_year,
            creator=creator,
            user_id=user_id,
        )

    def count_items(
        self,
        user_id: int | None = None,
        content_type: ContentType | None = None,
        status: ConsumptionStatus | None = None,
    ) -> int:
        return self.sqlite_db.count_items(
            user_id=user_id, content_type=content_type, status=status
        )

    def get_all_users(self) -> list[UserDict]:
        with self.sqlite_db.connection() as conn:
            return get_all_users(conn)

    def update_user_identity(
        self, user_id: int, username: str, display_name: str | None
    ) -> UserDict:
        """Normalized here: ``schema.py`` cannot import ``accounts`` without a cycle."""
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
        without clobbering the other keys there."""
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
        """*apply* edits the config in place, and must not call another
        ``_save_lock`` writer: the lock is not reentrant, so that wedges the
        worker for good, silently.
        """
        with self._save_lock:
            preference_config = self.get_user_preference_config(user_id)
            apply(preference_config)
            self._write_preference_config(user_id, preference_config)
            return preference_config
