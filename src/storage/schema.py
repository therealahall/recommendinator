"""Database schema definitions."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, TypedDict

from src.models.detail_fields import (
    DETAIL_FIELDS,
    FieldKind,
    text_names,
    to_json_array,
    to_text,
)
from src.storage.derived import backfill_derived_columns
from src.storage.merge import (
    normalize_creator_for_matching,
    normalize_title_for_matching,
)
from src.utils.series import split_series_from_title


class EnrichmentStatusDict(TypedDict):
    """Enrichment status for a content item."""

    content_item_id: int
    last_enriched_at: str | None
    enrichment_provider: str | None
    enrichment_quality: str | None
    needs_enrichment: bool
    enrichment_error: str | None


class UserDict(TypedDict):
    """A user record."""

    id: int
    username: str
    display_name: str | None
    created_at: str
    settings: dict[str, Any] | None


class PreferenceProfileRow(TypedDict):
    """A ``preference_profiles`` row, with ``profile_json`` already parsed."""

    id: int
    user_id: int
    profile: dict[str, Any]
    generated_at: str


class SourceConfigRow(TypedDict):
    """Raw row from the source_configs table."""

    source_id: str
    plugin: str
    config_json: str
    enabled: int
    sync_interval: str | None
    migrated_at: str
    updated_at: str


class SourceConfigDict(TypedDict):
    """Parsed source config record returned by SourceConfigStore.

    ``config`` is the deserialised non-sensitive config dict; sensitive
    values stay in the encrypted ``credentials`` table and must be merged in
    by ``resolve_inputs`` at sync time.
    """

    source_id: str
    plugin: str
    config: dict[str, Any]
    enabled: bool
    sync_interval: str | None
    migrated_at: str
    updated_at: str


SyncRunStatus = Literal["completed", "failed"]


class SyncRunDict(TypedDict):
    id: int
    source_id: str
    started_at: str
    finished_at: str
    status: SyncRunStatus
    items_added: int
    items_updated: int
    items_unchanged: int
    total_items: int
    errors: list[str]
    omitted_errors: int


# One-time steps, guarded by the stored ``PRAGMA user_version``: 1 and 2 clear
# seeded ``settings`` rows, 3 repairs legacy content rows, 6 prunes orphaned
# leaves.

# 16 reduces a list column holding an object and clears a re-queued item's
# stale quality; 17 splits a crammed series title, drops a placeholder author,
# folds a company name, re-normalizes every title and re-derives every row's
# sort and search columns; 18 rebuilds sync_runs, whose unfinished row is a
# claim; 19 carries the UI theme out of the preference blob.

# Changing ``normalize_title_for_matching``, ``get_sort_title`` or
# ``build_search_text`` needs a bump and a step to rewrite what the old one
# stored, or dedup lookups stop matching and duplicates accumulate in silence.
_SCHEMA_VERSION = 19

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

# Prefixes of the leaves the AI removal orphaned. By prefix rather than by key,
# because these sections carried leaves across several releases and a row from
# any of them is equally unreachable now.
_ORPHANED_SETTING_PREFIXES: tuple[str, ...] = (
    "features.",
    "ollama.",
    "conversation.",
)

_CONTENT_ITEMS_TABLE = """
    CREATE TABLE IF NOT EXISTS content_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        normalized_title TEXT,
        sort_title TEXT,
        search_text TEXT,
        content_type TEXT NOT NULL,
        status TEXT NOT NULL,
        rating INTEGER CHECK (rating >= 1 AND rating <= 5),
        review TEXT,
        date_completed DATE,
        ignored BOOLEAN DEFAULT 0,
        -- Source id, never a plugin name: two sources on one plugin must
        -- stay tellable apart.
        source TEXT,
        -- The item this row was merged into. Set rather than deleted, so the
        -- merge is reversible; every read filters it, and CASCADE because
        -- deleting the survivor deletes the one item the user sees.
        merged_into INTEGER REFERENCES content_items(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

# Parenthesised so no source the app creates can be named it: every door
# validates against ``SOURCE_ID_PATTERN`` (a lowercase letter, then letters,
# digits, _ and -). Only a hand-written ``inputs`` key, taken verbatim from
# config.yaml, could collide.
_LEGACY_EXTERNAL_ID_SOURCE = "(legacy)"

# Declared once because ``create_schema`` creates them from here and the guard
# on the content_items rebuild needs to know which tables follow it.
_CONTENT_ITEM_CHILDREN: dict[str, str] = {
    # Steam's 440 and GOG's 440 differ, and Trakt's movie 1 from its show 1, so
    # the key needs both; type is copied here because UNIQUE spans one table.
    "content_item_external_ids": """
        CREATE TABLE IF NOT EXISTS content_item_external_ids (
            content_item_id INTEGER NOT NULL
                REFERENCES content_items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            content_type TEXT NOT NULL,
            PRIMARY KEY (content_item_id, source),
            UNIQUE (user_id, source, external_id, content_type)
        )
    """,
    "book_details": """
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
    """,
    "movie_details": """
        CREATE TABLE IF NOT EXISTS movie_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            director TEXT,
            runtime INTEGER,  -- minutes
            release_year INTEGER,
            genres TEXT,  -- JSON array of genres
            studio TEXT,
            metadata TEXT
        )
    """,
    "tv_show_details": """
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
    """,
    "video_game_details": """
        CREATE TABLE IF NOT EXISTS video_game_details (
            content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
            developer TEXT,
            publisher TEXT,
            platforms TEXT,  -- JSON array of platforms
            genres TEXT,  -- JSON array of genres
            release_year INTEGER,
            metadata TEXT
        )
    """,
    "content_item_merges": """
        CREATE TABLE IF NOT EXISTS content_item_merges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survivor_id INTEGER NOT NULL
                REFERENCES content_items(id) ON DELETE CASCADE,
            -- UNIQUE because an undone merge is deleted rather than kept as
            -- history: this table is the merges in force, one per absorbed row.
            absorbed_id INTEGER NOT NULL UNIQUE
                REFERENCES content_items(id) ON DELETE CASCADE,
            evidence TEXT NOT NULL,
            evidence_detail TEXT,
            merged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            -- What the merge overwrote on the survivor, as JSON. The absorbed
            -- row needs no such record: nothing writes it.
            restore_json TEXT NOT NULL
        )
    """,
    "content_item_duplicate_declines": """
        CREATE TABLE IF NOT EXISTS content_item_duplicate_declines (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            -- Ordered low, high so one pair is one row either way round. Ids
            -- are AUTOINCREMENT, so no new row inherits a deleted one's refusal.
            lower_item_id INTEGER NOT NULL
                REFERENCES content_items(id) ON DELETE CASCADE,
            higher_item_id INTEGER NOT NULL
                REFERENCES content_items(id) ON DELETE CASCADE,
            PRIMARY KEY (lower_item_id, higher_item_id)
        )
    """,
    "enrichment_status": """
        CREATE TABLE IF NOT EXISTS enrichment_status (
            content_item_id INTEGER PRIMARY KEY
                REFERENCES content_items(id) ON DELETE CASCADE,
            last_enriched_at TIMESTAMP,
            enrichment_provider TEXT,
            enrichment_quality TEXT,
            needs_enrichment BOOLEAN DEFAULT 1,
            enrichment_error TEXT
        )
    """,
}

_SYNC_RUNS_TABLE = (
    "CREATE TABLE IF NOT EXISTS sync_runs ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "source_id TEXT NOT NULL, "
    "started_at TIMESTAMP NOT NULL, "
    "finished_at TIMESTAMP, "
    "status TEXT NOT NULL, "
    "items_added INTEGER NOT NULL DEFAULT 0, "
    "items_updated INTEGER NOT NULL DEFAULT 0, "
    "items_unchanged INTEGER NOT NULL DEFAULT 0, "
    "total_items INTEGER NOT NULL DEFAULT 0, "
    "errors_json TEXT NOT NULL DEFAULT '[]', "
    "omitted_errors INTEGER NOT NULL DEFAULT 0, "
    "heartbeat_at TIMESTAMP"
    ")"
)

_CONTENT_ITEM_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_content_user ON content_items(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_content_type ON content_items(content_type)",
    "CREATE INDEX IF NOT EXISTS idx_status ON content_items(status)",
    "CREATE INDEX IF NOT EXISTS idx_rating ON content_items(rating)",
    "CREATE INDEX IF NOT EXISTS idx_date_completed ON content_items(date_completed)",
    "CREATE INDEX IF NOT EXISTS idx_source ON content_items(source)",
    "CREATE INDEX IF NOT EXISTS idx_user_type ON content_items(user_id, content_type)",
    "CREATE INDEX IF NOT EXISTS idx_user_status ON content_items(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_ci_normalized_title"
    " ON content_items(user_id, content_type, normalized_title)",
    "CREATE INDEX IF NOT EXISTS idx_ci_sort_title"
    " ON content_items(user_id, sort_title, id)",
    "CREATE INDEX IF NOT EXISTS idx_ci_merged_into ON content_items(merged_into)",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the database schema.

    Sets ``conn.row_factory`` on the caller's connection unconditionally.
    """
    # Required by the steps below, which read columns by name.
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    stored_version = _stored_schema_version(cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            settings TEXT  -- JSON for per-user settings (scorer weights, etc.)
        )
        """)

    # Login credentials for that row, NULL until someone claims the instance —
    # which is how the web layer tells a fresh install from a claimed one.
    _add_column_if_not_exists(cursor, "users", "password_hash", "TEXT")
    _add_column_if_not_exists(cursor, "users", "password_salt", "TEXT")
    _add_column_if_not_exists(cursor, "users", "password_updated_at", "TIMESTAMP")

    # Web sessions, keyed by a hash of the token so that reading this table
    # hands over no live session (see src/storage/accounts.py).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            last_seen_at TIMESTAMP NOT NULL
        )
        """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")

    cursor.execute(_CONTENT_ITEMS_TABLE)

    for child_statement in _CONTENT_ITEM_CHILDREN.values():
        cursor.execute(child_statement)

    # Ahead of every write below, because it cannot open a transaction while
    # one is already open. It commits before the version stamp, so the guard is
    # the column — a version guard would rebuild a table with no external_id.
    if _has_column(cursor, "content_items", "external_id"):
        _move_external_ids_off_content_items(conn)
    # Ahead of the indexes, one of which reads it, and of the release.
    _add_column_if_not_exists(
        cursor,
        "content_items",
        "merged_into",
        "INTEGER REFERENCES content_items(id) ON DELETE CASCADE",
    )
    for index_statement in _CONTENT_ITEM_INDEXES:
        cursor.execute(index_statement)

    cursor.execute("""
        INSERT OR IGNORE INTO users (id, username, display_name)
        VALUES (1, 'default', 'Default User')
        """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_book_author ON book_details(author)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_movie_director ON movie_details(director)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_game_developer ON video_game_details(developer)"
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_enrichment_needs "
        "ON enrichment_status(needs_enrichment)"
    )

    _add_column_if_not_exists(cursor, "book_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "book_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "movie_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "movie_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "tv_show_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "tv_show_details", "description", "TEXT")
    _add_column_if_not_exists(cursor, "video_game_details", "tags", "TEXT")
    _add_column_if_not_exists(cursor, "video_game_details", "description", "TEXT")

    _add_column_if_not_exists(cursor, "content_items", "ignored", "BOOLEAN DEFAULT 0")
    _add_column_if_not_exists(cursor, "content_items", "normalized_title", "TEXT")

    # Columns derived from the title, the creator and the series, so the
    # library list is ordered and searched in SQL (see src/storage/derived.py).
    # Added ahead of the steps below, which rewrite them.
    _add_column_if_not_exists(cursor, "content_items", "sort_title", "TEXT")
    _add_column_if_not_exists(cursor, "content_items", "search_text", "TEXT")
    if stored_version < 3:
        _repair_legacy_content_rows(cursor)
    if stored_version < 16:
        _reduce_non_scalar_list_columns(cursor)
        _clear_quality_on_requeued_items(cursor)
    if stored_version < 17:
        # An upgraded library does not collapse its duplicates on open. It
        # rewrites their keys; the save door decides each pair on the next
        # sync, and the merge door decides what that leaves.
        _split_crammed_series_titles(cursor)
        _clear_placeholder_authors(cursor)
        _fold_stranded_company_names(cursor)
        _renormalize_titles(cursor)
        _clear_derived_columns(cursor)

    # Filled after the repair, which recovers a creator that existed only in a
    # blob. Unguarded because the fill selects the rows that need it rather
    # than the databases that have never had one.
    backfill_derived_columns(cursor)

    # Preference profile snapshots (regenerated periodically)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS preference_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            profile_json TEXT NOT NULL,  -- Distilled preference summary
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id)  -- One active profile per user
        )
    """)

    # What the interface looks like for one user. Its own table rather than a
    # key in ``users.settings``: that blob is the preference config, and
    # resetting the scoring preferences must not change how the app looks.
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS user_ui_settings ("
        "user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, "
        "theme TEXT NOT NULL DEFAULT ''"
        ")"
    )
    if stored_version < 19:
        _move_themes_off_preference_blob(cursor)

    # Credentials table for encrypted source credentials (API keys, tokens)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            credential_key TEXT NOT NULL,
            credential_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, source_id, credential_key)
        )
        """)

    # Source configs table: per-source non-sensitive config that has been
    # migrated from config.yaml into the database. Once a row exists for
    # (user_id, source_id), the YAML entry for that source is no longer
    # consulted by resolve_inputs — the database is the source of truth.
    # Sensitive fields (API keys, tokens) keep going through the encrypted
    # ``credentials`` table above; this table holds the rest.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            plugin TEXT NOT NULL,
            config_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            -- Automatic-sync cadence. NULL is the plugin's own default;
            -- 'off' is never.
            sync_interval TEXT,
            migrated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, source_id)
        )
        """)
    _add_column_if_not_exists(cursor, "source_configs", "sync_interval", "TEXT")

    cursor.execute(_SYNC_RUNS_TABLE)
    if stored_version < 18:
        _rebuild_sync_runs(cursor)
    _add_column_if_not_exists(
        cursor, "sync_runs", "omitted_errors", "INTEGER NOT NULL DEFAULT 0"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_runs_source "
        "ON sync_runs(user_id, source_id, started_at DESC)"
    )
    # The claim: neither process can see the other's memory, so this refuses.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_runs_claim "
        "ON sync_runs(user_id, source_id) WHERE finished_at IS NULL"
    )

    # One row, because one job runs at a time. In the database rather than on
    # the manager: the CLI and the server are separate processes.
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS enrichment_job ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "running INTEGER NOT NULL DEFAULT 0, "
        "completed INTEGER NOT NULL DEFAULT 0, "
        "cancelled INTEGER NOT NULL DEFAULT 0, "
        "stop_requested INTEGER NOT NULL DEFAULT 0, "
        "items_processed INTEGER NOT NULL DEFAULT 0, "
        "items_enriched INTEGER NOT NULL DEFAULT 0, "
        "items_failed INTEGER NOT NULL DEFAULT 0, "
        "items_not_found INTEGER NOT NULL DEFAULT 0, "
        "total_items INTEGER NOT NULL DEFAULT 0, "
        "current_item TEXT NOT NULL DEFAULT '', "
        "content_type TEXT, "
        "errors_json TEXT NOT NULL DEFAULT '[]', "
        "started_at TIMESTAMP, "
        "finished_at TIMESTAMP, "
        "heartbeat_at TIMESTAMP"
        ")"
    )

    # Global/system settings: dotted leaf key -> JSON-encoded value. Holds ONLY
    # the leaves a user explicitly set via the Settings page / `settings` CLI,
    # keyed by dotted path (e.g. "recommendations.default_count"). Nothing is
    # seeded here on boot; a stored leaf wins over YAML and the registry const
    # default. Per-source config and credentials keep their own tables above.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

    # Version-guarded one-time settings migrations (see _migrate_settings_table).
    _migrate_settings_table(cursor, stored_version)

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


