"""Database schema definitions."""

import json
import sqlite3
from typing import Any, TypedDict

from src.models.detail_fields import text_names, to_text
from src.storage.derived import backfill_derived_columns
from src.storage.merge import (
    merge_detail_tables,
    merge_scalar_columns,
    normalize_title_for_matching,
)


class EnrichmentStatusDict(TypedDict):
    """Enrichment status for a content item."""

    content_item_id: int
    last_enriched_at: str | None
    enrichment_provider: str | None
    enrichment_quality: str | None
    needs_enrichment: bool
    enrichment_error: str | None


class CoreMemoryDict(TypedDict):
    """A core memory record."""

    id: int
    user_id: int
    memory_text: str
    memory_type: str
    source: str
    confidence: float
    created_at: str
    updated_at: str | None
    is_active: bool


class ConversationMessageDict(TypedDict):
    """A conversation message record."""

    id: int
    user_id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None
    created_at: str


class UserDict(TypedDict):
    """A user record."""

    id: int
    username: str
    display_name: str | None
    created_at: str
    settings: dict[str, Any] | None


class SourceConfigRow(TypedDict):
    """Raw row from the source_configs table."""

    source_id: str
    plugin: str
    config_json: str
    enabled: int
    migrated_at: str
    updated_at: str


class SourceConfigDict(TypedDict):
    """Parsed source config record returned by StorageManager.

    ``config`` is the deserialised non-sensitive config dict; sensitive
    values stay in the encrypted ``credentials`` table and must be merged in
    by ``resolve_inputs`` at sync time.
    """

    source_id: str
    plugin: str
    config: dict[str, Any]
    enabled: bool
    migrated_at: str
    updated_at: str


# Whitelist of table names allowed in dynamic SQL queries.
# Defense-in-depth: these names come from hardcoded strings in
# get_enrichment_stats, but validating prevents accidental injection.
_ALLOWED_ENRICHMENT_TABLES: frozenset[str] = frozenset(
    {"content_items", "enrichment_status"}
)

# Whitelist of column names allowed in dynamic enrichment GROUP BY queries.
# Defense-in-depth: values come from hardcoded call sites in get_enrichment_stats,
# but validating here prevents SQL injection if a new call site passes untrusted
# input. When adding enrichment columns, update this set.
_ALLOWED_ENRICHMENT_COLUMNS: frozenset[str] = frozenset(
    {"enrichment_provider", "enrichment_quality"}
)

# Whitelist of SQL table aliases allowed in enrichment queries.
_ALLOWED_ENRICHMENT_ALIASES: frozenset[str] = frozenset({"es"})

# Whitelist of SQL WHERE clauses allowed in enrichment count queries.
# Defense-in-depth: all current call sites in get_enrichment_stats pass
# hardcoded literals, but validating prevents SQL injection if a future
# call site passes untrusted input.
_ALLOWED_ENRICHMENT_WHERE: frozenset[str] = frozenset(
    {
        "1=1",
        "needs_enrichment = 1 AND enrichment_error IS NULL",
        "es.needs_enrichment = 1 AND es.enrichment_error IS NULL",
        "needs_enrichment = 0 AND enrichment_error IS NULL"
        " AND enrichment_provider != 'none'",
        "es.needs_enrichment = 0 AND es.enrichment_error IS NULL"
        " AND es.enrichment_provider != 'none'",
        "enrichment_error IS NOT NULL",
        "es.enrichment_error IS NOT NULL",
    }
)

# Whitelist of SQL JOIN clauses allowed in enrichment queries.
_ALLOWED_ENRICHMENT_JOINS: frozenset[str] = frozenset(
    {"", " JOIN content_items ci ON es.content_item_id = ci.id"}
)

# Whitelist of SQL filter suffixes allowed in enrichment queries.
_ALLOWED_ENRICHMENT_FILTERS: frozenset[str] = frozenset({"", " AND ci.user_id = ?"})

# Schema version tracked in SQLite's ``PRAGMA user_version``. Bumped when a
# one-time upgrade must run exactly once per database. ``create_schema`` reads
# the stored version once per open, hands it to every guarded step, and writes
# this value back after the last of them, so a step runs only while the stored
# version is below the one that introduced it:
#
#   1: clear the ``settings`` rows an earlier seed-on-boot design wrote
#   2: prune the ``settings`` leaves that are no longer registry entries
#   3: repair the legacy content rows ``_repair_legacy_content_rows`` describes
#
# Version 4 records the derived columns ``src/storage/derived.py`` describes and
# guards nothing: their backfill selects the rows missing them, so it repairs a
# row a downgraded build inserted into a database already stamped 4.
#
# Version 5 records the ``users`` password columns and the ``sessions`` table
# (``src/storage/accounts.py``), and guards nothing: the unconditional ALTER
# and CREATE add both, and an unclaimed instance is exactly the NULL columns.
#
# A guarded step runs once per database, so the values it wrote never follow a
# change to the function that produced them: changing
# ``normalize_title_for_matching``, ``get_sort_title`` or ``build_search_text``
# needs a version bump and a new guarded step to rewrite the stored columns.
# Without one, stored values keep the old form while new saves compute the new
# one, and the dedup lookups stop matching — duplicates accumulate in silence.
#
# The plain ``CREATE TABLE IF NOT EXISTS`` / ``ALTER`` migrations stay idempotent
# and run unconditionally; only version-guarded steps consult this.
_SCHEMA_VERSION = 5

