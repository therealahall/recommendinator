"""Tests for the data-integrity guards in the duplicate-merge path.

``merge_scalar_columns`` and ``merge_detail_tables`` run inside dedup, which
permanently deletes one of the two rows. The guards covered here only fire
when something has already gone wrong — a row vanishing mid-merge, metadata
that is not a JSON object, a season count that is not a number — and each one
exists to leave the kept row's data alone rather than crash or clobber it.
Without a test, removing one would break dedup silently.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.storage.merge import merge_detail_tables, merge_scalar_columns
from src.storage.schema import create_schema


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A temporary on-disk database with the full schema, closed after the test."""
    connection = sqlite3.connect(tmp_path / "merge.db")
    create_schema(connection)
    yield connection
    connection.close()


def _insert_show(
    conn: sqlite3.Connection,
    external_id: str,
    *,
    metadata: str | None = None,
    seasons: object = None,
) -> int:
    """Insert a TV show row plus its detail row, returning the content item id."""
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO content_items
           (user_id, external_id, title, normalized_title, content_type, status)
           VALUES (1, ?, 'Regression Show', 'regression show', 'tv_show',
                   'completed')""",
        (external_id,),
    )
    item_id = cursor.lastrowid
    assert item_id is not None
    cursor.execute(
        "INSERT INTO tv_show_details (content_item_id, metadata, seasons)"
        " VALUES (?, ?, ?)",
        (item_id, metadata, seasons),
    )
    conn.commit()
    return item_id


def _detail_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    """Read a TV show detail row back."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM tv_show_details WHERE content_item_id = ?", (item_id,)
    )
    row = cursor.fetchone()
    assert row is not None
    return row


class TestVanishedRowGuard:
    """A row that disappears between the SELECT and the merge is skipped."""

    def test_missing_kept_row_leaves_duplicate_untouched(
        self, conn: sqlite3.Connection
    ) -> None:
        """A merge into a nonexistent kept row changes nothing and does not raise."""
        dup_id = _insert_show(conn, "dup")
        cursor = conn.cursor()
        cursor.execute("UPDATE content_items SET rating = 4 WHERE id = ?", (dup_id,))
        conn.commit()

        merge_scalar_columns(cursor, keep_id=999_999, delete_id=dup_id)
        conn.commit()

        cursor.execute("SELECT rating FROM content_items WHERE id = ?", (dup_id,))
        assert cursor.fetchone()["rating"] == 4

    def test_missing_duplicate_row_leaves_kept_row_untouched(
        self, conn: sqlite3.Connection
    ) -> None:
        """A merge from a nonexistent duplicate leaves the kept row as it was."""
        keep_id = _insert_show(conn, "keep")
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE content_items SET rating = 5, updated_at = '2020-01-01 00:00:00'"
            " WHERE id = ?",
            (keep_id,),
        )
        conn.commit()

        merge_scalar_columns(cursor, keep_id=keep_id, delete_id=999_999)
        conn.commit()

        cursor.execute(
            "SELECT rating, updated_at FROM content_items WHERE id = ?", (keep_id,)
        )
        row = cursor.fetchone()
        assert row["rating"] == 5
        assert row["updated_at"] == "2020-01-01 00:00:00"


class TestNonDictKeptMetadataGuard:
    """Kept metadata that is not a JSON object is preserved byte for byte."""

    @pytest.mark.parametrize("keep_metadata", ["[1, 2, 3]", '"hello"'])
    def test_non_dict_kept_metadata_is_preserved(
        self, conn: sqlite3.Connection, keep_metadata: str
    ) -> None:
        """A JSON array or scalar on the kept row blocks the metadata merge.

        Merging into it would either raise or discard it, so the merge is
        skipped and the stored text is left exactly as it was.
        """
        keep_id = _insert_show(conn, "keep", metadata=keep_metadata)
        dup_id = _insert_show(conn, "dup", metadata=json.dumps({"award": "GOTY"}))

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        assert _detail_row(conn, keep_id)["metadata"] == keep_metadata

    def test_dict_kept_metadata_still_merges(self, conn: sqlite3.Connection) -> None:
        """The guard is narrow: ordinary object metadata still merges."""
        keep_id = _insert_show(conn, "keep", metadata=json.dumps({"playtime": 40}))
        dup_id = _insert_show(conn, "dup", metadata=json.dumps({"award": "GOTY"}))

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        merged = json.loads(_detail_row(conn, keep_id)["metadata"])
        assert merged == {"playtime": 40, "award": "GOTY"}


class TestMonotonicSeasonMergeGuard:
    """The seasons/episodes merge tolerates values that are not integers."""

    def test_non_integer_duplicate_seasons_leaves_kept_count(
        self, conn: sqlite3.Connection
    ) -> None:
        """A duplicate carrying seasons='unknown' does not disturb the kept count."""
        keep_id = _insert_show(conn, "keep", seasons=3)
        dup_id = _insert_show(conn, "dup", seasons="unknown")

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        assert _detail_row(conn, keep_id)["seasons"] == 3

    def test_non_integer_kept_seasons_leaves_kept_value(
        self, conn: sqlite3.Connection
    ) -> None:
        """A kept row carrying seasons='unknown' is not overwritten either."""
        keep_id = _insert_show(conn, "keep", seasons="unknown")
        dup_id = _insert_show(conn, "dup", seasons=5)

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        assert _detail_row(conn, keep_id)["seasons"] == "unknown"

    def test_both_season_counts_absent_writes_nothing(
        self, conn: sqlite3.Connection
    ) -> None:
        """Two rows with no season count leave the column NULL.

        The odd one out in this class: with the duplicate's count NULL the
        ``dup_val is not None`` check short-circuits before any conversion, so
        this pins the path that never reaches the try/except the others cover.
        """
        keep_id = _insert_show(conn, "keep")
        dup_id = _insert_show(conn, "dup")

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        assert _detail_row(conn, keep_id)["seasons"] is None

    def test_numeric_string_duplicate_wins_when_higher(
        self, conn: sqlite3.Connection
    ) -> None:
        """A numeric string is what the guard's try block is written to allow."""
        keep_id = _insert_show(conn, "keep", seasons=3)
        dup_id = _insert_show(conn, "dup", seasons="5")

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        assert _detail_row(conn, keep_id)["seasons"] == 5

    def test_lower_duplicate_season_count_does_not_win(
        self, conn: sqlite3.Connection
    ) -> None:
        """The merge is monotonic: the higher season count survives."""
        keep_id = _insert_show(conn, "keep", seasons=5)
        dup_id = _insert_show(conn, "dup", seasons=2)

        merge_detail_tables(conn.cursor(), keep_id=keep_id, delete_id=dup_id)
        conn.commit()

        assert _detail_row(conn, keep_id)["seasons"] == 5