def _like_prefix(prefix: str) -> str:
    """Return the ``LIKE`` pattern matching every key starting with *prefix*."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


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

    Each reads the whole library, so both are guarded to run once per
    database: no current write path produces what either looks for, and a
    row a pass deliberately declines to settle — a fill-only column holding
    another producer's object, say — would otherwise be re-read on every open
    for the life of the database.

    They share the transaction ``create_schema``'s ``INSERT OR IGNORE INTO
    users`` opened and its commit closes. Nothing between them may commit, and
    that connection must keep implicit transactions, or a step that raises
    leaves the ones before it committed over a half-upgraded library.
    """
    # Approximate normalization for a column the caller's ALTER may have just
    # added; step 17 corrects it with the full Python function.
    cursor.execute(
        "UPDATE content_items SET normalized_title = lower(title) "
        "WHERE normalized_title IS NULL"
    )
    _migrate_stranded_detail_shapes(cursor)


@contextmanager
def _rebuilding(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold the connection where a rebuild is safe, and commit its transaction.

    Foreign keys off so the drop ending a rebuild cannot cascade; legacy
    renaming on so it leaves the children's REFERENCES alone. Neither pragma
    survives inside a transaction.
    """
    enforced = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.execute("BEGIN")
        yield
        conn.commit()
    finally:
        # Restoring a pragma is a silent no-op while a transaction is open, so
        # a rebuild that raised has to be rolled back before they go back.
        if conn.in_transaction:
            conn.rollback()
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute(f"PRAGMA foreign_keys = {int(enforced)}")


def _move_external_ids_off_content_items(conn: sqlite3.Connection) -> None:
    with _rebuilding(conn):
        _rebuild_content_items(conn.cursor())


def _foreign_key_parents(cursor: sqlite3.Cursor, table: str) -> set[str]:
    """The tables *table* declares a foreign key to; empty when it has none."""
    cursor.execute(f"PRAGMA foreign_key_list({table})")
    return {row["table"] for row in cursor.fetchall()}


def _assert_no_child_followed(cursor: sqlite3.Cursor, scratch: str) -> None:
    """Refuse a rename that dragged the children onto *scratch*.

    A pragma is a no-op inside a transaction, so only the caller's ordering
    keeps these clauses on content_items — and an unchecked rebuild commits a
    database no write can use.
    """
    followed = sorted(
        table
        for table in _CONTENT_ITEM_CHILDREN
        if scratch in _foreign_key_parents(cursor, table)
    )
    if followed:
        raise RuntimeError(
            f"Renaming content_items rewrote the foreign keys of {', '.join(followed)}:"
            " the legacy_alter_table pragma did not take effect."
        )


def _rebuild_content_items(cursor: sqlite3.Cursor) -> None:
    """``source`` names the last source to sync the row, not the one whose id
    ``external_id`` holds. Filing legacy ids under a name no source can claim
    keeps every source's next sync on the title path instead of a duplicate."""
    cursor.execute("ALTER TABLE content_items RENAME TO content_items_old")
    _assert_no_child_followed(cursor, "content_items_old")
    cursor.execute(_CONTENT_ITEMS_TABLE)
    # Read off the two tables rather than a written-down list: the rebuild is
    # irreversible and drops the old table, so a column the list forgot would
    # take the operator's ratings and reviews with it, once and in silence.
    old_columns = set(_column_names(cursor, "content_items_old"))
    carried = ", ".join(
        column
        for column in _column_names(cursor, "content_items")
        if column in old_columns
    )
    cursor.execute(
        f"INSERT INTO content_items ({carried})"
        f" SELECT {carried} FROM content_items_old"
    )

    cursor.execute(
        """INSERT INTO content_item_external_ids
               (content_item_id, user_id, source, external_id, content_type)
           SELECT id, user_id, ?, external_id, content_type
             FROM content_items_old
            WHERE external_id IS NOT NULL""",
        (_LEGACY_EXTERNAL_ID_SOURCE,),
    )

    cursor.execute("DROP TABLE content_items_old")


def _rebuild_sync_runs(cursor: sqlite3.Cursor) -> None:
    """A rebuild because SQLite cannot drop ``finished_at``'s NOT NULL in place."""
    cursor.execute("ALTER TABLE sync_runs RENAME TO sync_runs_old")
    cursor.execute(_SYNC_RUNS_TABLE)
    old_columns = set(_column_names(cursor, "sync_runs_old"))
    carried = ", ".join(
        column for column in _column_names(cursor, "sync_runs") if column in old_columns
    )
    cursor.execute(
        f"INSERT INTO sync_runs ({carried}) SELECT {carried} FROM sync_runs_old"
    )
    cursor.execute("DROP TABLE sync_runs_old")


def _move_themes_off_preference_blob(cursor: sqlite3.Cursor) -> None:
    """Carry each user's stored theme into ``user_ui_settings``.

    Only this step can reach the theme an upgrading operator picked; without
    it, the first open after the upgrade paints the default instead.
    """
    cursor.execute("SELECT id, settings FROM users WHERE settings IS NOT NULL")
    for row in cursor.fetchall():
        try:
            settings = json.loads(row["settings"])
        except (json.JSONDecodeError, TypeError):
            continue
        blob = settings.get("preference_config") if isinstance(settings, dict) else None
        theme = blob.get("theme") if isinstance(blob, dict) else None
        if isinstance(theme, str) and theme:
            cursor.execute(
                "INSERT OR IGNORE INTO user_ui_settings (user_id, theme) VALUES (?, ?)",
                (row["id"], theme),
            )


def _migrate_settings_table(cursor: sqlite3.Cursor, stored_version: int) -> None:
    """Run the version-guarded one-time migrations of the ``settings`` table.

    **Version 1 — drop every pre-existing row.**
    An earlier iteration of the database-backed config seeded the ``settings``
    table on every boot — both dotted-leaf rows (``recommendations.max_count``)
    and stale whole-section JSON-blob rows (``recommendations`` -> a dict).
    Seed-on-boot
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

    **Version 6 — prune :data:`_ORPHANED_SETTING_PREFIXES`.** Unlike the rows
    above these were released, so this discards values a user really set: the
    subsystem they configured no longer exists.
    """
    if stored_version < 1:
        cursor.execute("DELETE FROM settings")

    if stored_version < 2:
        cursor.executemany(
            "DELETE FROM settings WHERE key = ?",
            [(key,) for key in _ORPHANED_SETTING_KEYS],
        )

    if stored_version < 6:
        cursor.executemany(
            # ESCAPE, so a prefix carrying _ or % matches itself literally.
            r"DELETE FROM settings WHERE key LIKE ? ESCAPE '\'",
            [(_like_prefix(prefix),) for prefix in _ORPHANED_SETTING_PREFIXES],
        )


def _renormalize_titles(cursor: sqlite3.Cursor) -> None:
    """Re-normalize all content_items titles using the full Python function.

    SQL's ``lower(title)`` backfill strips no punctuation, article or suffix.
    """
    cursor.execute("SELECT id, title FROM content_items WHERE title IS NOT NULL")
    # fetchall() required: cursor is reused for UPDATEs inside the loop
    for row in cursor.fetchall():
        normalized = normalize_title_for_matching(row["title"])
        cursor.execute(
            "UPDATE content_items SET normalized_title = ? WHERE id = ?",
            (normalized, row["id"]),
        )


def _split_crammed_series_titles(cursor: sqlite3.Cursor) -> None:
    """Move a book title's series marker into its metadata, where the plugins
    now put it: crammed, it keys as itself and stops naming its Calibre twin."""
    cursor.execute(
        "SELECT ci.id, ci.title, bd.metadata FROM content_items AS ci"
        " JOIN book_details AS bd ON bd.content_item_id = ci.id"
        " WHERE ci.content_type = 'book' AND ci.title LIKE '%(%'"
    )
    for row in cursor.fetchall():
        bare, series = split_series_from_title(row["title"])
        if not series:
            continue
        try:
            blob = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blob, dict):
            continue
        cursor.execute(
            "UPDATE content_items SET title = ? WHERE id = ?", (bare, row["id"])
        )
        cursor.execute(
            "UPDATE book_details SET metadata = ? WHERE content_item_id = ?",
            (json.dumps({**series, **blob}), row["id"]),
        )