# Leaves that were settings-registry entries on an earlier iteration of the
# database-backed config and no longer are. ``web.host``/``port``/``debug`` moved
# to bootstrap-only config (the launcher reads them before any database is open)
# and the ``ingestion`` section was removed with the conflict-resolution code it
# configured. Rows for these keys are unreachable from the app but would still
# be overlaid onto config, so they are pruned once on upgrade.
_ORPHANED_SETTING_KEYS: tuple[str, ...] = (
    "web.host",
    "web.port",
    "web.debug",
    "ingestion.conflict_strategy",
    "ingestion.source_priority",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the database schema.

    Includes:
    - Users table for multi-user support
    - Content items with user_id foreign key
    - Type-specific detail tables (books, movies, TV shows, games)
    - Preference interpretation cache

    Args:
        conn: SQLite database connection. ``row_factory`` is set to
              ``sqlite3.Row`` unconditionally — required by migration dedup.
    """
    # Required by merge_scalar_columns which uses named column access
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    stored_version = _stored_schema_version(cursor)

    # Users table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settings TEXT  -- JSON for per-user settings (AI enabled, weights, etc.)
        )
        """
    )

    # Create default user
    cursor.execute(
        """
        INSERT OR IGNORE INTO users (id, username, display_name)
        VALUES (1, 'default', 'Default User')
        """
    )

    # Login credentials for that row, NULL until someone claims the instance —
    # which is how the web layer tells a fresh install from a claimed one.
    _add_column_if_not_exists(cursor, "users", "password_hash", "TEXT")
    _add_column_if_not_exists(cursor, "users", "password_salt", "TEXT")
    _add_column_if_not_exists(cursor, "users", "password_updated_at", "TIMESTAMP")

    # Web sessions, keyed by a hash of the token so that reading this table
    # hands over no live session (see src/storage/accounts.py).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            last_seen_at TIMESTAMP NOT NULL
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")

    # Base content items table with user_id
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
            external_id TEXT,
            title TEXT NOT NULL,
            normalized_title TEXT,
            sort_title TEXT,
            search_text TEXT,
            content_type TEXT NOT NULL,
            status TEXT NOT NULL,
            rating INTEGER CHECK (rating >= 1 AND rating <= 5),
            review TEXT,
            date_completed DATE,
            -- Source id, never a plugin name: two sources on one plugin must
            -- stay tellable apart. migrate_source_attribution repairs rows
            -- written before the plugins kept the id.
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, external_id, content_type)
        )
        """
    )

    # Book-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS book_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            author TEXT,
            pages INTEGER,
            isbn TEXT,
            isbn13 TEXT,
            publisher TEXT,
            year_published INTEGER,
            genres TEXT,  -- JSON array of genres
            metadata TEXT  -- JSON for additional fields
        )
        """
    )

    # Movie-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS movie_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            director TEXT,
            runtime INTEGER,  -- minutes
            release_year INTEGER,
            genres TEXT,  -- JSON array of genres
            studio TEXT,
            metadata TEXT
        )
        """
    )

    # TV Show-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tv_show_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            creators TEXT,
            seasons INTEGER,
            episodes INTEGER,
            network TEXT,
            release_year INTEGER,
            genres TEXT,  -- JSON array of genres
            metadata TEXT
        )
        """
    )

    # Video Game-specific details
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS video_game_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            developer TEXT,
            publisher TEXT,
            platforms TEXT,  -- JSON array of platforms
            genres TEXT,  -- JSON array of genres
            release_year INTEGER,
            metadata TEXT
        )
        """
    )

    # Create indexes for common queries
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_user ON content_items(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_type ON content_items(content_type)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_status ON content_items(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rating ON content_items(rating)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_date_completed ON content_items(date_completed)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source ON content_items(source)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_type ON content_items(user_id, content_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_status ON content_items(user_id, status)"
    )

    # Indexes for type-specific fields
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_author ON book_details(author)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_director ON movie_details(director)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_game_developer ON video_game_details(developer)"
    )

    # Preference interpretation cache (for LLM interpretations)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS preference_interpretation_cache (
            cache_key TEXT PRIMARY KEY,
            interpretation_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Enrichment status tracking
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment_status (
            content_item_id INTEGER PRIMARY KEY
                REFERENCES content_items(id) ON DELETE CASCADE,
            last_enriched_at TIMESTAMP,
            enrichment_provider TEXT,
            enrichment_quality TEXT,
            needs_enrichment BOOLEAN DEFAULT 1,
            enrichment_error TEXT
        )
        """
    )

    # Index for finding items that need enrichment
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_enrichment_needs "
        "ON enrichment_status(needs_enrichment)"
    )

    # Add tags and description columns to detail tables if they don't exist
    # Use safe ALTER TABLE that checks for column existence
    _add_column_if_not_exists(cursor, "book_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "book_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "movie_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "movie_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "tv_show_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "tv_show_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "video_game_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "video_game_details", "description", "TEXT")

    # Add ignored column to content_items for filtering from recommendations
    _add_column_if_not_exists(cursor, "content_items", "ignored", "BOOLEAN DEFAULT 0")

    # Add normalized_title column for O(1) title-matching lookups
    _add_column_if_not_exists(cursor, "content_items", "normalized_title", "TEXT")
    if stored_version < 3:
        _repair_legacy_content_rows(cursor)
    # Index must be created *after* the migration adds the column
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ci_normalized_title "
        "ON content_items(user_id, content_type, normalized_title)"
    )

    # Columns derived from the title and the creator, so the library list is
    # ordered and searched in SQL (see src/storage/derived.py). Filled after
    # the repair above, which writes a creator two ways this fill has to see:
    # the merge moves one onto the row that survives, and the company fold
    # recovers one that existed only in a blob. Unguarded because the fill
    # selects the rows that need it rather than the databases that have never
    # had one.
    _add_column_if_not_exists(cursor, "content_items", "sort_title", "TEXT")
    _add_column_if_not_exists(cursor, "content_items", "search_text", "TEXT")
    backfill_derived_columns(cursor)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ci_sort_title "
        "ON content_items(user_id, sort_title, id)"
    )

    # Core memories: significant preference signals
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS core_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            memory_text TEXT NOT NULL,
            memory_type TEXT NOT NULL,  -- "user_stated" or "inferred"
            source TEXT,  -- "conversation", "rating_pattern", "manual"
            confidence REAL DEFAULT 1.0,  -- 0.0-1.0 for inferred memories
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1  -- User can deactivate inferred memories
        )
    """
    )

    # Conversation history (for context rebuilding)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,  -- "user" or "assistant"
            content TEXT NOT NULL,
            tool_calls TEXT,  -- JSON array of tool calls made
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Preference profile snapshots (regenerated periodically)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS preference_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            profile_json TEXT NOT NULL,  -- Distilled preference summary
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id)  -- One active profile per user
        )
    """
    )

    # Credentials table for encrypted source credentials (API keys, tokens)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS credentials (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            credential_key TEXT NOT NULL,
            credential_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, source_id, credential_key)
        )
        """
    )

    # Source configs table: per-source non-sensitive config that has been
    # migrated from config.yaml into the database. Once a row exists for
    # (user_id, source_id), the YAML entry for that source is no longer
    # consulted by resolve_inputs — the database is the source of truth.
    # Sensitive fields (API keys, tokens) keep going through the encrypted
    # ``credentials`` table above; this table holds the rest.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS source_configs (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            plugin TEXT NOT NULL,
            config_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            migrated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, source_id)
        )
        """
    )

    # Global/system settings: dotted leaf key -> JSON-encoded value. Holds ONLY
    # the leaves a user explicitly set via the Settings page / `settings` CLI,
    # keyed by dotted path (e.g. "recommendations.default_count"). Nothing is
    # seeded here on boot; a stored leaf wins over YAML and the registry const
    # default. Per-source config and credentials keep their own tables above.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Records the one-time migrations that run at app start rather than at
    # database open, so they cannot ride PRAGMA user_version. Kept out of
    # ``settings``, whose rows are the user's own and which a fresh install
    # leaves empty.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS completed_migrations (
            name TEXT PRIMARY KEY,
            completed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Version-guarded one-time settings migrations (see _migrate_settings_table).
    _migrate_settings_table(cursor, stored_version)

    # Indexes for conversation tables
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_core_memories_user " "ON core_memories(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_core_memories_active "
        "ON core_memories(user_id, is_active)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_messages_user "
        "ON conversation_messages(user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_created "
        "ON conversation_messages(user_id, created_at DESC)"
    )

    # Records that every guarded step above has run, so the next open skips
    # them. Written inside the same transaction as the steps themselves: an
    # open that raises advances nothing and the next one retries the lot.
    # Only ever moves forward. Skipped when the version already says this,
    # because the pragma rewrites the database header and an open with nothing
    # to upgrade should leave the file alone, and skipped when the version is
    # higher because a database written by a later build must not be rewound
    # into re-running one of its one-time steps.
    #
    # PRAGMA statements cannot be parameterised; the value is a validated
    # module-level integer constant, not caller input.
    cursor.execute("PRAGMA user_version")
    if cursor.fetchone()[0] < _SCHEMA_VERSION:
        cursor.execute(f"PRAGMA user_version = {int(_SCHEMA_VERSION)}")

    conn.commit()


