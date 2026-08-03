"""Tests for CLI library commands."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands import (
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import UNSET, StorageManager
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH

from .conftest import _invoke_with_mocks


def _make_item(
    db_id: int = 1,
    title: str = "Test Book",
    author: str | None = "Test Author",
    content_type: ContentType = ContentType.BOOK,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
    rating: int | None = 4,
    review: str | None = None,
    ignored: bool | None = False,
) -> ContentItem:
    """Create a ContentItem for testing."""
    item = ContentItem(
        id=f"ext-{db_id}",
        title=title,
        author=author,
        content_type=content_type,
        status=status,
        rating=rating,
        review=review,
        ignored=ignored,
    )
    item.db_id = db_id
    return item


class TestLibraryList:
    """Tests for library list command."""

    def test_list_table_output(self, cli_runner: CliRunner) -> None:
        """Test listing items with table output."""
        items = [
            _make_item(db_id=1, title="Book One", author="Author A", rating=5),
            _make_item(db_id=2, title="Book Two", author="Author B", rating=3),
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "Book One" in result.output
        assert "Book Two" in result.output
        assert "Author A" in result.output

    def test_list_json_output(self, cli_runner: CliRunner) -> None:
        """Test listing items with JSON output matches web ContentItemResponse shape."""
        items = [
            _make_item(db_id=1, title="Book One", rating=5, review="Loved it"),
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--format", "json"], mock_storage
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        item = parsed[0]
        # Full field set matches web API ContentItemResponse
        assert set(item.keys()) == {
            "id",
            "db_id",
            "title",
            "author",
            "content_type",
            "status",
            "rating",
            "review",
            "source",
            "date_completed",
            "ignored",
            "seasons_watched",
            "total_seasons",
            "enriched",
            "genres",
            "tags",
            "description",
        }
        assert item["title"] == "Book One"
        assert item["db_id"] == 1
        assert item["rating"] == 5
        assert item["review"] == "Loved it"
        assert item["author"] == "Test Author"
        assert item["content_type"] == "book"
        assert item["status"] == "completed"
        assert item["ignored"] is False
        assert item["seasons_watched"] is None
        assert item["total_seasons"] is None

    def test_list_type_filter(self, cli_runner: CliRunner) -> None:
        """Test listing items filtered by type."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--type", "movie"], mock_storage
        )

        assert result.exit_code == 0
        mock_storage.get_content_items.assert_called_once()
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["content_type"] == ContentType.MOVIE

    def test_list_status_filter(self, cli_runner: CliRunner) -> None:
        """Test listing items filtered by status."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--status", "completed"], mock_storage
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["status"] == ConsumptionStatus.COMPLETED

    def test_list_empty_results(self, cli_runner: CliRunner) -> None:
        """Test listing when no items match."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "No items found" in result.output

    def test_list_enrichment_not_enriched(self, cli_runner: CliRunner) -> None:
        """Test --enrichment not_enriched forwards the filter to storage."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--enrichment", "not_enriched"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["enrichment"] == "not_enriched"

    def test_list_enrichment_enriched(self, cli_runner: CliRunner) -> None:
        """Test --enrichment enriched forwards the filter to storage."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--enrichment", "enriched"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["enrichment"] == "enriched"

    def test_list_enrichment_default_unset(self, cli_runner: CliRunner) -> None:
        """Test the enrichment filter is None (all items) when not provided."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["enrichment"] is None

    def test_list_enrichment_invalid_value(self, cli_runner: CliRunner) -> None:
        """Test an invalid --enrichment value is rejected by Click choices."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--enrichment", "partial"],
            mock_storage,
        )

        assert result.exit_code != 0
        mock_storage.get_content_items.assert_not_called()

    def test_list_table_shows_enriched_column(self, cli_runner: CliRunner) -> None:
        """Test the table output carries an Enriched indicator column."""
        item = _make_item(db_id=1, title="Book One")
        item.enriched = True
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = [item]

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "Enriched" in result.output

    def test_list_search_filters_results(self, cli_runner: CliRunner) -> None:
        """Test that --search forwards the query and shows matching items."""
        items = [_make_item(db_id=1, title="Dune", author="Frank Herbert")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--search", "Dune"], mock_storage
        )

        assert result.exit_code == 0
        assert "Dune" in result.output
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["search"] == "Dune"

    def test_list_search_json_output(self, cli_runner: CliRunner) -> None:
        """Test that --search works with JSON output."""
        items = [_make_item(db_id=1, title="Dune", author="Frank Herbert")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--search", "Dune", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed[0]["title"] == "Dune"
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["search"] == "Dune"

    def test_list_search_combines_with_type(self, cli_runner: CliRunner) -> None:
        """Test that --search ANDs with --type."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--search", "Dune", "--type", "book"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["search"] == "Dune"
        assert call_kwargs["content_type"] == ContentType.BOOK

    def test_list_passes_a_non_latin_search_term_through_unmangled(
        self, cli_runner: CliRunner
    ) -> None:
        """A non-Latin --search term reaches storage and renders in both formats.

        Storage is mocked, so the matching itself is not exercised here —
        ``tests/test_sorting.py`` covers that. What this pins is the CLI half:
        Click hands the term to storage byte-for-byte, and a matched title in
        a non-Latin script survives both the table and the JSON rendering.
        """
        items = [_make_item(db_id=1, title="進撃の巨人", author="諫山創")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--search", "進撃の巨人"], mock_storage
        )

        assert result.exit_code == 0
        assert "進撃の巨人" in result.output
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["search"] == "進撃の巨人"

        json_result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--search", "進撃の巨人", "--format", "json"],
            mock_storage,
        )

        assert json_result.exit_code == 0
        assert json.loads(json_result.output)[0]["title"] == "進撃の巨人"

    def test_list_rejects_an_over_long_search_term(self, cli_runner: CliRunner) -> None:
        """--search is bounded at the same length the web API accepts.

        Fuzzy matching slides a window over every candidate title with no SQL
        LIMIT to stop it, so the term's length multiplies the cost of the
        whole scan. The web rejects a longer term with a 422; the CLI has to
        agree or the two interfaces disagree about what a valid search is.
        """
        mock_storage = MagicMock(spec=StorageManager)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--search", "x" * (MAX_SEARCH_LENGTH + 1)],
            mock_storage,
        )

        assert result.exit_code != 0
        assert f"at most {MAX_SEARCH_LENGTH} characters" in result.output
        mock_storage.get_content_items.assert_not_called()

    def test_list_accepts_a_search_term_at_the_limit(
        self, cli_runner: CliRunner
    ) -> None:
        """The bound is inclusive, so a term of exactly the limit still runs."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []
        term = "x" * MAX_SEARCH_LENGTH

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--search", term], mock_storage
        )

        assert result.exit_code == 0
        assert mock_storage.get_content_items.call_args[1]["search"] == term

    def test_list_without_search_passes_none(self, cli_runner: CliRunner) -> None:
        """Test that omitting --search forwards search=None (unchanged behavior)."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["search"] is None

    def test_list_forwards_sort_limit_offset(self, cli_runner: CliRunner) -> None:
        """Test that --sort, --limit, --offset, --show-ignored reach storage."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            [
                "library",
                "list",
                "--sort",
                "rating",
                "--limit",
                "5",
                "--offset",
                "10",
                "--show-ignored",
            ],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["sort_by"] == "rating"
        assert call_kwargs["limit"] == 5
        assert call_kwargs["offset"] == 10
        assert call_kwargs["include_ignored"] is True

    def test_list_needs_rating_forces_completed_unrated(
        self, cli_runner: CliRunner
    ) -> None:
        """--needs-rating lists completed items with no rating."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--needs-rating"], mock_storage
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
        assert call_kwargs["unrated_only"] is True

    def test_list_needs_rating_overrides_explicit_status(
        self, cli_runner: CliRunner
    ) -> None:
        """--needs-rating takes precedence over an explicit --status."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--needs-rating", "--status", "unread"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
        assert call_kwargs["unrated_only"] is True

    def test_list_needs_rating_composes_with_type(self, cli_runner: CliRunner) -> None:
        """--needs-rating composes with --type, forwarding both filters."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--needs-rating", "--type", "book"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["content_type"] == ContentType.BOOK
        assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
        assert call_kwargs["unrated_only"] is True

    def test_list_without_needs_rating_does_not_force(
        self, cli_runner: CliRunner
    ) -> None:
        """Without --needs-rating, status is not forced and unrated_only is False."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["status"] is None
        assert call_kwargs["unrated_only"] is False

    def test_list_needs_rating_composes_with_limit_and_offset(
        self, cli_runner: CliRunner
    ) -> None:
        """--needs-rating forwards limit/offset alongside the forced filters."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--needs-rating", "--limit", "10", "--offset", "5"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.get_content_items.assert_called_once()
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["limit"] == 10
        assert call_kwargs["offset"] == 5
        assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
        assert call_kwargs["unrated_only"] is True


class TestLibraryShow:
    """Tests for library show command."""

    def test_show_item(self, cli_runner: CliRunner) -> None:
        """Test showing a single item."""
        item = _make_item(
            db_id=42,
            title="The Great Book",
            author="Famous Author",
            rating=5,
            review="Excellent!",
        )
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "42"], mock_storage
        )

        assert result.exit_code == 0
        assert "The Great Book" in result.output
        assert "Famous Author" in result.output
        assert "Excellent!" in result.output

    def test_show_item_not_found(self, cli_runner: CliRunner) -> None:
        """Test showing a non-existent item."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "999"], mock_storage
        )

        assert result.exit_code != 0
        assert "Error: Item 999 not found." in result.output

    def test_show_json_output(self, cli_runner: CliRunner) -> None:
        """Test showing item with JSON output matches web ContentItemResponse shape."""
        item = _make_item(
            db_id=42, title="The Great Book", rating=5, review="Masterpiece"
        )
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", "42", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        # Full field set matches web API ContentItemResponse
        assert set(parsed.keys()) == {
            "id",
            "db_id",
            "title",
            "author",
            "content_type",
            "status",
            "rating",
            "review",
            "source",
            "date_completed",
            "ignored",
            "seasons_watched",
            "total_seasons",
            "enriched",
            "genres",
            "tags",
            "description",
        }
        assert parsed["title"] == "The Great Book"
        assert parsed["rating"] == 5
        assert parsed["db_id"] == 42
        assert parsed["author"] == "Test Author"
        assert parsed["content_type"] == "book"
        assert parsed["status"] == "completed"
        assert parsed["ignored"] is False
        assert parsed["review"] == "Masterpiece"
        assert parsed["date_completed"] is None

    def test_show_json_with_date_completed(self, cli_runner: CliRunner) -> None:
        """Test that a non-None date_completed is serialized as ISO string."""
        item = _make_item(db_id=42, title="Finished Book")
        item.date_completed = date(2025, 12, 31)
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", "42", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["date_completed"] == "2025-12-31"

    def test_show_json_tv_show_with_seasons(self, cli_runner: CliRunner) -> None:
        """Test that TV show metadata populates seasons_watched and total_seasons."""
        item = _make_item(
            db_id=1, title="Breaking Bad", content_type=ContentType.TV_SHOW
        )
        item.metadata = {"seasons_watched": [1, 2, 3], "seasons": "5"}
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", "1", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["seasons_watched"] == [1, 2, 3]
        assert parsed["total_seasons"] == 5

    def test_show_json_tv_show_with_unparseable_seasons(
        self, cli_runner: CliRunner
    ) -> None:
        """Test graceful handling when seasons metadata is not an integer."""
        item = _make_item(db_id=1, title="Show", content_type=ContentType.TV_SHOW)
        item.metadata = {"seasons": "unknown"}
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", "1", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["total_seasons"] is None
        assert parsed["seasons_watched"] is None


