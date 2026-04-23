"""Tests for CLI library commands."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager

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
        assert (
            "Provide at least one of --status, --rating, --review, --seasons-watched."
            in result.output
        )
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