def _stored_schema_version(cursor: sqlite3.Cursor) -> int:
    """Read the version the one-time upgrade steps must start from.

    A database with no tables at all is being created by this open, so
    ``CREATE TABLE`` is about to write every row in the current shape and no
    guarded step has anything to find. It reports as already current rather
    than as version 0 — which is the version an upgrading database that
    predates ``user_version`` really carries.
    """
    cursor.execute("SELECT COUNT(*) FROM sqlite_master")
    if cursor.fetchone()[0] == 0:
        return _SCHEMA_VERSION
    cursor.execute("PRAGMA user_version")
    return int(cursor.fetchone()[0])


def _repair_legacy_content_rows(cursor: sqlite3.Cursor) -> None:
    """Rewrite the content rows left in shapes storage no longer writes.

    Each pass reads the whole library, so all three are guarded to run once per
    database: no current write path produces what any of them looks for, and a
    row a pass deliberately declines to settle — a fill-only column holding
    another producer's object, say — would otherwise be re-read on every open
    for the life of the database.

    The passes are ordered, and the order is only safe while they share one
    transaction: the implicit one ``create_schema``'s first write opened and
    the commit at the end closes. Nothing may commit between them, and that
    connection must keep implicit transactions, or a merge that fails leaves
    the repair committed over a library the merge never finished.
    """
    # Approximate normalization for a column the caller's ALTER may have just
    # added; the pass below corrects it with the full Python function, which
    # SQL's lower() cannot match — it strips no punctuation, article or
    # edition suffix.
    cursor.execute(
        "UPDATE content_items SET normalized_title = lower(title) "
        "WHERE normalized_title IS NULL"
    )
    _renormalize_titles(cursor)
    # Repairing before the merge lets each row fold its own stranded season
    # count onto its own column: the merge then takes the higher of two real
    # counts, rather than of whichever blob copy survived it.
    _migrate_stranded_detail_shapes(cursor)
    # Merge any duplicates exposed by the corrected normalization
    _deduplicate_inline(cursor)


def _migrate_settings_table(cursor: sqlite3.Cursor, stored_version: int) -> None:
    """Run the version-guarded one-time migrations of the ``settings`` table.

    **Version 1 — drop every pre-existing row.**
    An earlier iteration of the database-backed config seeded the ``settings``
    table on every boot — both dotted-leaf rows (``features.ai_enabled``) and
    stale whole-section JSON-blob rows (``features`` -> a dict). Seed-on-boot
    has since been removed; the table now holds only leaves a user explicitly
    sets via the settings UI/CLI. Because that feature is unreleased, no
    pre-existing row is genuine user input — every one is a seed artifact — so
    the whole table is cleared once on the first upgrade.

    Guarded by ``PRAGMA user_version``: each step runs only while *stored_version*
    is below the version that introduced it, and ``create_schema`` advances the
    stored version once every guarded step has run, so neither fires again. A
    leaf a user sets after the upgrade therefore survives every later init.

    **Version 2 — prune only the keys in :data:`_ORPHANED_SETTING_KEYS`.**
    Unlike version 1 this must SPARE every other row: by now a developer on this
    branch may have set real values. These five were briefly registry entries and
    no longer are, leaving rows the app cannot reach — ``settings reset`` and
    ``DELETE /api/settings`` both refuse a key with no registry entry. The
    ``web.*`` rows would still be overlaid onto ``config["web"]`` by
    ``migrate_config_settings``; the ``ingestion.*`` rows cannot be, since that
    section left ``IN_SCOPE_SECTIONS`` too, and are deleted simply as garbage.
    Also unreleased, so no row here is genuine user intent either.
    """
    if stored_version < 1:
        cursor.execute("DELETE FROM settings")

    if stored_version < 2:
        cursor.executemany(
            "DELETE FROM settings WHERE key = ?",
            [(key,) for key in _ORPHANED_SETTING_KEYS],
        )


def _renormalize_titles(cursor: sqlite3.Cursor) -> None:
    """Re-normalize all content_items titles using the full Python function.

    The initial migration backfill uses SQL ``lower(title)`` which misses
    punctuation stripping, article removal, edition suffix removal, etc.
    This updates every row with a non-NULL title to use the canonical
    normalization.
    """
    cursor.execute("SELECT id, title FROM content_items WHERE title IS NOT NULL")
    # fetchall() required: cursor is reused for UPDATEs inside the loop
    for row in cursor.fetchall():
        normalized = normalize_title_for_matching(row["title"])
        cursor.execute(
            "UPDATE content_items SET normalized_title = ? WHERE id = ?",
            (normalized, row["id"]),
        )


def _deduplicate_inline(cursor: sqlite3.Cursor) -> None:
    """Merge duplicate rows exposed by re-normalization.

    Finds groups sharing (user_id, content_type, normalized_title) and
    keeps the oldest row (lowest id), merging data from duplicates.
    Runs inside the schema migration transaction.
    """
    cursor.execute(
        """SELECT user_id, content_type, normalized_title
           FROM content_items
           WHERE normalized_title IS NOT NULL AND normalized_title != ''
           GROUP BY user_id, content_type, normalized_title
           HAVING COUNT(*) > 1"""
    )
    groups = cursor.fetchall()
    for group in groups:
        g_user_id = group["user_id"]
        g_content_type = group["content_type"]
        g_normalized = group["normalized_title"]
        cursor.execute(
            """SELECT id FROM content_items
               WHERE user_id = ? AND content_type = ? AND normalized_title = ?
               ORDER BY id""",
            (g_user_id, g_content_type, g_normalized),
        )
        rows = cursor.fetchall()
        if len(rows) < 2:
            continue

        keep_id = rows[0]["id"]
        for dup_row in rows[1:]:
            dup_id = dup_row["id"]
            _merge_duplicate_row(cursor, keep_id=keep_id, delete_id=dup_id)


def _merge_duplicate_row(cursor: sqlite3.Cursor, keep_id: int, delete_id: int) -> None:
    """Merge all data from duplicate into kept row, then delete duplicate.

    Uses the shared ``merge_scalar_columns`` and ``merge_detail_tables``
    functions from ``merge`` for the merge rules.  This ensures that
    migration-time dedup preserves user-owned state (rating, review, status,
    ignored, completion date) and detail table data (genres, tags, etc.)
    the same way as the runtime ``_merge_duplicate_into`` method.
    """
    merge_scalar_columns(cursor, keep_id, delete_id)
    merge_detail_tables(cursor, keep_id, delete_id)
    cursor.execute("DELETE FROM content_items WHERE id = ?", (delete_id,))


def _migrate_stranded_detail_shapes(cursor: sqlite3.Cursor) -> None:
    """Rewrite detail rows left in shapes storage no longer writes.

    No shape self-repairs on a re-sync — the metadata blob merge lets
    existing keys win, and ``platforms``, ``developer`` and ``publisher`` are
    fill-only in ``SQLiteDB._save_detail_table`` — so a one-off rewrite is the
    only fix. Every pass skips rows already in the current shape, so
    re-running is a no-op.
    """
    _move_stranded_total_seasons(cursor)
    _fold_stranded_company_names(cursor)
    _rewrite_platform_flag_dicts(cursor)