class TestLibraryEdit:
    """Tests for library edit command."""

    def test_edit_rating(self, cli_runner: CliRunner) -> None:
        """Test editing an item's rating."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--rating", "5"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Updated" in result.output
        mock_storage.update_item_from_ui.assert_called_once()
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["rating"] == 5

    def test_edit_status(self, cli_runner: CliRunner) -> None:
        """Test editing an item's status."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--status", "completed"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Updated" in result.output
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["status"] == "completed"

    def test_edit_review(self, cli_runner: CliRunner) -> None:
        """Test editing an item's review (review-only update)."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--review", "A revelation"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["review"] == "A revelation"

    def test_edit_item_not_found(self, cli_runner: CliRunner) -> None:
        """Test editing a non-existent item."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "999", "--rating", "3"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "Error: Item 999 not found." in result.output

    def test_edit_no_fields(self, cli_runner: CliRunner) -> None:
        """Test that edit aborts when no fields are provided (before storage call)."""
        mock_storage = MagicMock(spec=StorageManager)

        result = _invoke_with_mocks(
            cli_runner, ["library", "edit", "--id", "1"], mock_storage
        )

        assert result.exit_code != 0
        assert "Provide at least one of" in result.output
        # Guard fires before any storage access.
        mock_storage.get_content_item.assert_not_called()
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_invalid_seasons_watched(self, cli_runner: CliRunner) -> None:
        """Test that non-integer seasons-watched input is rejected."""
        mock_storage = MagicMock(spec=StorageManager)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--seasons-watched", "1,two,3"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "comma-separated integers" in result.output.lower()
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_seasons_watched(self, cli_runner: CliRunner) -> None:
        """Test parsing valid seasons-watched input to a list of ints."""
        item = _make_item(db_id=1, title="Show", content_type=ContentType.TV_SHOW)
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--seasons-watched", "1, 2 ,3"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["seasons_watched"] == [1, 2, 3]

    def test_edit_genres_tags_description(self, cli_runner: CliRunner) -> None:
        """Test setting manual enrichment metadata forwards lists/text to storage."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            [
                "library",
                "edit",
                "--id",
                "1",
                "--genre",
                "Action",
                "--genre",
                "RPG",
                "--tag",
                "co-op",
                "--description",
                "A grand adventure.",
            ],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["genres"] == ["Action", "RPG"]
        assert call_kwargs["tags"] == ["co-op"]
        assert call_kwargs["description"] == "A grand adventure."

    def test_edit_genre_only_leaves_others_unchanged(
        self, cli_runner: CliRunner
    ) -> None:
        """Test editing only --genre leaves tags/description as None (unchanged)."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--genre", "Sci-Fi"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["genres"] == ["Sci-Fi"]
        assert call_kwargs["tags"] is None
        assert call_kwargs["description"] is None

    def test_edit_description_only(self, cli_runner: CliRunner) -> None:
        """Test editing only --description leaves genres/tags as None (unchanged)."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--description", "New blurb."],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["description"] == "New blurb."
        assert call_kwargs["genres"] is None
        assert call_kwargs["tags"] is None

    def test_edit_update_fails(self, cli_runner: CliRunner) -> None:
        """Test edit when storage update returns False."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = False

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--rating", "3"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "failed to update" in result.output.lower()

    def test_edit_review_at_length_limit(self, cli_runner: CliRunner) -> None:
        """A review at exactly the length limit is accepted and forwarded."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        review = "x" * MAX_REVIEW_LENGTH
        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--review", review],
            mock_storage,
        )

        assert result.exit_code == 0
        assert mock_storage.update_item_from_ui.call_args[1]["review"] == review


class TestLibraryEditRegression:
    """Regression tests for the library edit command's input validation."""

    def _tv_storage(self) -> MagicMock:
        """A storage mock returning a TV show item from get_content_item."""
        item = _make_item(db_id=1, title="Show", content_type=ContentType.TV_SHOW)
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True
        return mock_storage

    def test_edit_rejects_season_above_cap_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """A season number above the cap is rejected, matching the web bound.

        Bug reported: the web ItemEditRequest rejects seasons outside
        1..MAX_SEASONS with a 422, but the CLI stored them silently.
        Root cause: the CLI parsed --seasons-watched ints with no range check.
        Fix: the CLI now rejects out-of-range seasons before touching storage.
        """
        mock_storage = self._tv_storage()
        result = _invoke_with_mocks(
            cli_runner,
            [
                "library",
                "edit",
                "--id",
                "1",
                "--seasons-watched",
                f"1,{MAX_SEASONS + 1}",
            ],
            mock_storage,
        )
        assert result.exit_code != 0
        assert f"between 1 and {MAX_SEASONS}" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_rejects_season_below_one_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """A season number below 1 is rejected, matching the web ge=1 bound."""
        mock_storage = self._tv_storage()
        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--seasons-watched", "0"],
            mock_storage,
        )
        assert result.exit_code != 0
        assert f"between 1 and {MAX_SEASONS}" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_rejects_too_many_seasons_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """A list longer than the cap is rejected, matching web max_length."""
        mock_storage = self._tv_storage()
        too_many = ",".join(str(n) for n in range(1, MAX_SEASONS + 2))
        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--seasons-watched", too_many],
            mock_storage,
        )
        assert result.exit_code != 0
        assert f"at most {MAX_SEASONS} seasons" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_rejects_over_long_review_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """An over-long review is rejected, matching the web bound.

        Bug reported: the web ItemEditRequest rejects reviews over
        MAX_REVIEW_LENGTH with a 422, but the CLI stored them silently.
        Root cause: --review had no length check before reaching storage.
        Fix: the CLI now rejects over-long reviews before touching storage.
        """
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--review", "x" * (MAX_REVIEW_LENGTH + 1)],
            mock_storage,
        )

        assert result.exit_code != 0
        assert f"at most {MAX_REVIEW_LENGTH} characters" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_rejects_manual_metadata_over_caps_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """Over-cap manual genres/tags/description are rejected by the CLI.

        Bug reported: the web ItemEditRequest caps manual metadata (at most
        MAX_GENRES genres, MAX_TAGS tags, MAX_GENRE_TAG_LENGTH chars per
        value, MAX_DESCRIPTION_LENGTH for the description) and 422s past those
        bounds, but the CLI accepted any size and wrote it straight through.
        Root cause: --genre/--tag/--description had no length checks before
        reaching storage. Fix: the CLI now validates each bound and aborts
        before any storage write, matching the web 422.
        """
        item = _make_item(db_id=1, title="Book One")

        cases: list[list[str]] = [
            [arg for _ in range(MAX_GENRES + 1) for arg in ("--genre", "g")],
            [arg for _ in range(MAX_TAGS + 1) for arg in ("--tag", "t")],
            ["--genre", "x" * (MAX_GENRE_TAG_LENGTH + 1)],
            ["--tag", "x" * (MAX_GENRE_TAG_LENGTH + 1)],
            ["--description", "x" * (MAX_DESCRIPTION_LENGTH + 1)],
        ]

        for extra_args in cases:
            mock_storage = MagicMock(spec=StorageManager)
            mock_storage.get_content_item.return_value = item
            mock_storage.update_item_from_ui.return_value = True

            result = _invoke_with_mocks(
                cli_runner,
                ["library", "edit", "--id", "1", *extra_args],
                mock_storage,
            )

            assert result.exit_code != 0, extra_args
            mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_accepts_manual_metadata_at_caps(self, cli_runner: CliRunner) -> None:
        """Manual metadata exactly at the caps is accepted and forwarded."""
        item = _make_item(db_id=1, title="Book One")
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        extra_args = [arg for _ in range(MAX_GENRES) for arg in ("--genre", "g")]
        extra_args += [arg for _ in range(MAX_TAGS) for arg in ("--tag", "t")]
        extra_args += ["--description", "x" * MAX_DESCRIPTION_LENGTH]

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", *extra_args],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert len(call_kwargs["genres"]) == MAX_GENRES
        assert len(call_kwargs["tags"]) == MAX_TAGS