def _clear_placeholder_authors(cursor: sqlite3.Cursor) -> None:
    """Drop a stored "Unknown": ``author`` is fill-only, so no sync replaces it."""
    cursor.execute(
        "SELECT content_item_id, author FROM book_details WHERE author IS NOT NULL"
    )
    for row in cursor.fetchall():
        if normalize_creator_for_matching(row["author"]):
            continue
        cursor.execute(
            "UPDATE book_details SET author = NULL WHERE content_item_id = ?",
            (row["content_item_id"],),
        )


def _clear_derived_columns(cursor: sqlite3.Cursor) -> None:
    cursor.execute("UPDATE content_items SET sort_title = NULL, search_text = NULL")


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


# GOG's plural spellings, mapped to the singular column each folds onto: a
# legacy blob strands them in front of ``to_text``, which refuses an object.
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


def _clear_quality_on_requeued_items(cursor: sqlite3.Cursor) -> None:
    """``not_found`` reads the label and ``pending`` the retry state, so a
    re-queued item keeping its label is counted in both."""
    cursor.execute(
        "UPDATE enrichment_status SET enrichment_quality = NULL"
        " WHERE needs_enrichment = 1 AND enrichment_quality IS NOT NULL"
    )


def _reduce_non_scalar_list_columns(cursor: sqlite3.Cursor) -> None:
    """Rewrite a list column holding an object as its names.

    A row synced before the codec refused an object still holds one, so every
    save of it raises. After the flag-dict repair, which recovers those names.
    """
    for spec in DETAIL_FIELDS.values():
        columns = [
            field.column
            for field in spec.fields
            if field.kind is FieldKind.STRING_LIST and field.column is not None
        ]
        selected = ", ".join(columns)
        cursor.execute(f"SELECT content_item_id, {selected} FROM {spec.table}")
        for row in cursor.fetchall():
            reduced = {
                column: names
                for column in columns
                if (names := _reduced_list_names(row[column])) is not None
            }
            if not reduced:
                continue
            assignments = ", ".join(f"{column} = ?" for column in reduced)
            # NULL rather than "[]": a fill-only column never fills again.
            values = [
                json.dumps(names) if names else None for names in reduced.values()
            ]
            cursor.execute(
                f"UPDATE {spec.table} SET {assignments} WHERE content_item_id = ?",
                (*values, row["content_item_id"]),
            )