def _move_stranded_total_seasons(cursor: sqlite3.Cursor) -> None:
    """Move a blob ``total_seasons`` onto the ``seasons`` column.

    Shows written before the column accepted ``total_seasons`` as an alias
    kept the count in the free-form metadata blob. ``src/utils/series.py``
    prefers that copy, so leaving it there means a later sync raising the
    column drifts away from the number the recommender reads — a completed
    show reappears as in-progress, and the variety ladder mis-ranks it.
    """
    cursor.execute(
        "SELECT content_item_id, seasons, metadata FROM tv_show_details"
        " WHERE metadata LIKE '%total_seasons%'"
    )
    for row in cursor.fetchall():
        try:
            blob = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict) or "total_seasons" not in blob:
            continue
        seasons = _higher_season_count(row["seasons"], blob.pop("total_seasons"))
        cursor.execute(
            "UPDATE tv_show_details SET seasons = ?, metadata = ?"
            " WHERE content_item_id = ?",
            # An emptied blob is stored as NULL, which is what the write path
            # leaves when an item has no leftover metadata.
            (seasons, json.dumps(blob) if blob else None, row["content_item_id"]),
        )


def _higher_season_count(column_value: Any, blob_value: Any) -> Any:
    """Return the higher of a column and blob season count.

    ``seasons`` is monotonic (:data:`~src.storage.merge.MONOTONIC_DETAIL_COLUMNS`),
    so folding the blob copy in must never lower it — and on a row written
    before the alias existed the blob holds the only count there is.
    """
    counts: list[int] = []
    for value in (column_value, blob_value):
        try:
            counts.append(int(value))
        except (TypeError, ValueError):
            continue
    return max(counts) if counts else column_value


# The plural spellings GOG writes, now aliases of the singular video-game
# columns that claim them, mapped to the column each one folds onto. They are
# the only aliases a legacy blob can strand in front of a text column: the rest
# are read by ``to_int`` or ``to_json_array``, neither of which raises, bar
# tv_show's ``creator``. That one is reachable — the markdown source turns any
# ``Key: Value`` in a list item into a lowercased metadata key — but only ever
# as a string, which ``to_text`` takes unchanged, so it folds onto ``creators``
# on the next save rather than refusing it. What keeps a key out of this
# mapping is the shape its producers can write, not the absence of a producer.
_STRANDED_COMPANY_COLUMNS: dict[str, str] = {
    "developers": "developer",
    "publishers": "publisher",
}


def _fold_stranded_company_names(cursor: sqlite3.Cursor) -> None:
    """Fold a blob ``developers``/``publishers`` onto its own column.

    GOG wrote both plural spellings straight from its API, and neither was a
    known key then, so a legacy blob holds whatever the API said — including
    the object shape ``[{"name": "CD Projekt Red"}]``. The read path merges
    the blob into the item it returns and a text column refuses an object, so
    every re-save of such an item raises: enrichment records a provider
    failure, leaves the item queued, and fails the same way on every later
    run. Folding the names onto the column and dropping the key ends the
    shape, and recovers a name that until now existed only in the blob.

    Written for two columns of one table and no more: the SELECT and the
    UPDATE name ``developer``, ``publisher`` and ``video_game_details``
    literally. A later alias stranded in front of a text column needs its own
    pass — on another table it needs its own SELECT anyway — because a key
    added to :data:`_STRANDED_COMPANY_COLUMNS` would be popped out of every
    game blob here and written to no column at all.
    """
    cursor.execute(
        "SELECT content_item_id, developer, publisher, metadata"
        " FROM video_game_details"
        " WHERE metadata LIKE '%developers%' OR metadata LIKE '%publishers%'"
    )
    for row in cursor.fetchall():
        try:
            blob = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict) or not _STRANDED_COMPANY_COLUMNS.keys() & blob:
            continue
        # Popped whatever the columns hold: the stranded key ceases to exist
        # either way, and only then is the fold fill-only, like the write path
        # — a name enrichment has already written stands.
        folded = {
            column: to_text(text_names(blob.pop(key, None)))
            for key, column in _STRANDED_COMPANY_COLUMNS.items()
        }
        cursor.execute(
            "UPDATE video_game_details SET developer = ?, publisher = ?, metadata = ?"
            " WHERE content_item_id = ?",
            (
                row["developer"] or folded["developer"],
                row["publisher"] or folded["publisher"],
                json.dumps(blob) if blob else None,
                row["content_item_id"],
            ),
        )


def _rewrite_platform_flag_dicts(cursor: sqlite3.Cursor) -> None:
    """Rewrite GOG's per-platform flag dict as the list of names.

    GOG used to write ``platforms`` as ``{"windows": true, ...}`` where every
    other producer writes a list of names, and the column is fill-only, so a
    re-sync never replaces it: an export writes the dict's Python repr into
    the platform cell and re-importing stores that repr as a literal string.
    The dict was truthy even when it named nothing, so a game supported on no
    platform ends up with no platform value at all.
    """
    cursor.execute(
        "SELECT content_item_id, platforms FROM video_game_details"
        " WHERE platforms LIKE '%{%'"
    )
    for row in cursor.fetchall():
        names = _platform_names_from_flags(row["platforms"])
        if names is None:
            continue
        cursor.execute(
            "UPDATE video_game_details SET platforms = ? WHERE content_item_id = ?",
            (json.dumps(names) if names else None, row["content_item_id"]),
        )


def _platform_names_from_flags(raw: Any) -> list[str] | None:
    """Read a stored flag dict as the platform names it says are supported.

    Returns ``None`` for anything that is not the old shape — the current
    list of names included — so that row is left untouched. A flag dict maps
    every name to a boolean, and a dict that does not is some other producer's
    object: ``generic_json`` wraps a non-list ``platform`` in a list, so an
    imported ``{"name": "PC"}`` arrives in exactly this shape, and reading its
    keys as names would rewrite the value to ``["Name"]`` on the next start,
    and on every start after that.
    """
    try:
        stored = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    # The dict reached the column through to_json_array, which wrapped it in
    # a single-element list.
    if isinstance(stored, list) and len(stored) == 1:
        stored = stored[0]
    if not isinstance(stored, dict):
        return None
    if not all(isinstance(supported, bool) for supported in stored.values()):
        return None
    # The flags lowercased GOG's platform names; the corrected plugin keeps
    # GOG's own capitalisation ("Windows", "Mac", "Linux").
    return [str(name).capitalize() for name, supported in stored.items() if supported]


_ALLOWED_ALTER_TABLES = frozenset(
    {
        "book_details",
        "movie_details",
        "tv_show_details",
        "video_game_details",
        "content_items",
        "users",
    }
)
_ALLOWED_ALTER_COLUMNS = frozenset(
    {
        "tags",
        "description",
        "ignored",
        "normalized_title",
        "sort_title",
        "search_text",
        "password_hash",
        "password_salt",
        "password_updated_at",
    }
)
_ALLOWED_ALTER_TYPES = frozenset({"TEXT", "BOOLEAN DEFAULT 0", "TIMESTAMP"})