class TestLibraryEditPartialUpdate:
    """Regression tests for `library edit` erasing fields it was not given.

    Bug reported: ``library edit --id N --genre X`` (or any edit that did not
    repeat the rating) nulled the item's rating and review. The rating is the
    taste signal, so the item silently dropped out of preference analysis, and
    the value could not be recovered.
    Root cause: unset ``--rating`` / ``--review`` are None, and storage wrote
    both columns unconditionally — "not supplied" and "clear it" were the same
    value.
    Fix: the CLI forwards UNSET for a flag the user did not pass, and storage
    only writes the fields it was actually given.
    """

    def _seeded_storage(self, tmp_path: Path) -> tuple[StorageManager, int]:
        """A real temp-DB storage holding one rated, reviewed book."""
        storage = StorageManager(sqlite_path=tmp_path / "library.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                author="Frank Herbert",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                review="Loved it",
            ),
            user_id=1,
        )
        return storage, db_id

    def test_genre_only_edit_preserves_rating_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A genre-only edit leaves the stored rating and review in place."""
        storage, db_id = self._seeded_storage(tmp_path)

        edited = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--genre", "Science Fiction"],
            storage,
        )
        assert edited.exit_code == 0, edited.output

        shown = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", str(db_id), "--format", "json"],
            storage,
        )
        assert shown.exit_code == 0, shown.output
        parsed = json.loads(shown.output)
        assert parsed["rating"] == 5
        assert parsed["review"] == "Loved it"
        assert parsed["genres"] == ["Science Fiction"]

    def test_status_only_edit_preserves_rating_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A status-only edit leaves the stored rating and review in place."""
        storage, db_id = self._seeded_storage(tmp_path)

        edited = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--status", "currently_consuming"],
            storage,
        )
        assert edited.exit_code == 0, edited.output

        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert stored.rating == 5
        assert stored.review == "Loved it"

    def test_rating_edit_overwrites_the_existing_rating(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """An explicit --rating still replaces the stored value."""
        storage, db_id = self._seeded_storage(tmp_path)

        edited = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--rating", "2"],
            storage,
        )
        assert edited.exit_code == 0, edited.output

        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.rating == 2
        assert stored.review == "Loved it"

    def test_unset_flags_are_forwarded_as_unset(self, cli_runner: CliRunner) -> None:
        """The CLI sends UNSET, not None, for flags the user did not pass."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = _make_item(db_id=1)
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--status", "completed"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["rating"] is UNSET
        assert call_kwargs["review"] is UNSET


class TestLibraryEditClearing:
    """Regression tests for clearing a rating or review from the CLI.

    Bug reported: a mis-rated item could not be put back to unrated from the
    command line, so it stayed out of ``library list --needs-rating`` forever
    and kept feeding preference analysis a score the user disowned. The web
    edit dialog clears both fields by sending an explicit null.
    Root cause: making an omitted flag mean "leave it alone" — which it had to,
    so that a genre-only edit stopped erasing the rating — left the CLI with no
    way to say "clear it" at all. Before that, ``--status completed`` nulled
    the rating implicitly, so the capability existed by accident and then went
    away. An empty ``--review ""`` was worse than nothing: it stored the empty
    string, which is not the NULL the web stores and which reads to the sync
    door as a review the user wrote, so no later import could ever fill one in.
    Fix: explicit ``--clear-rating`` / ``--clear-review`` flags that send the
    same None the web sends, and an empty ``--review`` is refused rather than
    stored.
    """

    def _seeded_storage(self, tmp_path: Path) -> tuple[StorageManager, int]:
        """A real temp-DB storage holding one rated, reviewed book."""
        storage = StorageManager(sqlite_path=tmp_path / "clear.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                review="Loved it",
            ),
            user_id=1,
        )
        return storage, db_id

    def test_clear_rating_stores_null_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--clear-rating puts the item back among the unrated."""
        storage, db_id = self._seeded_storage(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--clear-rating"],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.rating is None
        assert stored.review == "Loved it"
        assert storage.get_content_items(user_id=1, unrated_only=True) != []

    def test_clear_review_stores_null_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """--clear-review stores NULL, not the empty string the web never sends."""
        storage, db_id = self._seeded_storage(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--clear-review"],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.review is None
        assert stored.rating == 5

    def test_clear_flags_forward_none_like_the_web(self, cli_runner: CliRunner) -> None:
        """The clear flags send the same None the web's explicit null sends."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = _make_item(db_id=1)
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--clear-rating", "--clear-review"],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["rating"] is None
        assert call_kwargs["review"] is None

    @pytest.mark.parametrize("review", ["", "   "])
    def test_empty_review_is_refused_regression(
        self, cli_runner: CliRunner, tmp_path: Path, review: str
    ) -> None:
        """An empty --review is refused rather than stored as an empty string.

        ``--review ""`` is the form the bug was reported against; whitespace is
        the same emptiness spelled differently, and the guard strips before
        testing so both must be refused alike.
        """
        storage, db_id = self._seeded_storage(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", review],
            storage,
        )

        assert result.exit_code != 0
        assert "--clear-review" in result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.review == "Loved it"

    def test_rating_and_clear_rating_together_are_refused(
        self, cli_runner: CliRunner
    ) -> None:
        """Setting and clearing the rating in one command is a contradiction."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = _make_item(db_id=1)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--rating", "3", "--clear-rating"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "cannot be used together" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_review_and_clear_review_together_are_refused(
        self, cli_runner: CliRunner
    ) -> None:
        """Setting and clearing the review in one command is a contradiction."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = _make_item(db_id=1)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--review", "Fine", "--clear-review"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "cannot be used together" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_a_clear_flag_alone_is_enough_of_an_edit(
        self, cli_runner: CliRunner
    ) -> None:
        """A clear flag on its own satisfies the "provide something" check."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = _make_item(db_id=1)
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner, ["library", "edit", "--id", "1", "--clear-rating"], mock_storage
        )

        assert result.exit_code == 0
        mock_storage.update_item_from_ui.assert_called_once()


class TestLibraryIgnore:
    """Tests for library ignore command."""

    def test_ignore_item(self, cli_runner: CliRunner) -> None:
        """Test ignoring an item."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.set_item_ignored.return_value = True

        result = _invoke_with_mocks(
            cli_runner, ["library", "ignore", "--id", "1"], mock_storage
        )

        assert result.exit_code == 0
        assert "Ignored item 1." in result.output
        mock_storage.set_item_ignored.assert_called_once_with(
            db_id=1, ignored=True, user_id=1
        )

    def test_ignore_item_not_found(self, cli_runner: CliRunner) -> None:
        """Test ignoring a non-existent item."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.set_item_ignored.return_value = False

        result = _invoke_with_mocks(
            cli_runner, ["library", "ignore", "--id", "999"], mock_storage
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestLibraryUnignore:
    """Tests for library unignore command."""

    def test_unignore_item(self, cli_runner: CliRunner) -> None:
        """Test unignoring an item."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.set_item_ignored.return_value = True

        result = _invoke_with_mocks(
            cli_runner, ["library", "unignore", "--id", "1"], mock_storage
        )

        assert result.exit_code == 0
        assert "Unignored item 1." in result.output
        mock_storage.set_item_ignored.assert_called_once_with(
            db_id=1, ignored=False, user_id=1
        )

    def test_unignore_item_not_found(self, cli_runner: CliRunner) -> None:
        """Test unignoring a non-existent item."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.set_item_ignored.return_value = False

        result = _invoke_with_mocks(
            cli_runner, ["library", "unignore", "--id", "999"], mock_storage
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestLibraryExport:
    """Tests for library export command."""

    def test_export_csv(self, cli_runner: CliRunner) -> None:
        """Test CSV export."""
        items = [_make_item(db_id=1, title="Book One")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        with patch("src.cli.commands.export_items_csv") as mock_csv:
            mock_csv.return_value = "title,author\nBook One,Test Author\n"
            result = _invoke_with_mocks(
                cli_runner,
                ["library", "export", "--type", "book"],
                mock_storage,
            )

        assert result.exit_code == 0
        assert "Book One" in result.output
        mock_csv.assert_called_once_with(items, ContentType.BOOK)

    def test_export_json(self, cli_runner: CliRunner) -> None:
        """Test JSON export."""
        items = [_make_item(db_id=1, title="Book One")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        with patch("src.cli.commands.export_items_json") as mock_json:
            mock_json.return_value = '[{"title": "Book One"}]'
            result = _invoke_with_mocks(
                cli_runner,
                ["library", "export", "--type", "book", "--format", "json"],
                mock_storage,
            )

        assert result.exit_code == 0
        assert "Book One" in result.output
        mock_json.assert_called_once_with(items, ContentType.BOOK)

    def test_export_to_file(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Test exporting to a file (--output)."""
        items = [_make_item(db_id=1, title="Book One")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items
        output_path = tmp_path / "books.csv"

        with patch("src.cli.commands.export_items_csv") as mock_csv:
            mock_csv.return_value = "title\nBook One\n"
            result = _invoke_with_mocks(
                cli_runner,
                [
                    "library",
                    "export",
                    "--type",
                    "book",
                    "--format",
                    "csv",
                    "--output",
                    str(output_path),
                ],
                mock_storage,
            )

        assert result.exit_code == 0
        assert output_path.read_text() == "title\nBook One\n"
        assert f"Exported 1 items to {output_path}" in result.output
        mock_storage.get_content_items.assert_called_once()
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["include_ignored"] is True
