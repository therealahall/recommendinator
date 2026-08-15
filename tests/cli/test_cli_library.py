"""Tests for CLI library commands."""

import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.models.content import (
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.storage.manager import StorageManager
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

    def test_list_empty_results(self, cli_runner: CliRunner) -> None:
        """Test listing when no items match."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "No items found" in result.output

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


class TestLibraryListCreatorColumnRegression:
    """`library list` headed the creator column "Author" for every type.

    Bug reported: a movie printed its director under a column headed
    "Author". Root cause: the header was hardcoded, which only looked right
    while non-book items read back with no author at all and the column said
    "N/A". Fix: one listing mixes the types, so the header is the name they
    share.
    """

    def test_a_mixed_listing_heads_its_creator_column_creator_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """A book's author and a movie's director sit under one honest header."""
        items = [
            _make_item(db_id=1, title="The Name of the Wind", author="Rothfuss"),
            _make_item(
                db_id=2,
                title="Arrival",
                author="Villeneuve",
                content_type=ContentType.MOVIE,
            ),
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "Creator" in result.output
        assert "Author" not in result.output
        assert "Rothfuss" in result.output
        assert "Villeneuve" in result.output


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


def _labelled_rows(output: str, label: str) -> list[str]:
    """The rendered table rows whose first cell is ``label``."""
    return [line for line in output.splitlines() if line.startswith(f"| {label} ")]


class TestLibraryShowCreatorLabelRegression:
    """`library show` labelled every type's creator "Author".

    Symptom: a movie rendered "Author | Denis Villeneuve" and a game
    "Author | Larian Studios". Root cause: the detail row was hardcoded to
    "Author", which only looked right because non-book items used to read back
    with no author at all, so the row said "N/A". Fix: take the label from the
    type's declared creator column in ``DETAIL_FIELDS``.
    """

    # The label each type's creator row carries, which is that type's
    # ``creator_column`` in title case. Parametrizing over ``ContentType``
    # rather than these keys means a type added without an entry fails here
    # instead of raising KeyError out of ``library show``.
    labels = {
        ContentType.BOOK: "Author",
        ContentType.MOVIE: "Director",
        ContentType.TV_SHOW: "Creator",
        ContentType.VIDEO_GAME: "Developer",
    }

    @pytest.mark.parametrize("content_type", list(ContentType))
    def test_creator_row_is_labelled_for_the_content_type(
        self,
        cli_runner: CliRunner,
        content_type: ContentType,
    ) -> None:
        """Each type names its creator the way a reader of that type would."""
        label = self.labels[content_type]
        item = _make_item(
            db_id=7, title="Fixture", author="Ada Lovelace", content_type=content_type
        )
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "7"], mock_storage
        )

        assert result.exit_code == 0
        creator_rows = _labelled_rows(result.output, label)
        assert len(creator_rows) == 1
        assert "Ada Lovelace" in creator_rows[0]
        for other in set(self.labels.values()) - {label}:
            assert _labelled_rows(result.output, other) == []

    def test_creator_row_keeps_its_label_with_no_creator_stored(
        self, cli_runner: CliRunner
    ) -> None:
        """An unknown director is still a director, not an author.

        The label comes from the content type, so it does not fall back to
        "Author" for the empty row that hid this bug in the first place.
        """
        item = _make_item(
            db_id=7, title="Fixture", author=None, content_type=ContentType.MOVIE
        )
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "7"], mock_storage
        )

        assert result.exit_code == 0
        assert len(_labelled_rows(result.output, "Director")) == 1
        assert "N/A" in _labelled_rows(result.output, "Director")[0]
        assert _labelled_rows(result.output, "Author") == []


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


class TestLibraryEditCompletesEverySeasonRegression:
    """`library edit --status completed` left the checklist partial (#123).

    Cause: the CLI sent the stored status when --status was absent, so storage
    could not tell a stated status from a filled-in one. Fix: absent is UNSET.
    """

    def test_completed_status_ticks_every_season_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """`--status completed` marks every season of the show watched."""
        storage = StorageManager(sqlite_path=tmp_path / "library.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="show-1",
                title="The Expanse",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.CURRENTLY_CONSUMING,
                rating=None,
                metadata={"seasons": 5, "seasons_watched": [1, 2]},
            ),
            user_id=1,
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--status", "completed"],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.status == ConsumptionStatus.COMPLETED
        assert stored.metadata["seasons_watched"] == [1, 2, 3, 4, 5]


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


class TestLibraryExport:
    """Tests for library export command."""

    def test_export_to_file(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        """Test exporting to a file (--output)."""
        items = [_make_item(db_id=1, title="Book One")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = items
        output_path = tmp_path / "books.csv"

        with patch("src.cli.commands._library.export_items_csv") as mock_csv:
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

    def test_export_guards_a_formula_title_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The CLI writes the same neutralised cell the web export does.

        Every other test here mocks the exporter out, so nothing else proves
        the CLI reaches the guarded writer.
        """
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_content_items.return_value = [
            _make_item(db_id=1, title='=HYPERLINK("http://evil","x")')
        ]
        output_path = tmp_path / "books.csv"

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "export", "--type", "book", "--output", str(output_path)],
            mock_storage,
        )

        rows = list(
            csv.DictReader(io.StringIO(output_path.read_text(encoding="utf-8")))
        )
        assert result.exit_code == 0
        assert rows[0]["title"] == '\'=HYPERLINK("http://evil","x")'
