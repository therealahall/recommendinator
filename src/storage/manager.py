"""Unified storage manager for SQLite and optionally ChromaDB."""

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
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.storage.accounts import (
    account_is_claimed,
    claim_account,
    create_session,
    lookup_session,
    purge_expired_sessions,
    revoke_all_sessions,
    revoke_session,
    set_password,
    verify_password,
)
from src.storage.global_secrets import GLOBAL_SECRET_USER_ID, secret_ref
from src.storage.schema import (
    ConversationMessageDict,
    CoreMemoryDict,
    EnrichmentStatusDict,
    SourceConfigDict,
    UserDict,
    clear_cached_preference_interpretations,
    clear_conversation_history,
    credential_row_exists,
    delete_core_memory,
    delete_credential,
    delete_credentials_for_source,
    delete_setting,
    delete_source_config,
    get_all_users,
    get_cached_preference_interpretation,
    get_conversation_history,
    get_core_memories,
    get_credential,
    get_credentials_for_source,
    get_default_user_id,
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
    save_cached_preference_interpretation,
    save_conversation_message,
    save_core_memory,
    save_credential,
    save_preference_profile,
    set_setting,
    set_source_config_enabled,
    update_core_memory,
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
    from src.storage.vector_db import VectorDB

logger = logging.getLogger(__name__)

#: Prefix of the synthetic vector-DB key used for an item with no external id.
_DB_EMBEDDING_PREFIX = "db_"


class UnknownUserError(LookupError):
    """A write named a user id no ``users`` row carries."""


def _embedding_key(item: ContentItem, db_id: int) -> str:
    """Return the vector-DB key for *item* as stored under *db_id*.

    Items imported without an external id — CSV, chat, manual completion —
    all have ``id is None``, so they key on their database row instead.  Every
    read and delete must derive the key the same way or it misses the row that
    was written.

    Args:
        item: The item as written to SQLite.
        db_id: Database ID it was written under.

    Returns:
        The item's external id, or ``db_<db_id>`` when it has none.
    """
    return item.id if item.id else f"{_DB_EMBEDDING_PREFIX}{db_id}"


def stored_embedding_key(item: ContentItem) -> str | None:
    """Return the vector-DB key *item*'s own embedding is stored under.

    Callers that search or exclude by key must derive it the same way the
    write did, so an item with no external id is named by its row rather than
    dropped.

    Args:
        item: An item read back from the library.

    Returns:
        The key, or ``None`` for an item with neither an external id nor a
        row, which therefore has no embedding of its own.
    """
    if item.db_id is None:
        return item.id
    return _embedding_key(item, item.db_id)


def _db_id_from_embedding_key(key: str) -> int | None:
    """Return the database ID *key* names, or ``None`` if it names none.

    Args:
        key: A vector-DB content id.

    Returns:
        The database ID for a synthetic ``db_`` key, otherwise ``None``.
    """
    suffix = key.removeprefix(_DB_EMBEDDING_PREFIX)
    if suffix == key or not suffix.isdecimal():
        return None
    try:
        return int(suffix)
    except ValueError:
        # Both guards earn their place. isdecimal rejects spellings int()
        # would happily take (padding whitespace, a sign, PEP 515
        # underscores), none of which this writer ever produces, so a key
        # spelled that way names no row. The try catches what no character
        # check can see: int() refuses any string past 4300 digits, however
        # plain its characters.
        return None


class StorageManager:
    """Unified storage manager for SQLite and optionally ChromaDB.

    When ai_enabled is False (default), only SQLite is used.
    When ai_enabled is True, ChromaDB is also initialized for embeddings.
    """

    def __init__(
        self,
        sqlite_path: Path,
        vector_db_path: Path | None = None,
        vector_collection_name: str = "content_embeddings",
        ai_enabled: bool = False,
    ) -> None:
        """Initialize storage manager.

        Args:
            sqlite_path: Path to SQLite database file
            vector_db_path: Path to ChromaDB database directory (optional)
            vector_collection_name: Name of ChromaDB collection
            ai_enabled: Whether to enable AI features (embeddings)
        """
        self.sqlite_db = SQLiteDB(sqlite_path)
        self.vector_db: VectorDB | None = None
        self.ai_enabled = ai_enabled
        self._credential_key_path = self._resolve_key_path(sqlite_path)
        # Serialises every read-then-write on this manager: each `with` site
        # below leaves a gap between read and write that WAL's concurrent
        # readers do not close, and the callers that collide there are parallel
        # sync workers and FastAPI threadpool workers alike.
        self._save_lock = threading.Lock()

        # Only initialize vector DB if AI is enabled and path provided.
        # Deferred import: chromadb is heavy (~500 MB+) and should not load
        # when AI features are disabled.
        if ai_enabled and vector_db_path:
            try:
                from src.storage.vector_db import VectorDB

                self.vector_db = VectorDB(vector_db_path, vector_collection_name)
            except ImportError:
                logger.warning(
                    "AI features enabled in config but chromadb is not installed. "
                    "Vector DB disabled. Install with: uv sync --locked --extra ai"
                )
                self.ai_enabled = False

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

    def save_content_item(
        self,
        item: ContentItem,
        user_id: int | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        """Save a content item to SQLite and optionally ChromaDB.

        Upsert-by-external-id and cross-source dedup by normalized title are
        both handled by :meth:`SQLiteDB.save_content_item`; this wrapper
        serialises that read-then-write under ``_save_lock`` and mirrors the
        embedding into the vector DB.

        Args:
            item: ContentItem to save
            user_id: User ID (defaults to item.user_id)
            embedding: Optional embedding vector to store (requires ai_enabled)

        Returns:
            Database ID of the saved item
        """
        with self._save_lock:
            # Save to SQLite. Upsert-by-external-id and cross-source dedup by
            # normalized title both live in SQLiteDB.save_content_item.
            db_id = self.sqlite_db.save_content_item(item, user_id=user_id)
            self._mirror_embedding(item, db_id, user_id, embedding)

        return db_id

    def complete_content_item(
        self,
        item: ContentItem,
        user_id: int | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        """Record an explicit completion, adding the item if it is new.

        The single entry point behind every completion — the ``complete`` CLI
        command, ``POST /api/complete`` and chat's ``mark_completed``:
        :meth:`SQLiteDB.complete_content_item` finds or creates the row and
        applies the user's rating, review and completion date in one
        transaction, and this wrapper serialises it under ``_save_lock`` and
        mirrors the embedding, as ``save_content_item`` does for sync.

        Args:
            item: ContentItem being completed
            user_id: User ID (defaults to item.user_id)
            embedding: Optional embedding vector to store (requires ai_enabled)

        Returns:
            Database ID of the completed item

        Raises:
            FutureCompletionDateError: ``item.date_completed`` is a day nobody
                has lived yet. Nothing is written.
        """
        with self._save_lock:
            db_id = self.sqlite_db.complete_content_item(item, user_id=user_id)
            self._mirror_embedding(item, db_id, user_id, embedding)

        return db_id

    def _mirror_embedding(
        self,
        item: ContentItem,
        db_id: int,
        user_id: int | None,
        embedding: list[float] | None,
    ) -> None:
        """Store *item*'s embedding in the vector DB, if there is one to store.

        Args:
            item: The item as written to SQLite.
            db_id: Database ID it was written under.
            user_id: User ID the caller supplied, if any.
            embedding: Embedding vector, or None when embeddings are off.
        """
        if embedding is None or not self.vector_db:
            return

        content_id = _embedding_key(item, db_id)
        metadata = {
            "content_type": get_enum_value(item.content_type),
            "title": item.title,
            "author": item.author or "",
            "status": get_enum_value(item.status),
            "user_id": str(user_id or item.user_id),
        }
        self.vector_db.add_embedding(content_id, embedding, metadata)

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

    def get_items_by_embedding_keys(
        self,
        keys: list[str],
        user_id: int | None = None,
        content_type: ContentType | None = None,
        include_ignored: bool = True,
    ) -> dict[str, ContentItem]:
        """Resolve vector-DB keys back to the items they were stored for.

        An embedding is keyed by its item's external id, or by ``db_<db_id>``
        when the item has none, so a caller holding search hits gets both
        forms back.  Fetching exactly the keys asked for keeps a similarity
        search independent of how large the library is.

        Args:
            keys: Vector-DB content ids, typically from a similarity search.
            user_id: User whose library to resolve against (defaults to the
                default user).
            content_type: Type the caller searched, if any. One external id
                may name a row of each type, so a search must say which one
                it means or it can resolve a hit to the wrong item.
            include_ignored: Whether ignored items may be returned.

        Returns:
            The item found for each key. A key naming no item is absent.
        """
        if not keys:
            return {}

        effective_user_id = user_id if user_id is not None else get_default_user_id()

        # External ids first: a stored external id is a real identity, so it
        # wins over the synthetic form should an id ever look like one.
        by_key: dict[str, ContentItem] = {
            item.id: item
            for item in self.sqlite_db.get_content_items_by_external_ids(
                keys, user_id=effective_user_id, content_type=content_type
            )
            if item.id
        }
        db_ids = [
            db_id
            for key in keys
            if key not in by_key
            and (db_id := _db_id_from_embedding_key(key)) is not None
        ]
        for item in self.sqlite_db.get_content_items_by_db_ids(db_ids):
            if item.user_id != effective_user_id:
                continue
            if content_type is not None and item.content_type != content_type:
                continue
            by_key[f"{_DB_EMBEDDING_PREFIX}{item.db_id}"] = item

        if include_ignored:
            return by_key
        return {key: item for key, item in by_key.items() if not item.ignored}

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

    def search_similar(
        self,
        query_embedding: list[float],
        user_id: int | None = None,
        n_results: int = 10,
        content_type: ContentType | None = None,
        exclude_consumed: bool = True,
    ) -> list[dict[str, Any]]:
        """Search for similar content using vector similarity.

        Requires ai_enabled=True.

        Args:
            query_embedding: Query embedding vector
            user_id: Filter by user ID
            n_results: Number of results to return
            content_type: Optional filter by content type
            exclude_consumed: If True, exclude consumed items

        Returns:
            List of similar content items with scores and metadata

        Raises:
            RuntimeError: If called when AI is not enabled
        """
        if not self.vector_db:
            raise RuntimeError(
                "Vector search requires ai_enabled=True in StorageManager"
            )

        # Exclude the consumed items by the keys their embeddings are stored
        # under, not by their external ids: an id-less item has an embedding
        # in the store and would otherwise take a result slot.
        exclude_ids: list[str] | None = None
        if exclude_consumed:
            consumed = self.get_completed_items(
                user_id=user_id, content_type=content_type
            )
            exclude_ids = [
                key for item in consumed if (key := stored_embedding_key(item))
            ]

        content_type_str = get_enum_value(content_type) if content_type else None

        results = self.vector_db.search_similar(
            query_embedding=query_embedding,
            n_results=n_results,
            content_type=content_type_str,
            exclude_ids=exclude_ids,
        )

        return results

    def delete_content_item(self, db_id: int, user_id: int | None = None) -> bool:
        """Delete a content item from both databases.

        Args:
            db_id: Database ID
            user_id: Optional user ID filter

        Returns:
            True if item was deleted, False if not found
        """
        # Read the item first: its embedding key is derived from it.
        item = self.sqlite_db.get_content_item(db_id, user_id=user_id)
        if not item:
            return False

        deleted = self.sqlite_db.delete_content_item(db_id, user_id=user_id)

        if deleted and self.vector_db:
            self.vector_db.delete_embedding(_embedding_key(item, db_id))

        return deleted

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
        status: str,
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
            status: New status value.
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

    def add_embedding(
        self,
        content_id: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add or update an embedding for a content item.

        Requires ai_enabled=True.

        Args:
            content_id: Unique identifier for the content item
            embedding: Vector embedding
            metadata: Optional metadata dictionary

        Raises:
            RuntimeError: If called when AI is not enabled
        """
        if not self.vector_db:
            raise RuntimeError("Embeddings require ai_enabled=True in StorageManager")
        self.vector_db.add_embedding(content_id, embedding, metadata)

    def get_embedding(self, content_id: str) -> list[float] | None:
        """Get embedding for a content item.

        Requires ai_enabled=True.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            Embedding vector if found, None otherwise

        Raises:
            RuntimeError: If called when AI is not enabled
        """
        if not self.vector_db:
            raise RuntimeError("Embeddings require ai_enabled=True in StorageManager")
        return self.vector_db.get_embedding(content_id)

    def has_embedding(self, content_id: str) -> bool:
        """Check if an embedding exists for a content item.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            True if embedding exists, False otherwise (or if AI disabled)
        """
        if not self.vector_db:
            return False
        return self.vector_db.has_embedding(content_id)

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

    # Account and session methods

    def account_is_claimed(self) -> bool:
        """Report whether anyone has set a password on this instance."""
        with self.sqlite_db.connection() as conn:
            return account_is_claimed(conn)

    def claim_account(
        self, username: str, display_name: str | None, plaintext_password: str
    ) -> UserDict:
        """Claim the instance: name the account and give it a password.

        Raises:
            AccountAlreadyClaimedError: The account already has a password.
        """
        with self.sqlite_db.connection() as conn:
            return claim_account(conn, username, display_name, plaintext_password)

    def set_password(self, user_id: int, plaintext: str) -> None:
        """Replace a user's password."""
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

    def get_cached_preference_interpretation(self, cache_key: str) -> str | None:
        """Get a cached preference interpretation.

        Args:
            cache_key: The cache key to look up.

        Returns:
            Cached JSON string or None if not found.
        """
        with self.sqlite_db.connection() as conn:
            return get_cached_preference_interpretation(conn, cache_key)

    def save_cached_preference_interpretation(
        self, cache_key: str, interpretation_json: str
    ) -> None:
        """Save a preference interpretation to the cache.

        Args:
            cache_key: The cache key.
            interpretation_json: JSON string of the interpretation.
        """
        with self.sqlite_db.connection() as conn:
            save_cached_preference_interpretation(conn, cache_key, interpretation_json)

    def clear_cached_preference_interpretations(self) -> int:
        """Clear all cached preference interpretations.

        Returns:
            Number of rows deleted.
        """
        with self.sqlite_db.connection() as conn:
            return clear_cached_preference_interpretations(conn)

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

    # Core memory methods

    def get_core_memories(
        self,
        user_id: int,
        active_only: bool = True,
        memory_type: str | None = None,
    ) -> list[CoreMemoryDict]:
        """Get core memories for a user.

        Args:
            user_id: User ID
            active_only: If True, only return active memories
            memory_type: Filter by type ("user_stated" or "inferred")

        Returns:
            List of memory dicts
        """
        with self.sqlite_db.connection() as conn:
            return get_core_memories(
                conn, user_id, active_only=active_only, memory_type=memory_type
            )

    def save_core_memory(
        self,
        user_id: int,
        memory_text: str,
        memory_type: str,
        source: str,
        confidence: float = 1.0,
    ) -> int:
        """Save a new core memory.

        Args:
            user_id: User ID
            memory_text: The preference statement
            memory_type: "user_stated" or "inferred"
            source: "conversation", "rating_pattern", or "manual"
            confidence: Confidence score (0.0-1.0)

        Returns:
            New memory ID
        """
        with self.sqlite_db.connection() as conn:
            return save_core_memory(
                conn,
                user_id=user_id,
                memory_text=memory_text,
                memory_type=memory_type,
                source=source,
                confidence=confidence,
            )

    def update_core_memory(
        self,
        memory_id: int,
        memory_text: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        """Update a core memory.

        Args:
            memory_id: Memory ID to update
            memory_text: New memory text (optional)
            is_active: New active status (optional)

        Returns:
            True if updated, False if not found
        """
        with self.sqlite_db.connection() as conn:
            return update_core_memory(
                conn,
                memory_id=memory_id,
                memory_text=memory_text,
                is_active=is_active,
            )

    def delete_core_memory(self, memory_id: int) -> bool:
        """Delete a core memory.

        Args:
            memory_id: Memory ID to delete

        Returns:
            True if deleted, False if not found
        """
        with self.sqlite_db.connection() as conn:
            return delete_core_memory(conn, memory_id)

    # Conversation history methods

    def get_conversation_history(
        self,
        user_id: int,
        limit: int = 50,
    ) -> list[ConversationMessageDict]:
        """Get recent conversation history for a user.

        Args:
            user_id: User ID
            limit: Maximum number of messages to return

        Returns:
            List of message dicts ordered chronologically (oldest first)
        """
        with self.sqlite_db.connection() as conn:
            return get_conversation_history(conn, user_id, limit=limit)

    def save_conversation_message(
        self,
        user_id: int,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> int:
        """Save a conversation message.

        Args:
            user_id: User ID
            role: "user" or "assistant"
            content: Message content
            tool_calls: Optional list of tool calls made

        Returns:
            New message ID
        """
        with self.sqlite_db.connection() as conn:
            return save_conversation_message(
                conn,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
            )

    def clear_conversation_history(self, user_id: int) -> int:
        """Clear conversation history for a user (the "reset" functionality).

        Note: This clears the conversation but preserves core memories.

        Args:
            user_id: User ID

        Returns:
            Number of messages deleted
        """
        with self.sqlite_db.connection() as conn:
            return clear_conversation_history(conn, user_id)

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