def _add_column_if_not_exists(
    cursor: sqlite3.Cursor, table: str, column: str, column_type: str
) -> None:
    """Add a column to a table if it doesn't already exist.

    Args:
        cursor: SQLite cursor
        table: Table name (must be in _ALLOWED_ALTER_TABLES)
        column: Column name to add (must be in _ALLOWED_ALTER_COLUMNS)
        column_type: SQL type for the column (must be in _ALLOWED_ALTER_TYPES)

    Raises:
        ValueError: If table, column, or column_type is not in the allowlist.
    """
    if table not in _ALLOWED_ALTER_TABLES:
        raise ValueError(f"Table {table!r} not in allowed tables for ALTER")
    if column not in _ALLOWED_ALTER_COLUMNS:
        raise ValueError(f"Column {column!r} not in allowed columns for ALTER")
    if column_type not in _ALLOWED_ALTER_TYPES:
        raise ValueError(f"Column type {column_type!r} not in allowed types for ALTER")

    # DDL/PRAGMA cannot use parameterized queries — allowlist above is the
    # sole injection defense.  All values are validated against frozensets.
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]

    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


# User management functions


def _row_to_user_dict(row: tuple) -> UserDict:
    """Convert a user row tuple to a user dict.

    Args:
        row: Tuple of (id, username, display_name, created_at, settings)

    Returns:
        User dict with parsed settings
    """
    settings = None
    if row[4]:
        try:
            settings = json.loads(row[4])
        except (json.JSONDecodeError, TypeError):
            settings = {}
    return {
        "id": row[0],
        "username": row[1],
        "display_name": row[2],
        "created_at": row[3],
        "settings": settings,
    }


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> UserDict | None:
    """Get a user by ID.

    Args:
        conn: SQLite database connection
        user_id: User ID

    Returns:
        User dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    return _row_to_user_dict(row) if row else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> UserDict | None:
    """Get a user by username.

    Args:
        conn: SQLite database connection
        username: Username

    Returns:
        User dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users WHERE username = ?",
        (username,),
    )
    row = cursor.fetchone()
    return _row_to_user_dict(row) if row else None


