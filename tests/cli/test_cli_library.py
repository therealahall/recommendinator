"""Tests for CLI library commands."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.main import cli
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create CLI test runner."""
    return CliRunner()


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


def _cli_patches():
    """Context manager stack for CLI patches."""
    return (
        patch("src.cli.main.load_config"),
        patch("src.cli.main.create_storage_manager"),
        patch("src.cli.main.create_llm_components"),
        patch("src.cli.main.create_recommendation_engine"),
    )


def _invoke_with_mocks(
    cli_runner: CliRunner,
    args: list[str],
    mock_storage: MagicMock,
    config: dict | None = None,
    input_text: str | None = None,
) -> object:
    """Invoke CLI with standard mock setup."""
    p_config, p_storage, p_llm, p_engine = _cli_patches()
    with (
        p_config as mock_load,
        p_storage as mock_storage_fn,
        p_llm as mock_llm,
        p_engine,
    ):
        mock_load.return_value = config or {}
        mock_storage_fn.return_value = mock_storage
        mock_llm.return_value = (
            None,
            MagicMock(spec=EmbeddingGenerator),
            MagicMock(spec=RecommendationGenerator),
        )
        return cli_runner.invoke(cli, args, input=input_text)


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
        """Test listing items with JSON output."""
        items = [
            _make_item(db_id=1, title="Book One", rating=5),
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--format", "json"], mock_storage
        )

        assert result.exit_code == 0
        assert '"title": "Book One"' in result.output
        assert '"db_id": 1' in result.output

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
        assert "not found" in result.output.lower()

    def test_show_json_output(self, cli_runner: CliRunner) -> None:
        """Test showing item with JSON output."""
        item = _make_item(db_id=42, title="The Great Book", rating=5)
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", "42", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert '"title": "The Great Book"' in result.output
        assert '"rating": 5' in result.output


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
        assert "not found" in result.output.lower()


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
        assert "Ignored" in result.output or "ignored" in result.output
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
        assert "unignored" in result.output.lower() or "Unignored" in result.output
        mock_storage.set_item_ignored.assert_called_once_with(
            db_id=1, ignored=False, user_id=1
        )


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
        mock_csv.assert_called_once()

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
        mock_json.assert_called_once()
