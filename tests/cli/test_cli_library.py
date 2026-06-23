"""Tests for CLI library commands."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.cli.commands import (
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.series import MAX_SEASONS

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
