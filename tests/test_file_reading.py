"""Tests for the shared user-supplied file readers."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest

from src.ingestion.file_reading import read_csv_rows, read_import_text
from src.ingestion.plugin_base import SourceError


class TestReadImportText:
    """Tests for read_import_text."""

    def test_utf8_file_is_returned_verbatim(self, tmp_path: Path) -> None:
        """A plain UTF-8 file reads back unchanged, non-ASCII included."""
        data_file = tmp_path / "notes.md"
        data_file.write_text("# Les Misérables\n", encoding="utf-8")

        assert read_import_text("markdown_import", data_file, "Markdown") == (
            "# Les Misérables\n"
        )

    def test_bom_is_stripped(self, tmp_path: Path) -> None:
        """An Excel/editor-written UTF-8 BOM does not reach the caller."""
        data_file = tmp_path / "notes.md"
        data_file.write_bytes("﻿# Books\n".encode())

        assert read_import_text("markdown_import", data_file, "Markdown") == "# Books\n"

    @pytest.mark.parametrize("encoding", ["latin-1", "utf-16"])
    def test_non_utf8_file_raises_source_error(
        self, tmp_path: Path, encoding: str
    ) -> None:
        """A Latin-1 or UTF-16 export is a SourceError, not a UnicodeDecodeError.

        Regression: ``UnicodeDecodeError`` escaped the ingestion pipeline
        unhandled, so any non-UTF-8 export (a very ordinary thing to upload)
        was a 500 with a stack trace instead of a message naming the problem.
        """
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\nCafé Society\n", encoding=encoding)

        with pytest.raises(SourceError, match="not UTF-8 text") as excinfo:
            read_import_text("csv_import", data_file, "CSV")

        assert excinfo.value.plugin_name == "csv_import"
        # The message has to say what to do about it, not just that it failed.
        assert "UTF-8" in excinfo.value.message

    def test_missing_file_raises_source_error(self, tmp_path: Path) -> None:
        with pytest.raises(SourceError, match="JSON file not found"):
            read_import_text("json_import", tmp_path / "nope.json", "JSON")

    def test_directory_raises_source_error(self, tmp_path: Path) -> None:
        """A directory is an OSError from ``read_text``, not a decode failure."""
        directory = tmp_path / "a_directory"
        directory.mkdir()

        with pytest.raises(SourceError, match="IsADirectoryError"):
            read_import_text("csv_import", directory, "CSV")

    def test_unopenable_path_raises_source_error(self, tmp_path: Path) -> None:
        """Any other ``open`` failure is refused too, named by exception type.

        A path whose parent is a regular file gives ``ENOTDIR`` on every OS and
        every user — unlike a ``chmod(0o000)`` file, which root reads happily
        and which therefore made this a CI-environment coin flip.
        """
        not_a_directory = tmp_path / "books.csv"
        not_a_directory.write_text("title\n", encoding="utf-8")

        with pytest.raises(SourceError, match="NotADirectoryError"):
            read_import_text("csv_import", not_a_directory / "inner.csv", "CSV")


class TestReadCsvRows:
    """Tests for read_csv_rows."""

    def test_rows_are_returned(self, tmp_path: Path) -> None:
        data_file = tmp_path / "books.csv"
        data_file.write_text("title,rating\nDune,5\n", encoding="utf-8")

        assert read_csv_rows("csv_import", data_file) == [
            {"title": "Dune", "rating": "5"}
        ]

    def test_empty_file_has_no_rows(self, tmp_path: Path) -> None:
        data_file = tmp_path / "empty.csv"
        data_file.write_text("", encoding="utf-8")

        assert read_csv_rows("csv_import", data_file) == []

    def test_quoted_field_spanning_lines_is_kept_whole(self, tmp_path: Path) -> None:
        """Line endings stay untranslated so a multi-line quoted field survives."""
        data_file = tmp_path / "books.csv"
        data_file.write_text('title,review\nDune,"line one\nline two"\n')

        assert read_csv_rows("csv_import", data_file) == [
            {"title": "Dune", "review": "line one\nline two"}
        ]

    def test_missing_required_column_raises_source_error(self, tmp_path: Path) -> None:
        """A header without a required column is a bad file, not empty output."""
        data_file = tmp_path / "books.csv"
        data_file.write_text("name,rating\nDune,5\n", encoding="utf-8")

        with pytest.raises(SourceError, match="missing required column: title"):
            read_csv_rows("csv_import", data_file, required_columns={"title"})

    def test_required_columns_are_not_checked_on_an_empty_file(
        self, tmp_path: Path
    ) -> None:
        """An export with no data rows is warned about by the import, not rejected."""
        data_file = tmp_path / "empty.csv"
        data_file.write_text("", encoding="utf-8")

        assert read_csv_rows("csv_import", data_file, required_columns={"title"}) == []

    def test_unknown_column_is_logged_not_rejected(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A column the caller does not understand is ignored, with a log line."""
        data_file = tmp_path / "books.csv"
        data_file.write_text("title,mood\nDune,tense\n", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="src.ingestion.file_reading"):
            rows = read_csv_rows(
                "csv_import",
                data_file,
                required_columns={"title"},
                known_columns={"title", "rating"},
            )

        assert rows == [{"title": "Dune", "mood": "tense"}]
        assert "unknown columns that will be ignored: mood" in caplog.text

    def test_no_known_columns_means_no_unknown_column_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A caller that accepts any header (Goodreads, StoryGraph) stays quiet."""
        data_file = tmp_path / "books.csv"
        data_file.write_text("Title,Author\nDune,Frank Herbert\n", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="src.ingestion.file_reading"):
            read_csv_rows("goodreads_csv", data_file)

        assert "unknown columns" not in caplog.text

    def test_csv_error_raises_source_error(self, tmp_path: Path) -> None:
        """A csv module fault is reported as a SourceError naming the plugin."""
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\n" + "x" * 200 + "\n", encoding="utf-8")

        original_limit = csv.field_size_limit()
        csv.field_size_limit(100)
        try:
            with pytest.raises(SourceError, match="Failed to parse CSV") as excinfo:
                read_csv_rows("csv_import", data_file)
        finally:
            csv.field_size_limit(original_limit)

        assert excinfo.value.plugin_name == "csv_import"

    def test_non_utf8_csv_raises_source_error(self, tmp_path: Path) -> None:
        data_file = tmp_path / "books.csv"
        data_file.write_text("title\nCafé\n", encoding="utf-16")

        with pytest.raises(SourceError, match="not UTF-8 text"):
            read_csv_rows("csv_import", data_file)

    def test_unknown_column_carrying_crlf_cannot_forge_a_log_line(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A header cell containing CRLF is escaped before it is logged.

        Reported: ``POST /api/import`` accepts an arbitrary file, so the header
        row is attacker-chosen. Root cause: the warning interpolated the raw
        header cells, and CSV permits a quoted field containing CRLF, so one
        cell could close this record and append a forged one under the app's
        own ``... | LEVEL | logger | message`` format (CWE-117). Fixed by
        escaping every named column through ``sanitize_for_log``.
        """
        data_file = tmp_path / "books.csv"
        # newline="": the CRLF has to reach the file untranslated for the
        # reader to hand it back inside the header cell.
        data_file.write_text(
            'title,"mood\r\n2099-01-01 | ERROR | src.web.api | forged"\nDune,tense\n',
            encoding="utf-8",
            newline="",
        )

        with caplog.at_level(logging.WARNING, logger="src.ingestion.file_reading"):
            read_csv_rows(
                "csv_import",
                data_file,
                required_columns={"title"},
                known_columns={"title"},
            )

        message = caplog.records[0].getMessage()
        assert "\r" not in message
        assert "\n" not in message
        # The cell is still shown, just defused. ``read_import_text`` decodes
        # with universal newlines, so the CRLF arrives here as a bare LF.
        assert "mood\\n2099-01-01 | ERROR | src.web.api | forged" in message

    def test_unknown_columns_are_bounded_in_count_and_length(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A wide header names only the first few columns and counts the rest.

        A CSV may declare thousands of columns of any length, so an unbounded
        echo lets a single upload bury every other record in the log.
        """
        columns = [f"col{index}_{'x' * 500}" for index in range(50)]
        data_file = tmp_path / "books.csv"
        data_file.write_text(
            "title," + ",".join(columns) + "\nDune" + ",v" * 50 + "\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="src.ingestion.file_reading"):
            read_csv_rows(
                "csv_import",
                data_file,
                required_columns={"title"},
                known_columns={"title"},
            )

        message = caplog.records[0].getMessage()
        assert "(50 in total)" in message
        # Five columns named, each cut off at the per-value cap.
        assert message.count("...(truncated)") == 5

    def test_bom_does_not_stick_to_the_first_column_name(self, tmp_path: Path) -> None:
        """The header a caller checks against is the BOM-free one.

        The required-column check now happens inside this function, so a BOM
        glued to the first column name would reject every Excel-saved export
        for a column that is plainly there.
        """
        data_file = tmp_path / "books.csv"
        data_file.write_text("title,rating\nDune,5\n", encoding="utf-8-sig")

        assert read_csv_rows("csv_import", data_file, required_columns={"title"}) == [
            {"title": "Dune", "rating": "5"}
        ]