def _reduced_list_names(raw: Any) -> list[str] | None:
    """The names to rewrite a stored list column as, or None to leave it:
    the codec decides which, so the pass cannot drift from what it refuses."""
    try:
        stored = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(stored, list):
        return None
    try:
        to_json_array(stored)
    except TypeError:
        return text_names(stored)
    return None


def _column_names(cursor: sqlite3.Cursor, table: str) -> list[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return [row["name"] for row in cursor.fetchall()]


def _has_column(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    return column in _column_names(cursor, table)


def _add_column_if_not_exists(
    cursor: sqlite3.Cursor, table: str, column: str, column_type: str
) -> None:
    if not _has_column(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


# User management functions


class UnknownUserError(LookupError):
    """A write named a user id no ``users`` row carries."""


def _row_to_user_dict(row: sqlite3.Row) -> UserDict:
    """Convert a ``users`` row to a user dict."""
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
    """Get a user by ID."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    return _row_to_user_dict(row) if row else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> UserDict | None:
    """Get a user by username."""
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
    settings: dict[str, Any] | None = None,
) -> int:
    """Create a new user, returning its id."""
    cursor = conn.cursor()
    settings_json = json.dumps(settings) if settings else None
    cursor.execute(
        "INSERT INTO users (username, display_name, settings) VALUES (?, ?, ?)",
        (username, display_name, settings_json),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def update_user_settings(
    conn: sqlite3.Connection, user_id: int, settings: dict[str, Any]
) -> bool:
    """Merge *settings* into the user's blob.

    Returns:
        False when the UPDATE matched no row, so a caller can tell a write
        that landed from one naming a user that does not exist.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            existing = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            existing = {}
    else:
        existing = {}

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
    """Get all users, ordered by id."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, display_name, created_at, settings FROM users ORDER BY id"
    )
    return [_row_to_user_dict(row) for row in cursor.fetchall()]


def get_default_user_id() -> int:
    """Get the default user ID."""
    return 1


def get_user_theme(conn: sqlite3.Connection, user_id: int) -> str:
    """Get the user's UI theme id, empty when they have not picked one."""
    cursor = conn.cursor()
    cursor.execute("SELECT theme FROM user_ui_settings WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return str(row[0]) if row else ""


def set_user_theme(conn: sqlite3.Connection, user_id: int, theme_id: str) -> bool:
    """Set the user's UI theme id.

    Returns:
        False when the write matched no ``users`` row, so a caller can tell a
        write that landed from one naming a user that does not exist.
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_ui_settings (user_id, theme) "
        "SELECT id, ? FROM users WHERE id = ? "
        "ON CONFLICT(user_id) DO UPDATE SET theme = excluded.theme",
        (theme_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


# Enrichment status functions


def get_enrichment_status(
    conn: sqlite3.Connection, content_item_id: int
) -> EnrichmentStatusDict | None:
    """Get enrichment status for a content item."""
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

    ``mark_enrichment_complete`` wraps it for a caller that wants the commit.
    """
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           SELECT id, CURRENT_TIMESTAMP, ?, ?, 0, NULL FROM content_items
            WHERE id = ? AND merged_into IS NULL""",
        (provider, quality, content_item_id),
    )


def mark_enrichment_complete(
    conn: sqlite3.Connection,
    content_item_id: int,
    provider: str,
    quality: str,
) -> None:
    """Mark an item as successfully enriched and commit."""
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
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           SELECT id, CURRENT_TIMESTAMP, NULL, NULL, 1, ? FROM content_items
            WHERE id = ? AND merged_into IS NULL""",
        (error, content_item_id),
    )
    conn.commit()


def mark_enrichment_settled_failure(
    conn: sqlite3.Connection,
    content_item_id: int,
    error: str,
) -> None:
    """Retire an item from the queue carrying the error that stopped it.

    Writing ``not_found`` here instead would tell an operator no provider had
    the item when one did, and would count it as a miss the run counted failed.
    """
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO enrichment_status
           (content_item_id, last_enriched_at, enrichment_provider,
            enrichment_quality, needs_enrichment, enrichment_error)
           SELECT id, CURRENT_TIMESTAMP, NULL, NULL, 0, ? FROM content_items
            WHERE id = ? AND merged_into IS NULL""",
        (error, content_item_id),
    )
    conn.commit()


def mark_item_needs_enrichment(
    conn: sqlite3.Connection,
    content_item_id: int,
) -> None:
    """Mark an item as needing enrichment."""
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR IGNORE INTO enrichment_status
           (content_item_id, needs_enrichment)
           SELECT id, 1 FROM content_items
            WHERE id = ? AND merged_into IS NULL""",
        (content_item_id,),
    )
    conn.commit()


def reset_enrichment_status(
    conn: sqlite3.Connection,
    provider: str | None = None,
    content_type: str | None = None,
    user_id: int | None = None,
    content_item_id: int | None = None,
) -> int:
    """Re-queue every tracked item a filter left as ``None`` does not exclude.

    Returns the number reset, which is the number the queue will hand out: a
    row behind a merge is neither reset nor counted.
    """
    conditions = ["ci.merged_into IS NULL"]
    params: list[str | int] = []
    if content_item_id is not None:
        conditions.append("es.content_item_id = ?")
        params.append(content_item_id)
    if provider:
        conditions.append("es.enrichment_provider = ?")
        params.append(provider)
    if content_type:
        conditions.append("ci.content_type = ?")
        params.append(content_type)
    if user_id:
        conditions.append("ci.user_id = ?")
        params.append(user_id)

    cursor = conn.cursor()
    cursor.execute(
        "UPDATE enrichment_status"
        " SET needs_enrichment = 1, enrichment_error = NULL,"
        "     enrichment_quality = NULL"
        " WHERE content_item_id IN ("
        "   SELECT es.content_item_id FROM enrichment_status es"
        "   JOIN content_items ci ON es.content_item_id = ci.id"
        f"  WHERE {' AND '.join(conditions)})",
        params,
    )
    updated = cursor.rowcount
    conn.commit()
    return updated


def _enrichment_count(
    cursor: sqlite3.Cursor, source: str, where: str, params: tuple[int, ...]
) -> int:
    """Count enrichment rows of *source* matching *where*."""
    cursor.execute(f"SELECT COUNT(*) FROM {source} WHERE {where}", params)
    result: int = cursor.fetchone()[0]
    return result


def _enrichment_group(
    cursor: sqlite3.Cursor,
    source: str,
    column: str,
    scope: str,
    params: tuple[int, ...],
) -> dict[str, int]:
    """Count enrichment rows of *source* per distinct value of *column*."""
    cursor.execute(
        f"SELECT {column}, COUNT(*) FROM {source}"
        f" WHERE {column} IS NOT NULL AND {scope}"
        f" GROUP BY {column}",
        params,
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def get_enrichment_stats(
    conn: sqlite3.Connection,
    user_id: int | None = None,
) -> dict[str, int | dict[str, int]]:
    """Count each item under exactly one enrichment state.

    ``enriched``, ``pending``, ``not_found`` and ``failed`` sum to ``total``:
    untracked counts as pending, failed only as failed. ``resettable`` is
    the tracked rows an unfiltered reset re-queues.
    """
    cursor = conn.cursor()

    # Always joined, unlike the user-scoped read this grew from: an absorbed
    # row keeps its enrichment row, and counting it leaves untracked negative.
    source = "enrichment_status es JOIN content_items ci ON es.content_item_id = ci.id"
    es = "es."
    scope = "ci.merged_into IS NULL"
    if user_id:
        scope += " AND ci.user_id = ?"
        params: tuple[int, ...] = (user_id,)
        cursor.execute(
            "SELECT COUNT(*) FROM content_items"
            " WHERE user_id = ? AND merged_into IS NULL",
            (user_id,),
        )
        total_items: int = cursor.fetchone()[0]
    else:
        params = ()
        cursor.execute("SELECT COUNT(*) FROM content_items WHERE merged_into IS NULL")
        total_items = cursor.fetchone()[0]

    tracked_items = _enrichment_count(cursor, source, scope, params)
    needs_enrichment = _enrichment_count(
        cursor,
        source,
        f"{es}needs_enrichment = 1 AND {es}enrichment_error IS NULL AND {scope}",
        params,
    )
    enriched = _enrichment_count(
        cursor,
        source,
        f"{es}needs_enrichment = 0 AND {es}enrichment_error IS NULL"
        f" AND {es}enrichment_provider != 'none' AND {scope}",
        params,
    )
    failed = _enrichment_count(
        cursor, source, f"{es}enrichment_error IS NOT NULL AND {scope}", params
    )

    untracked = total_items - tracked_items

    by_provider = _enrichment_group(
        cursor, source, f"{es}enrichment_provider", scope, params
    )
    by_quality = _enrichment_group(
        cursor, source, f"{es}enrichment_quality", scope, params
    )

    return {
        "total": total_items,
        "resettable": tracked_items,
        "enriched": enriched,
        "pending": needs_enrichment + untracked,
        "not_found": by_quality.get("not_found", 0),
        "failed": failed,
        "by_provider": by_provider,
        "by_quality": by_quality,
    }


# Preference profile functions


def get_preference_profile(
    conn: sqlite3.Connection, user_id: int
) -> PreferenceProfileRow | None:
    """Get the preference profile for a user."""
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
    """Save or update the user's single preference profile, returning its id."""
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
    """Get a single credential value (raw/encrypted)."""
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

    *credential_value* is stored verbatim; encrypting it is the caller's job.
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
    """Delete a credential row, reporting whether one was there."""
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
    """Delete every credential row for a source, returning the row count."""
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
    """Check if a credential row exists (without decrypting)."""
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
    """Get all credential key-value pairs for a source (raw/encrypted)."""
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
        sync_interval=row["sync_interval"],
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
        "SELECT source_id, plugin, config_json, enabled, sync_interval, "
        "migrated_at, updated_at "
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
    ``updated_at`` advances, and ``sync_interval`` is left alone: editing a
    source's config must not clear the schedule it is on.
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


def set_source_config_schedule(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
    sync_interval: str,
) -> bool:
    """``False`` when the source is not migrated."""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE source_configs SET sync_interval = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ? AND source_id = ?",
        (sync_interval, user_id, source_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_source_config(
    conn: sqlite3.Connection,
    user_id: int,
    source_id: str,
) -> bool:
    """Delete a migrated source config row, reporting whether one was there."""
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
        "SELECT source_id, plugin, config_json, enabled, sync_interval, "
        "migrated_at, updated_at "
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