def create_user(
    conn: sqlite3.Connection,
    username: str,
    display_name: str | None = None,
    settings: dict | None = None,
) -> int:
    """Create a new user.

    Args:
        conn: SQLite database connection
        username: Unique username
        display_name: Optional display name
        settings: Optional settings dict

    Returns:
        New user ID
    """
    cursor = conn.cursor()
    settings_json = json.dumps(settings) if settings else None
    cursor.execute(
        "INSERT INTO users (username, display_name, settings) VALUES (?, ?, ?)",
        (username, display_name, settings_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def update_user_settings(
    conn: sqlite3.Connection, user_id: int, settings: dict
) -> bool:
    """Merge *settings* into the user's blob.

    Returns:
        False when the UPDATE matched no row, so a caller can tell a write
        that landed from one naming a user that does not exist.
    """
    cursor = conn.cursor()

    # Get existing settings
    cursor.execute("SELECT settings FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            existing = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            existing = {}
    else:
        existing = {}

    # Merge settings
    existing.update(settings)

    cursor.execute(
        "UPDATE users SET settings = ? WHERE id = ?",
        (json.dumps(existing), user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def update_user_identity(
    conn: sqlite3.Connection,
    user_id: int,
    username: str,
    display_name: str | None,
) -> UserDict | None:
    """Write both names, leaving the credentials alone: a rename must not cost
    anyone their password or their open sessions.

    Returns:
        None when the UPDATE matched no row.

    Raises:
        sqlite3.IntegrityError: Another row already holds *username*.
    """
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET username = ?, display_name = ? WHERE id = ?",
        (username, display_name, user_id),
    )
    conn.commit()
    return get_user_by_id(conn, user_id) if cursor.rowcount == 1 else None


def get_all_users(conn: sqlite3.Connection) -> list[UserDict]:
    """Get all users.

    Args:
        conn: SQLite database connection

    Returns:
        List of user dicts ordered by id
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users ORDER BY id"
    )
    return [_row_to_user_dict(row) for row in cursor.fetchall()]


def get_default_user_id() -> int:
    """Get the default user ID.

    Returns:
        Default user ID (always 1)
    """
    return 1


# Preference interpretation cache functions


def get_cached_preference_interpretation(
    conn: sqlite3.Connection, cache_key: str
) -> str | None:
    """Get a cached preference interpretation.

    Args:
        conn: SQLite database connection
        cache_key: The cache key to look up

    Returns:
        Cached JSON string or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT interpretation_json FROM preference_interpretation_cache WHERE cache_key = ?",
        (cache_key,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def save_cached_preference_interpretation(
    conn: sqlite3.Connection, cache_key: str, interpretation_json: str
) -> None:
    """Save a preference interpretation to the cache.

    Args:
        conn: SQLite database connection
        cache_key: The cache key
        interpretation_json: JSON string of the interpretation
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO preference_interpretation_cache
        (cache_key, interpretation_json, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (cache_key, interpretation_json),
    )
    conn.commit()


def clear_cached_preference_interpretations(conn: sqlite3.Connection) -> int:
    """Clear all cached preference interpretations.

    Args:
        conn: SQLite database connection

    Returns:
        Number of rows deleted
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM preference_interpretation_cache")
    deleted = cursor.rowcount
    conn.commit()
    return deleted


# Enrichment status functions


def get_enrichment_status(
    conn: sqlite3.Connection, content_item_id: int
) -> EnrichmentStatusDict | None:
    """Get enrichment status for a content item.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID

    Returns:
        Enrichment status dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        """SELECT content_item_id, last_enriched_at, enrichment_provider,
                  enrichment_quality, needs_enrichment, enrichment_error
           FROM enrichment_status WHERE content_item_id = ?""",
        (content_item_id,),
    )
    row = cursor.fetchone()
    if row:
        return {
            "content_item_id": row[0],
            "last_enriched_at": row[1],
            "enrichment_provider": row[2],
            "enrichment_quality": row[3],
            "needs_enrichment": bool(row[4]),
            "enrichment_error": row[5],
        }
    return None


def write_enrichment_complete(
    cursor: sqlite3.Cursor,
    content_item_id: int,
    provider: str,
    quality: str,
) -> None:
    """Write the "enriched" status row without committing.

    Lets a caller fold the write into its own transaction and control the
    single commit point. ``mark_enrichment_complete`` wraps this for callers
    that own the connection and want an immediate commit.

    Args:
        cursor: Cursor on the caller's open transaction
        content_item_id: Content item database ID
        provider: Name of the provider that enriched the item
        quality: Match quality ("high", "medium", "not_found")
    """
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           VALUES (?, CURRENT_TIMESTAMP, ?, ?, 0, NULL)""",
        (content_item_id, provider, quality),
    )


def mark_enrichment_complete(
    conn: sqlite3.Connection,
    content_item_id: int,
    provider: str,
    quality: str,
) -> None:
    """Mark an item as successfully enriched and commit.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID
        provider: Name of the provider that enriched the item
        quality: Match quality ("high", "medium", "not_found")
    """
    write_enrichment_complete(conn.cursor(), content_item_id, provider, quality)
    conn.commit()


def mark_enrichment_failed(
    conn: sqlite3.Connection,
    content_item_id: int,
    error: str,
) -> None:
    """Mark an item's enrichment as failed and keep it queued.

    A failure means no provider ever said whether it has this item, so the
    outcome is unknown rather than settled: ``needs_enrichment`` stays 1 so the
    next enrichment run retries the item. Contrast ``mark_enrichment_complete``
    with quality ``not_found``, which records a settled miss and retires the
    item from the queue.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID
        error: Error message describing the failure
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           VALUES (?, CURRENT_TIMESTAMP, NULL, NULL, 1, ?)""",
        (content_item_id, error),
    )
    conn.commit()


def mark_item_needs_enrichment(
    conn: sqlite3.Connection,
    content_item_id: int,
) -> None:
    """Mark an item as needing enrichment.

    Args:
        conn: SQLite database connection
        content_item_id: Content item database ID
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR IGNORE INTO enrichment_status
           (content_item_id, needs_enrichment)
           VALUES (?, 1)""",
        (content_item_id,),
    )
    conn.commit()


def reset_enrichment_status(
    conn: sqlite3.Connection,
    provider: str | None = None,
    content_type: str | None = None,
    user_id: int | None = None,
) -> int:
    """Reset enrichment status for items to allow re-enrichment.

    Args:
        conn: SQLite database connection
        provider: If specified, only reset items enriched by this provider.
                  If None, reset all items.
        content_type: If specified, only reset items of this content type.
        user_id: If specified, only reset items for this user.

    Returns:
        Number of items reset
    """
    cursor = conn.cursor()
    params: list[str | int] = []

    # Join with content_items for content_type and user_id filtering
    if content_type or user_id:
        base_query = """
            UPDATE enrichment_status
            SET needs_enrichment = 1, enrichment_error = NULL
            WHERE content_item_id IN (
                SELECT es.content_item_id
                FROM enrichment_status es
                JOIN content_items ci ON es.content_item_id = ci.id
                WHERE 1=1
        """
        if provider:
            base_query += " AND es.enrichment_provider = ?"
            params.append(provider)
        if content_type:
            base_query += " AND ci.content_type = ?"
            params.append(content_type)
        if user_id:
            base_query += " AND ci.user_id = ?"
            params.append(user_id)
        base_query += ")"
        cursor.execute(base_query, params)
    elif provider:
        cursor.execute(
            """UPDATE enrichment_status
               SET needs_enrichment = 1, enrichment_error = NULL
               WHERE enrichment_provider = ?""",
            (provider,),
        )
    else:
        cursor.execute(
            """UPDATE enrichment_status
               SET needs_enrichment = 1, enrichment_error = NULL"""
        )

    updated = cursor.rowcount
    conn.commit()
    return updated


def _enrichment_count_query(
    cursor: sqlite3.Cursor,
    table_name: str,
    table_alias: str | None,
    where_clause: str,
    user_join: str,
    user_filter: str,
    user_params: tuple[int, ...],
) -> int:
    """Execute a COUNT query with optional user filtering."""
    if table_name not in _ALLOWED_ENRICHMENT_TABLES:
        raise ValueError(f"Unknown SQL table: {table_name!r}")
    if table_alias is not None and table_alias not in _ALLOWED_ENRICHMENT_ALIASES:
        raise ValueError(f"Unknown SQL table alias: {table_alias!r}")
    if where_clause not in _ALLOWED_ENRICHMENT_WHERE:
        raise ValueError(f"Unknown SQL WHERE clause: {where_clause!r}")
    if user_join not in _ALLOWED_ENRICHMENT_JOINS:
        raise ValueError(f"Unknown SQL JOIN clause: {user_join!r}")
    if user_filter not in _ALLOWED_ENRICHMENT_FILTERS:
        raise ValueError(f"Unknown SQL filter: {user_filter!r}")
    from_clause = f"{table_name} {table_alias}" if table_alias else table_name
    query = f"SELECT COUNT(*) FROM {from_clause}{user_join} WHERE {where_clause}{user_filter}"
    cursor.execute(query, user_params)
    result: int = cursor.fetchone()[0]
    return result


def _enrichment_group_query(
    cursor: sqlite3.Cursor,
    select_col: str,
    table_name: str,
    table_alias: str | None,
    user_join: str,
    user_filter: str,
    user_params: tuple[int, ...],
) -> dict[str, int]:
    """Execute a GROUP BY query with optional user filtering."""
    if table_name not in _ALLOWED_ENRICHMENT_TABLES:
        raise ValueError(f"Unknown SQL table: {table_name!r}")
    if table_alias is not None and table_alias not in _ALLOWED_ENRICHMENT_ALIASES:
        raise ValueError(f"Unknown SQL table alias: {table_alias!r}")
    if select_col not in _ALLOWED_ENRICHMENT_COLUMNS:
        raise ValueError(f"Unknown enrichment column: {select_col!r}")
    if user_join not in _ALLOWED_ENRICHMENT_JOINS:
        raise ValueError(f"Unknown SQL JOIN clause: {user_join!r}")
    if user_filter not in _ALLOWED_ENRICHMENT_FILTERS:
        raise ValueError(f"Unknown SQL filter: {user_filter!r}")
    from_clause = f"{table_name} {table_alias}" if table_alias else table_name
    col_prefix = f"{table_alias}.{select_col}" if table_alias else select_col
    query = (
        f"SELECT {col_prefix}, COUNT(*) FROM {from_clause}{user_join}"
        f" WHERE {col_prefix} IS NOT NULL{user_filter}"
        f" GROUP BY {col_prefix}"
    )
    cursor.execute(query, user_params)
    return {row[0]: row[1] for row in cursor.fetchall()}


def get_enrichment_stats(
    conn: sqlite3.Connection,
    user_id: int | None = None,
) -> dict[str, int | dict[str, int]]:
    """Get overall enrichment statistics.

    ``enriched``, ``pending`` and ``failed`` read the same retry state and are
    mutually exclusive: a failed item is queued for retry like a pending one,
    but ``pending`` requires ``enrichment_error IS NULL``, so it is reported
    only as failed — the more specific of the two, and the one the operator
    may need to act on. Interfaces list these counts side by side, so an item
    appearing in two of them would read as two items.

    ``not_found`` is a different measure: it counts a quality label rather
    than a retry state, so it overlaps the other three instead of extending
    them into a partition. An item that settled as not_found and was then
    re-queued by :func:`reset_enrichment_status` — which clears the error but
    leaves the quality alone — is counted under both ``pending`` and
    ``not_found``. The four buckets therefore need not sum to ``total``.

    Args:
        conn: SQLite database connection
        user_id: If specified, only count items for this user.

    Returns:
        Dict with enrichment statistics
    """
    cursor = conn.cursor()

    # Build reusable query parts for optional user filtering
    user_join = (
        " JOIN content_items ci ON es.content_item_id = ci.id" if user_id else ""
    )
    user_filter = " AND ci.user_id = ?" if user_id else ""
    user_params: tuple[int, ...] = (user_id,) if user_id else ()

    # Use 'es' alias when joining, plain table name otherwise
    es_alias: str | None = "es" if user_id else None

    count_args = (
        cursor,
        "enrichment_status",
        es_alias,
        "1=1",
        user_join,
        user_filter,
        user_params,
    )

    # total_items doesn't use the enrichment join, so query directly
    if user_id:
        cursor.execute(
            "SELECT COUNT(*) FROM content_items WHERE user_id = ?", (user_id,)
        )
        total_items: int = cursor.fetchone()[0]
    else:
        total_items = _enrichment_count_query(
            cursor,
            "content_items",
            None,
            "1=1",
            user_join,
            user_filter,
            user_params,
        )

    tracked_items: int = _enrichment_count_query(*count_args)
    needs_enrichment: int = _enrichment_count_query(
        cursor,
        "enrichment_status",
        es_alias,
        (
            "es.needs_enrichment = 1 AND es.enrichment_error IS NULL"
            if user_id
            else "needs_enrichment = 1 AND enrichment_error IS NULL"
        ),
        user_join,
        user_filter,
        user_params,
    )
    enriched: int = _enrichment_count_query(
        cursor,
        "enrichment_status",
        es_alias,
        (
            "es.needs_enrichment = 0 AND es.enrichment_error IS NULL"
            " AND es.enrichment_provider != 'none'"
            if user_id
            else "needs_enrichment = 0 AND enrichment_error IS NULL"
            " AND enrichment_provider != 'none'"
        ),
        user_join,
        user_filter,
        user_params,
    )
    failed: int = _enrichment_count_query(
        cursor,
        "enrichment_status",
        es_alias,
        (
            "es.enrichment_error IS NOT NULL"
            if user_id
            else "enrichment_error IS NOT NULL"
        ),
        user_join,
        user_filter,
        user_params,
    )

    untracked = total_items - tracked_items

    by_provider = _enrichment_group_query(
        cursor,
        "enrichment_provider",
        "enrichment_status",
        es_alias,
        user_join,
        user_filter,
        user_params,
    )
    by_quality = _enrichment_group_query(
        cursor,
        "enrichment_quality",
        "enrichment_status",
        es_alias,
        user_join,
        user_filter,
        user_params,
    )

    return {
        "total": total_items,
        "enriched": enriched,
        "pending": needs_enrichment + untracked,
        "not_found": by_quality.get("not_found", 0),
        "failed": failed,
        "by_provider": by_provider,
        "by_quality": by_quality,
    }


# Core memory functions


def get_core_memories(
    conn: sqlite3.Connection,
    user_id: int,
    active_only: bool = True,
    memory_type: str | None = None,
) -> list[CoreMemoryDict]:
    """Get core memories for a user.

    Args:
        conn: SQLite database connection
        user_id: User ID
        active_only: If True, only return active memories
        memory_type: Filter by type ("user_stated" or "inferred")

    Returns:
        List of memory dicts
    """
    cursor = conn.cursor()
    query = """
        SELECT id, user_id, memory_text, memory_type, source, confidence,
               created_at, updated_at, is_active
        FROM core_memories
        WHERE user_id = ?
    """
    params: list[int | str] = [user_id]

    if active_only:
        query += " AND is_active = 1"

    if memory_type:
        query += " AND memory_type = ?"
        params.append(memory_type)

    query += " ORDER BY created_at DESC"

    cursor.execute(query, params)
    memories: list[CoreMemoryDict] = []
    for row in cursor.fetchall():
        memories.append(
            CoreMemoryDict(
                id=row[0],
                user_id=row[1],
                memory_text=row[2],
                memory_type=row[3],
                source=row[4],
                confidence=row[5],
                created_at=row[6],
                updated_at=row[7],
                is_active=bool(row[8]),
            )
        )
    return memories


def save_core_memory(
    conn: sqlite3.Connection,
    user_id: int,
    memory_text: str,
    memory_type: str,
    source: str,
    confidence: float = 1.0,
) -> int:
    """Save a new core memory.

    Args:
        conn: SQLite database connection
        user_id: User ID
        memory_text: The preference statement
        memory_type: "user_stated" or "inferred"
        source: "conversation", "rating_pattern", or "manual"
        confidence: Confidence score (0.0-1.0)

    Returns:
        New memory ID
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO core_memories
        (user_id, memory_text, memory_type, source, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, memory_text, memory_type, source, confidence),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def update_core_memory(
    conn: sqlite3.Connection,
    memory_id: int,
    memory_text: str | None = None,
    is_active: bool | None = None,
) -> bool:
    """Update a core memory.

    Args:
        conn: SQLite database connection
        memory_id: Memory ID to update
        memory_text: New memory text (optional)
        is_active: New active status (optional)

    Returns:
        True if updated, False if not found
    """
    if memory_text is None and is_active is None:
        return False

    cursor = conn.cursor()
    # COALESCE(?, col) — NULL means "keep existing", non-NULL means "update".
    # is_active=None → NULL (preserves existing), False → 0, True → 1.
    cursor.execute(
        """UPDATE core_memories
           SET memory_text = COALESCE(?, memory_text),
               is_active   = COALESCE(?, is_active),
               updated_at  = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            memory_text,
            int(is_active) if is_active is not None else None,
            memory_id,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_core_memory(conn: sqlite3.Connection, memory_id: int) -> bool:
    """Delete a core memory.

    Args:
        conn: SQLite database connection
        memory_id: Memory ID to delete

    Returns:
        True if deleted, False if not found
    """
    cursor = conn.cursor()
    cursor.execute("DELETE FROM core_memories WHERE id = ?", (memory_id,))
    conn.commit()
    return cursor.rowcount > 0


# Conversation message functions


def get_conversation_history(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int = 50,
) -> list[ConversationMessageDict]:
    """Get recent conversation history for a user.

    Args:
        conn: SQLite database connection
        user_id: User ID
        limit: Maximum number of messages to return

    Returns:
        List of message dicts ordered by created_at ascending (oldest first)
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, role, content, tool_calls, created_at
        FROM conversation_messages
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    messages: list[ConversationMessageDict] = []
    for row in cursor.fetchall():
        tool_calls = None
        if row[4]:
            try:
                tool_calls = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                pass
        messages.append(
            ConversationMessageDict(
                id=row[0],
                user_id=row[1],
                role=row[2],
                content=row[3],
                tool_calls=tool_calls,
                created_at=row[5],
            )
        )
    # Return in chronological order (oldest first)
    return list(reversed(messages))


def save_conversation_message(
    conn: sqlite3.Connection,
    user_id: int,
    role: str,
    content: str,
    tool_calls: list[dict] | None = None,
) -> int:
    """Save a conversation message.

    Args:
        conn: SQLite database connection
        user_id: User ID
        role: "user" or "assistant"
        content: Message content
        tool_calls: Optional list of tool calls made

    Returns:
        New message ID
    """
    cursor = conn.cursor()
    tool_calls_json = json.dumps(tool_calls) if tool_calls else None
    cursor.execute(
        """
        INSERT INTO conversation_messages
        (user_id, role, content, tool_calls)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, role, content, tool_calls_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def clear_conversation_history(conn: sqlite3.Connection, user_id: int) -> int:
    """Clear conversation history for a user (the "reset" functionality).

    Args:
        conn: SQLite database connection
        user_id: User ID

    Returns:
        Number of messages deleted
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM conversation_messages WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    return cursor.rowcount


# Preference profile functions


def get_preference_profile(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Get the preference profile for a user.

    Args:
        conn: SQLite database connection
        user_id: User ID

    Returns:
        Profile dict or None if not found
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, profile_json, generated_at
        FROM preference_profiles
        WHERE user_id = ?
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    if row:
        try:
            profile_data = json.loads(row[2])
        except (json.JSONDecodeError, TypeError):
            profile_data = {}
        return {
            "id": row[0],
            "user_id": row[1],
            "profile": profile_data,
            "generated_at": row[3],
        }
    return None


def save_preference_profile(
    conn: sqlite3.Connection,
    user_id: int,
    profile_json: str,
) -> int:
    """Save or update a preference profile.

    Uses UPSERT to replace existing profile for the user.

    Args:
        conn: SQLite database connection
        user_id: User ID
        profile_json: JSON string of the profile

    Returns:
        Profile ID
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO preference_profiles (user_id, profile_json, generated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            profile_json = excluded.profile_json,
            generated_at = CURRENT_TIMESTAMP
        """,
        (user_id, profile_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


# Credential functions


def get_credential(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    credential_key: str,
) -> str | None:
    """Get a single credential value (raw/encrypted).

    Args:
        conn: SQLite database connection
        user_id: User ID
        source_id: Source identifier (e.g. "gog", "steam")
        credential_key: Credential field name (e.g. "refresh_token")

    Returns:
        Raw (encrypted) credential value, or None if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT credential_value FROM credentials "
        "WHERE user_id = ? AND source_id = ? AND credential_key = ?",
        (user_id, source_id, credential_key),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def save_credential(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    credential_key: str,
    credential_value: str,
) -> None:
    """Save or update a credential (UPSERT).

    Args:
        conn: SQLite database connection
        user_id: User ID
        source_id: Source identifier
        credential_key: Credential field name
        credential_value: Value to store (should be pre-encrypted)
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO credentials (user_id, source_id, credential_key, credential_value, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, source_id, credential_key) DO UPDATE SET
            credential_value = excluded.credential_value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, source_id, credential_key, credential_value),
    )
    conn.commit()


def delete_credential(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    credential_key: str,
) -> bool:
    """Delete a credential row.

    Args:
        conn: SQLite database connection
        user_id: User ID
        source_id: Source identifier
        credential_key: Credential field name

    Returns:
        True if a row was deleted, False if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM credentials "
        "WHERE user_id = ? AND source_id = ? AND credential_key = ?",
        (user_id, source_id, credential_key),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_credentials_for_source(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
) -> int:
    """Delete every credential row for a source.

    Args:
        conn: SQLite database connection
        user_id: User ID
        source_id: Source identifier

    Returns:
        Number of rows deleted.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM credentials WHERE user_id = ? AND source_id = ?",
        (user_id, source_id),
    )
    conn.commit()
    return cursor.rowcount


def credential_row_exists(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    credential_key: str,
) -> bool:
    """Check if a credential row exists (without decrypting).

    Args:
        conn: SQLite database connection
        user_id: User ID
        source_id: Source identifier
        credential_key: Credential field name

    Returns:
        True if a row exists in the credentials table.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM credentials "
        "WHERE user_id = ? AND source_id = ? AND credential_key = ?",
        (user_id, source_id, credential_key),
    )
    return cursor.fetchone() is not None


def get_credentials_for_source(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
) -> dict[str, str]:
    """Get all credential key-value pairs for a source (raw/encrypted).

    Args:
        conn: SQLite database connection
        user_id: User ID
        source_id: Source identifier

    Returns:
        Dict mapping credential_key to raw (encrypted) credential_value.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT credential_key, credential_value FROM credentials "
        "WHERE user_id = ? AND source_id = ?",
        (user_id, source_id),
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


# Source config functions


def _row_to_source_config(row: sqlite3.Row) -> SourceConfigRow:
    """Map a ``source_configs`` row to a typed dict.

    ``conn.row_factory`` is set to ``sqlite3.Row`` by ``create_schema``, so
    every connection coming through this module supports column-name access.
    Using names instead of positional indexes keeps mappings safe if the
    SELECT column order ever drifts.
    """
    return SourceConfigRow(
        source_id=row["source_id"],
        plugin=row["plugin"],
        config_json=row["config_json"],
        enabled=row["enabled"],
        migrated_at=row["migrated_at"],
        updated_at=row["updated_at"],
    )


def get_source_config(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
) -> SourceConfigRow | None:
    """Get the migrated source config row for a (user, source).

    Returns ``None`` when the source has not been migrated to the database
    yet — callers should fall back to the YAML config in that case.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_id, plugin, config_json, enabled, migrated_at, updated_at "
        "FROM source_configs WHERE user_id = ? AND source_id = ?",
        (user_id, source_id),
    )
    row = cursor.fetchone()
    return _row_to_source_config(row) if row else None


def upsert_source_config(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    plugin: str,
    config_json: str,
    enabled: bool,
) -> None:
    """Insert or update a migrated source config (UPSERT).

    On insert ``migrated_at`` is set to ``CURRENT_TIMESTAMP``. On update only
    ``updated_at`` advances — ``migrated_at`` is preserved as the original
    migration moment.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO source_configs (
            user_id, source_id, plugin, config_json, enabled,
            migrated_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, source_id) DO UPDATE SET
            plugin = excluded.plugin,
            config_json = excluded.config_json,
            enabled = excluded.enabled,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, source_id, plugin, config_json, 1 if enabled else 0),
    )
    conn.commit()


def set_source_config_enabled(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    enabled: bool,
) -> bool:
    """Toggle the enabled flag for a migrated source.

    Returns ``True`` if a row was updated, ``False`` if the source has not
    been migrated yet (caller should ignore or surface a 404).
    """
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE source_configs SET enabled = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ? AND source_id = ?",
        (1 if enabled else 0, user_id, source_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_source_config(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
) -> bool:
    """Delete a migrated source config row.

    Returns ``True`` if a row was deleted, ``False`` if not found.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM source_configs WHERE user_id = ? AND source_id = ?",
        (user_id, source_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_source_configs(
    conn: sqlite3.Connection,
    user_id: int,
) -> list[SourceConfigRow]:
    """List every migrated source config for a user."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT source_id, plugin, config_json, enabled, migrated_at, updated_at "
        "FROM source_configs WHERE user_id = ? ORDER BY source_id",
        (user_id,),
    )
    return [_row_to_source_config(row) for row in cursor.fetchall()]


# Settings functions (global/system config, key -> JSON-encoded value)


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    """Get the raw JSON-encoded value for a settings key.

    Returns ``None`` when the key has not been stored — callers should fall
    back to the YAML config in that case.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT value_json FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value_json: str) -> None:
    """Insert or update a settings value (UPSERT).

    *value_json* must be a JSON-encoded string; the caller owns serialisation.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO settings (key, value_json, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value_json = excluded.value_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value_json),
    )
    conn.commit()


def list_settings(conn: sqlite3.Connection) -> dict[str, str]:
    """Return every stored setting as a key -> raw JSON string mapping."""
    cursor = conn.cursor()
    cursor.execute("SELECT key, value_json FROM settings")
    return {row[0]: row[1] for row in cursor.fetchall()}


def delete_setting(conn: sqlite3.Connection, key: str) -> None:
    """Delete a settings row by key. No error if the key is absent."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
