import csv
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands import library
from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_RELEASE_YEAR,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    MIN_RELEASE_YEAR,
    ConsumptionStatus,
    ContentItem,
    ContentType,
    ExternalId,
)
from src.storage.duplicates import GROUP_MEMBER_MAX, MAX_DECLINE_OTHERS
from src.storage.manager import (
    SUGGESTION_PAGE_DEFAULT,
    StorageManager,
    UncorrectableFieldError,
)
from src.utils.duplicate_serialization import (
    ALSO_OFFERED_NOTE,
    declined_pair_to_dict,
    merge_to_dict,
    skipped_works_note,
    suggestion_page_to_dict,
)
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.web.api._library import ContentItemResponse, IgnoreItemResponse
from tests.factories import make_storage_mock

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
    item = ContentItem(
        id=f"ext-{db_id}",
        external_ids=[ExternalId(source="goodreads_csv", external_id=f"ext-{db_id}")],
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
    def test_list_table_states_each_rows_title_series_and_creator(
        self, cli_runner: CliRunner
    ) -> None:
        items = [
            _make_item(db_id=1, title="Book One", author="Author A", rating=5),
            _make_item(db_id=2, title="Book Two", author="Author B", rating=3),
        ]
        items[0].metadata = {"series": "The Expanse", "series_index": 2.0}
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "Book One" in result.output
        assert "Book Two" in result.output
        assert "Author A" in result.output
        assert "The Expanse #2" in result.output

    def test_list_json_output(self, cli_runner: CliRunner) -> None:
        items = [
            _make_item(db_id=1, title="Book One", rating=5, review="Loved it"),
        ]
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--format", "json"], mock_storage
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        item = parsed[0]
        assert set(item.keys()) == {
            "external_ids",
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
            "release_year",
            "series",
            "series_index",
            "enriched",
            "manually_enriched",
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
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "No items found" in result.output

    def test_list_search_filters_results(self, cli_runner: CliRunner) -> None:
        items = [_make_item(db_id=1, title="Dune", author="Frank Herbert")]
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--search", "Dune"], mock_storage
        )

        assert result.exit_code == 0
        assert "Dune" in result.output
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["search"] == "Dune"

    def test_list_rejects_an_over_long_search_term(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "list", "--search", "x" * (MAX_SEARCH_LENGTH + 1)],
            mock_storage,
        )

        assert result.exit_code != 0
        assert f"at most {MAX_SEARCH_LENGTH} characters" in result.output
        mock_storage.get_content_items.assert_not_called()

    def test_list_forwards_sort_limit_offset(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
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
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = []

        result = _invoke_with_mocks(
            cli_runner, ["library", "list", "--needs-rating"], mock_storage
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.get_content_items.call_args[1]
        assert call_kwargs["status"] == ConsumptionStatus.COMPLETED
        assert call_kwargs["unrated_only"] is True


class TestLibraryListCreatorColumnRegression:
    def test_a_mixed_listing_heads_its_creator_column_creator_regression(
        self, cli_runner: CliRunner
    ) -> None:
        items = [
            _make_item(db_id=1, title="The Name of the Wind", author="Rothfuss"),
            _make_item(
                db_id=2,
                title="Arrival",
                author="Villeneuve",
                content_type=ContentType.MOVIE,
            ),
        ]
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = items

        result = _invoke_with_mocks(cli_runner, ["library", "list"], mock_storage)

        assert result.exit_code == 0
        assert "Creator" in result.output
        assert "Author" not in result.output
        assert "Rothfuss" in result.output
        assert "Villeneuve" in result.output


class TestLibraryShow:
    def test_show_item(self, cli_runner: CliRunner) -> None:
        item = _make_item(
            db_id=42,
            title="The Great Book",
            author="Famous Author",
            rating=5,
            review="Excellent!",
        )
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "42"], mock_storage
        )

        assert result.exit_code == 0
        assert "The Great Book" in result.output
        assert "Famous Author" in result.output
        assert "Excellent!" in result.output

    def test_show_names_the_manual_state_enrichment_reset_undoes(
        self, cli_runner: CliRunner
    ) -> None:
        item = _make_item(db_id=42)
        item.enriched = True
        item.manually_enriched = True
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "42"], mock_storage
        )

        assert result.exit_code == 0
        assert "Yes (manual)" in result.output

    def test_show_item_not_found(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "999"], mock_storage
        )

        assert result.exit_code != 0
        assert "Error: Item 999 not found." in result.output

    def test_show_json_output(self, cli_runner: CliRunner) -> None:
        item = _make_item(
            db_id=42, title="The Great Book", rating=5, review="Masterpiece"
        )
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", "42", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert set(parsed.keys()) == {
            "external_ids",
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
            "release_year",
            "series",
            "series_index",
            "enriched",
            "manually_enriched",
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
        assert parsed["external_ids"] == [
            {"source": "goodreads_csv", "external_id": "ext-42"}
        ]

    def test_show_table_names_the_source_behind_each_id(
        self, cli_runner: CliRunner
    ) -> None:
        item = _make_item(db_id=42)
        item.external_ids.append(ExternalId(source="steam", external_id="440"))
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "42"], mock_storage
        )

        assert result.exit_code == 0
        assert "goodreads_csv: ext-42" in result.output
        assert "steam: 440" in result.output

    def test_show_table_states_the_year_a_correction_would_replace(
        self, cli_runner: CliRunner
    ) -> None:
        item = _make_item(db_id=42, content_type=ContentType.VIDEO_GAME)
        item.metadata = {"release_year": 2016}
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "42"], mock_storage
        )

        assert result.exit_code == 0
        rows = _labelled_rows(result.output, "Release Year")
        assert len(rows) == 1
        assert "2016" in rows[0]

    def test_show_json_tv_show_with_seasons(self, cli_runner: CliRunner) -> None:
        item = _make_item(
            db_id=1, title="Breaking Bad", content_type=ContentType.TV_SHOW
        )
        item.metadata = {"seasons_watched": [1, 2, 3], "seasons": "5"}
        mock_storage = make_storage_mock()
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

    def test_show_table_states_the_series_the_title_no_longer_carries(
        self, cli_runner: CliRunner
    ) -> None:
        item = _make_item(db_id=42, title="All Systems Red")
        item.metadata = {"series": "The Murderbot Diaries", "series_index": 1.0}
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "42"], mock_storage
        )

        assert result.exit_code == 0
        rows = _labelled_rows(result.output, "Series")
        assert len(rows) == 1
        assert "The Murderbot Diaries #1" in rows[0]


def _labelled_rows(output: str, label: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(f"| {label} ")]


class TestLibraryShowCreatorLabelRegression:
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
        label = self.labels[content_type]
        item = _make_item(
            db_id=7, title="Fixture", author="Ada Lovelace", content_type=content_type
        )
        mock_storage = make_storage_mock()
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
        item = _make_item(
            db_id=7, title="Fixture", author=None, content_type=ContentType.MOVIE
        )
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item

        result = _invoke_with_mocks(
            cli_runner, ["library", "show", "--id", "7"], mock_storage
        )

        assert result.exit_code == 0
        assert len(_labelled_rows(result.output, "Director")) == 1
        assert "N/A" in _labelled_rows(result.output, "Director")[0]
        assert _labelled_rows(result.output, "Author") == []


class TestLibraryEdit:
    def test_edit_rating(self, cli_runner: CliRunner) -> None:
        item = _make_item(db_id=1, title="Book One")
        mock_storage = make_storage_mock()
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
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "999", "--rating", "3"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "Error: Item 999 not found." in result.output

    def test_edit_no_fields(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()

        result = _invoke_with_mocks(
            cli_runner, ["library", "edit", "--id", "1"], mock_storage
        )

        assert result.exit_code != 0
        assert "Provide at least one of" in result.output
        mock_storage.get_content_item.assert_not_called()
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_invalid_seasons_watched(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--seasons-watched", "1,two,3"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "comma-separated integers" in result.output.lower()
        mock_storage.update_item_from_ui.assert_not_called()

    @pytest.mark.parametrize(
        ("flags", "sent"),
        [
            (["--seasons-watched", "1, 2 ,3"], [1, 2, 3]),
            (["--clear-seasons"], []),
            (["--status", "unread"], None),
        ],
        ids=["parsed", "cleared", "unmentioned"],
    )
    def test_edit_sends_the_seasons_its_flags_name(
        self, cli_runner: CliRunner, flags: list[str], sent: list[int] | None
    ) -> None:
        item = _make_item(db_id=1, title="Show", content_type=ContentType.TV_SHOW)
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True

        result = _invoke_with_mocks(
            cli_runner, ["library", "edit", "--id", "1", *flags], mock_storage
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["seasons_watched"] == sent

    def test_edit_genres_tags_description(self, cli_runner: CliRunner) -> None:
        item = _make_item(db_id=1, title="Book One")
        mock_storage = make_storage_mock()
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

    def test_edit_json_carries_the_web_item_response_keys(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "edit-json.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )

        result = _invoke_with_mocks(
            cli_runner,
            [
                "library",
                "edit",
                "--id",
                str(db_id),
                "--rating",
                "5",
                "--format",
                "json",
            ],
            storage,
        )

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert set(parsed) == set(ContentItemResponse.model_fields)
        assert parsed["rating"] == 5


class TestLibraryEditRegression:
    def _tv_storage(self) -> MagicMock:
        item = _make_item(db_id=1, title="Show", content_type=ContentType.TV_SHOW)
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = item
        mock_storage.update_item_from_ui.return_value = True
        return mock_storage

    def test_edit_rejects_season_above_cap_regression(
        self, cli_runner: CliRunner
    ) -> None:
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
        item = _make_item(db_id=1, title="Book One")
        mock_storage = make_storage_mock()
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
        item = _make_item(db_id=1, title="Book One")

        cases: list[list[str]] = [
            [arg for _ in range(MAX_GENRES + 1) for arg in ("--genre", "g")],
            [arg for _ in range(MAX_TAGS + 1) for arg in ("--tag", "t")],
            ["--genre", "x" * (MAX_GENRE_TAG_LENGTH + 1)],
            ["--tag", "x" * (MAX_GENRE_TAG_LENGTH + 1)],
            ["--description", "x" * (MAX_DESCRIPTION_LENGTH + 1)],
        ]

        for extra_args in cases:
            mock_storage = make_storage_mock()
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
    def _seeded_storage(self, tmp_path: Path) -> tuple[StorageManager, int]:
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
    """`library edit --status completed` left the checklist partial (#123)."""

    def test_completed_status_ticks_every_season_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
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
    def _seeded_storage(self, tmp_path: Path) -> tuple[StorageManager, int]:
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

    @pytest.mark.parametrize("emptied", ["", "   "])
    def test_an_empty_description_is_its_clear_as_the_web_box_is(
        self, cli_runner: CliRunner, tmp_path: Path, emptied: str
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "clear.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="movie-1",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                metadata={"description": "A linguist."},
            ),
            user_id=1,
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--description", emptied],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert not stored.metadata.get("description")

    @pytest.mark.parametrize(
        "flag,cleared,kept",
        [("--clear-genres", "genres", "tags"), ("--clear-tags", "tags", "genres")],
    )
    def test_clear_genres_and_tags_empty_only_the_list_named(
        self, cli_runner: CliRunner, tmp_path: Path, flag: str, cleared: str, kept: str
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "clear.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="movie-1",
                title="Arrival",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.COMPLETED,
                metadata={"genres": ["Sci-Fi"], "tags": ["cerebral"]},
            ),
            user_id=1,
        )
        seeded = storage.get_content_item(db_id, user_id=1)
        assert seeded is not None
        assert seeded.metadata[cleared] and seeded.metadata[kept]

        result = _invoke_with_mocks(
            cli_runner, ["library", "edit", "--id", str(db_id), flag], storage
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.metadata.get(cleared, []) == []
        assert stored.metadata[kept] == seeded.metadata[kept]

    @pytest.mark.parametrize(
        "args",
        [
            ["--genre", "Sci-Fi", "--clear-genres"],
            ["--tag", "classic", "--clear-tags"],
            ["--seasons-watched", "1", "--clear-seasons"],
            ["--rating", "3", "--clear-rating"],
        ],
    )
    def test_setting_and_clearing_one_field_together_is_refused(
        self, cli_runner: CliRunner, args: list[str]
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = _make_item(db_id=1)

        result = _invoke_with_mocks(
            cli_runner, ["library", "edit", "--id", "1", *args], mock_storage
        )

        assert result.exit_code != 0
        assert "cannot be used together" in result.output
        mock_storage.update_item_from_ui.assert_not_called()


class TestLibraryIgnore:
    def test_ignore_item(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
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
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = None

        result = _invoke_with_mocks(
            cli_runner, ["library", "ignore", "--id", "999"], mock_storage
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @pytest.mark.parametrize(
        ("command", "ignored"), [("ignore", True), ("unignore", False)]
    )
    def test_ignoring_json_carries_the_web_ignore_response_keys(
        self, cli_runner: CliRunner, tmp_path: Path, command: str, ignored: bool
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "ignore-json.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["library", command, "--id", str(db_id), "--format", "json"],
            storage,
        )

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert set(parsed) == set(IgnoreItemResponse.model_fields)
        assert parsed["db_id"] == db_id
        assert parsed["title"] == "Dune"
        assert parsed["ignored"] is ignored

    def test_unignore_reaches_an_item_that_is_already_ignored(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "unignore.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )
        assert storage.set_item_ignored(db_id=db_id, ignored=True, user_id=1)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "unignore", "--id", str(db_id), "--format", "json"],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["ignored"] is False
        restored = storage.get_content_item(db_id, user_id=1)
        assert restored is not None
        assert restored.ignored is False


class TestLibraryExport:
    def test_export_to_file(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        items = [_make_item(db_id=1, title="Book One")]
        mock_storage = make_storage_mock()
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

    def test_export_without_a_type_covers_every_content_type(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = [
            _make_item(db_id=1, title="Book One"),
            _make_item(db_id=2, title="Movie One", content_type=ContentType.MOVIE),
        ]
        output_path = tmp_path / "library.csv"

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "export", "--output", str(output_path)],
            mock_storage,
        )

        rows = list(
            csv.DictReader(io.StringIO(output_path.read_text(encoding="utf-8")))
        )
        assert result.exit_code == 0
        assert mock_storage.get_content_items.call_args[1]["content_type"] is None
        assert [row["title"] for row in rows] == ["Book One", "Movie One"]
        assert rows[0]["author"] == "Test Author"
        assert rows[1]["director"] == "Test Author"

    def test_export_guards_a_formula_title_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        mock_storage = make_storage_mock()
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

    def test_an_output_path_that_cannot_be_written_is_named_not_raised(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = [_make_item(db_id=1)]
        destination = tmp_path / "absent" / "library.csv"

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "export", "--output", str(destination)],
            mock_storage,
        )

        assert result.exit_code != 0
        assert str(destination) in result.output
        assert isinstance(result.exception, SystemExit)

    @pytest.mark.parametrize("refusal", ["n\n", "\n"])
    def test_an_existing_output_file_is_replaced_only_when_the_operator_said_so(
        self, cli_runner: CliRunner, tmp_path: Path, refusal: str
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.get_content_items.return_value = [_make_item(db_id=1)]
        destination = tmp_path / "library.csv"
        destination.write_text("title\nmy own work\n", encoding="utf-8")

        def export(*args: str, input_text: str | None = None) -> Any:
            return _invoke_with_mocks(
                cli_runner,
                ["library", "export", "--output", str(destination), *args],
                mock_storage,
                input_text=input_text,
            )

        declined = export(input_text=refusal)

        assert destination.read_text(encoding="utf-8") == "title\nmy own work\n"

        confirmed = export(input_text="y\n")

        assert "Test Book" in destination.read_text(encoding="utf-8")

        destination.write_text("title\nmy own work\n", encoding="utf-8")
        forced = export("--yes")

        assert declined.exit_code == 0
        assert confirmed.exit_code == 0
        assert forced.exit_code == 0, forced.output
        assert "Test Book" in destination.read_text(encoding="utf-8")


def _duplicate_library(tmp_path: Path) -> tuple[StorageManager, list[int]]:
    storage = StorageManager(sqlite_path=tmp_path / "duplicates.db")
    rows = [
        ("goodreads_csv", "1", "The Gate of the Feral Gods", None),
        ("goodreads_csv", "2", "Gate of the Feral Gods", None),
        ("calibre", "3", "Deadhouse Gates", None),
        ("goodreads_csv", "4", "Deadhouse Gates (Malazan Book 2)", "Steven Erikson"),
    ]
    db_ids = [
        _save_book(storage, source, external_id, title, author)
        for source, external_id, title, author in rows
    ]
    assert len(set(db_ids)) == len(rows)
    return storage, db_ids


def _save_book(
    storage: StorageManager,
    source: str,
    external_id: str,
    title: str,
    author: str | None = None,
) -> int:
    return storage.save_content_item(
        ContentItem(
            id=external_id,
            source=source,
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        user_id=1,
    )


def _row_containing(output: str, needle: str) -> str:
    (row,) = [line for line in output.splitlines() if needle in line]
    return row


def _merge(
    cli_runner: CliRunner,
    storage: StorageManager,
    survivor: int,
    absorbed: int,
    user: str = "1",
    fmt: str = "table",
) -> Any:
    return _invoke_with_mocks(
        cli_runner,
        ["library", "merge", "--survivor", str(survivor), "--absorbed"]
        + [str(absorbed), "--user", user, "--format", fmt],
        storage,
    )


def _decline(
    cli_runner: CliRunner,
    storage: StorageManager,
    one: int,
    other: int,
    user: str = "1",
) -> Any:
    return _invoke_with_mocks(
        cli_runner,
        ["library", "decline-duplicate", "--one", str(one), "--other"]
        + [str(other), "--user", user],
        storage,
    )


def _json(cli_runner: CliRunner, storage: StorageManager, args: list[str]) -> Any:
    result = _invoke_with_mocks(cli_runner, [*args, "--format", "json"], storage)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


class TestLibraryDuplicates:
    def test_each_block_shows_every_copy_and_the_looser_key_reads_as_looser(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, _ = _duplicate_library(tmp_path)

        result = _invoke_with_mocks(cli_runner, ["library", "duplicates"], storage)

        assert result.exit_code == 0, result.output
        exact = _row_containing(result.output, "Feral Gods")
        looser = _row_containing(result.output, "Malazan Book 2")
        assert "same title" in exact
        assert "apart from a qualifier" not in exact
        assert "same title apart from a qualifier" in looser
        assert "Deadhouse Gates (N/A, calibre)" in looser
        assert "Deadhouse Gates (Malazan Book 2) (Steven Erikson, goodreads_csv)" in (
            looser
        )

    def test_a_declined_pair_is_listed_and_undeclining_it_offers_it_again(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        pair = ["--one", str(db_ids[0]), "--other", str(db_ids[1])]
        declined = _invoke_with_mocks(
            cli_runner, ["library", "decline-duplicate", *pair], storage
        )

        listed = _invoke_with_mocks(
            cli_runner, ["library", "declined-duplicates"], storage
        )
        lifted = _invoke_with_mocks(
            cli_runner, ["library", "undecline-duplicate", *pair], storage
        )
        offered = _invoke_with_mocks(cli_runner, ["library", "duplicates"], storage)
        again = _invoke_with_mocks(
            cli_runner, ["library", "undecline-duplicate", *pair], storage
        )

        assert "will not be offered as duplicates again" in declined.output
        row = _row_containing(listed.output, "Feral Gods")
        assert str(db_ids[0]) in row
        assert str(db_ids[1]) in row
        assert lifted.exit_code == 0, lifted.output
        assert "may be offered as duplicates again" in lifted.output
        assert "Feral Gods" in offered.output
        assert again.exit_code != 0
        assert f"Items {db_ids[0]} and {db_ids[1]} are not a declined pair" in (
            again.output
        )

    def test_three_copies_of_one_work_are_offered_as_one_block(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "witcher.db")
        titles = [
            "The Time of Contempt (The Witcher, #2)",
            "Time of Contempt",
            "The Time of Contempt",
        ]
        db_ids = [
            _save_book(storage, "goodreads_csv", str(number), title)
            for number, title in enumerate(titles)
        ]

        offered = _json(cli_runner, storage, ["library", "duplicates"])
        table = _invoke_with_mocks(cli_runner, ["library", "duplicates"], storage)

        assert offered["total"] == 1
        (block,) = offered["suggestions"]
        assert [copy["db_id"] for copy in block["copies"]] == db_ids
        assert block["survivor_id"] == db_ids[0]
        assert all(title in table.output for title in titles)
        assert "Showing 1 of 1 suspected duplicates." in table.output

    def test_declining_one_copy_leaves_the_others_offered_together(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        third = _save_book(
            storage, "storygraph_csv", "5", "Deadhouse Gates (Malazan, Book Two)"
        )

        declined = _invoke_with_mocks(
            cli_runner,
            ["library", "decline-duplicate", "--one", str(third)]
            + ["--other", str(db_ids[2]), "--other", str(db_ids[3])],
            storage,
        )
        offered = _json(cli_runner, storage, ["library", "duplicates"])
        listed = _json(cli_runner, storage, ["library", "declined-duplicates"])

        assert declined.exit_code == 0, declined.output
        assert [
            [copy["db_id"] for copy in block["copies"]]
            for block in offered["suggestions"]
        ] == [[db_ids[0], db_ids[1]], [db_ids[2], db_ids[3]]]
        assert [(pair["one_id"], pair["other_id"]) for pair in listed] == [
            (db_ids[2], third),
            (db_ids[3], third),
        ]

    def test_one_copy_named_twice_is_refused_once_and_a_dead_id_refuses_none(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        third = _save_book(
            storage, "storygraph_csv", "5", "Deadhouse Gates (Malazan, Book Two)"
        )
        twice = ["--other", str(db_ids[2]), "--other", str(db_ids[2])]

        repeated = _invoke_with_mocks(
            cli_runner,
            ["library", "decline-duplicate", "--one", str(third), *twice],
            storage,
        )
        dead = _invoke_with_mocks(
            cli_runner,
            ["library", "decline-duplicate", "--one", str(third)]
            + ["--other", str(db_ids[3]), "--other", "9999"],
            storage,
        )
        assert repeated.exit_code == 0, repeated.output
        assert repeated.output.count("will not be offered as duplicates again") == 1
        assert len(_json(cli_runner, storage, ["library", "declined-duplicates"])) == 1
        assert dead.exit_code != 0
        assert (
            f"Item {third} and items {db_ids[3]}, 9999 are not live pairs to decline."
            in dead.output
        )

    def test_a_decline_naming_more_copies_than_a_block_holds_is_refused(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)

        def decline(count: int) -> Any:
            others: list[str] = []
            for other_id in range(9000, 9000 + count):
                others += ["--other", str(other_id)]
            return _invoke_with_mocks(
                cli_runner,
                ["library", "decline-duplicate", "--one", str(db_ids[0]), *others],
                storage,
            )

        taken = decline(MAX_DECLINE_OTHERS)
        over = decline(MAX_DECLINE_OTHERS + 1)

        assert taken.exit_code != 0
        assert "are not live pairs to decline." in taken.output
        assert over.exit_code != 0
        assert f"--other accepts at most {MAX_DECLINE_OTHERS} values." in over.output

    def test_a_copy_two_blocks_both_offer_says_so_in_each_of_them(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        third = _save_book(
            storage, "storygraph_csv", "5", "Deadhouse Gates (Malazan, Book Two)"
        )
        fourth = _save_book(storage, "openlibrary", "6", "Deadhouse Gates (Book Two)")
        declined = _decline(cli_runner, storage, third, db_ids[2])

        offered = _json(cli_runner, storage, ["library", "duplicates"])
        cut = _json(cli_runner, storage, ["library", "duplicates", "--limit", "2"])
        table = _invoke_with_mocks(cli_runner, ["library", "duplicates"], storage)

        assert declined.exit_code == 0, declined.output
        _, cut_short = cut["suggestions"]
        notes = [copy["also_offered"] for copy in cut_short["copies"]]
        assert notes.count(ALSO_OFFERED_NOTE) == 2
        assert {
            copy["db_id"]
            for block in offered["suggestions"]
            for copy in block["copies"]
            if copy["also_offered"] == ALSO_OFFERED_NOTE
        } == {db_ids[3], fourth}
        assert table.output.count(ALSO_OFFERED_NOTE) == 4

    def test_a_type_filter_and_a_limit_cut_the_offer_without_hiding_the_count(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, _ = _duplicate_library(tmp_path)

        books = _json(cli_runner, storage, ["library", "duplicates", "--type", "book"])
        games = _json(
            cli_runner, storage, ["library", "duplicates", "--type", "video_game"]
        )
        capped = _json(cli_runner, storage, ["library", "duplicates", "--limit", "1"])
        table = _invoke_with_mocks(
            cli_runner, ["library", "duplicates", "--limit", "1"], storage
        )

        assert books["total"] == 2
        assert len(books["suggestions"]) == 2
        assert games == {"total": 0, "skipped_note": "", "suggestions": []}
        assert capped["total"] == 2
        assert len(capped["suggestions"]) == 1
        assert "Showing 1 of 2 suspected duplicates." in table.output

    def test_a_work_left_unsearched_is_reported_rather_than_counted_as_none(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "shelf.db")
        for index in range(GROUP_MEMBER_MAX + 1):
            _save_book(storage, "calibre", str(index), f"The Wandering Inn ({index})")

        table = _invoke_with_mocks(cli_runner, ["library", "duplicates"], storage)
        offered = _json(cli_runner, storage, ["library", "duplicates"])

        assert table.exit_code == 0, table.output
        assert "No suspected duplicates." not in table.output
        assert offered["skipped_note"] == skipped_works_note(1)
        assert table.output.strip() == offered["skipped_note"]

        _save_book(storage, "calibre", "pair-1", "Deadhouse Gates")
        _save_book(storage, "goodreads_csv", "pair-2", "Deadhouse Gates (Malazan)")
        beside = _invoke_with_mocks(cli_runner, ["library", "duplicates"], storage)

        assert (
            f"Showing 1 of 1 suspected duplicates. {skipped_works_note(1)}"
            in beside.output
        )


class TestLibraryMerge:
    def test_a_merge_is_listed_and_unmerge_puts_the_absorbed_row_back(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        survivor, absorbed = db_ids[0], db_ids[1]

        merged = _merge(cli_runner, storage, survivor, absorbed)
        listed = _invoke_with_mocks(cli_runner, ["library", "merges"], storage)
        after_merge = _json(cli_runner, storage, ["library", "list"])

        assert merged.exit_code == 0, merged.output
        assert f"Gate of the Feral Gods (#{absorbed}) into" in merged.output
        assert f"The Gate of the Feral Gods (#{survivor})" in merged.output
        merge_row = _row_containing(listed.output, "Feral Gods")
        assert f"(#{survivor})" in merge_row
        assert "your choice" in merge_row
        assert sorted(item["db_id"] for item in after_merge) == sorted(
            db_ids[2:] + [survivor]
        )

        merge_id = _json(cli_runner, storage, ["library", "merges"])[0]["id"]
        unmerged = _invoke_with_mocks(
            cli_runner, ["library", "unmerge", "--merge-id", str(merge_id)], storage
        )
        after_unmerge = _json(cli_runner, storage, ["library", "list"])

        assert unmerged.exit_code == 0, unmerged.output
        assert f"(#{absorbed}) from" in unmerged.output
        assert sorted(item["db_id"] for item in after_unmerge) == sorted(db_ids)

    def test_an_undo_out_of_order_says_which_merge_to_deal_with_first(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        for absorbed in db_ids[1:3]:
            merged = _merge(cli_runner, storage, db_ids[0], absorbed)
            assert merged.exit_code == 0, merged.output
        records = _json(cli_runner, storage, ["library", "merges"])
        newest, oldest = records[0]["id"], records[1]["id"]

        result = _invoke_with_mocks(
            cli_runner, ["library", "unmerge", "--merge-id", str(oldest)], storage
        )

        assert result.exit_code != 0
        assert f"Merge {oldest} cannot be undone before merge {newest}" in result.output


class TestDuplicateJsonIsTheSharedSerializer:
    def test_every_suggestion_and_refusal_verb_emits_what_it_makes(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        pair = ["--one", str(db_ids[0]), "--other", str(db_ids[1])]
        page = storage.list_duplicate_suggestions(
            user_id=1, limit=SUGGESTION_PAGE_DEFAULT
        )

        offered = _json(cli_runner, storage, ["library", "duplicates"])
        declined = _json(cli_runner, storage, ["library", "decline-duplicate", *pair])
        (stored,) = storage.list_declined_duplicates(user_id=1)
        listed = _json(cli_runner, storage, ["library", "declined-duplicates"])
        lifted = _json(cli_runner, storage, ["library", "undecline-duplicate", *pair])

        assert offered == suggestion_page_to_dict(page)
        assert declined == [declined_pair_to_dict(stored)]
        assert listed == [declined_pair_to_dict(stored)]
        assert lifted == declined_pair_to_dict(stored)

    def test_every_merge_verb_emits_what_it_makes(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)

        merged = json.loads(
            _merge(cli_runner, storage, db_ids[0], db_ids[1], fmt="json").output
        )
        (stored,) = storage.list_content_item_merges(user_id=1)
        listed = _json(cli_runner, storage, ["library", "merges"])
        undone = _json(
            cli_runner, storage, ["library", "unmerge", "--merge-id", str(stored.id)]
        )

        assert merged == merge_to_dict(stored)
        assert listed == [merge_to_dict(stored)]
        assert undone == merge_to_dict(stored)


class TestDuplicatesWrongIds:
    def test_a_merge_refuses_each_wrong_id_in_the_storage_layer_s_own_words(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        survivor, absorbed = db_ids[0], db_ids[1]
        first = _merge(cli_runner, storage, survivor, absorbed)
        assert first.exit_code == 0, first.output
        before = _json(cli_runner, storage, ["library", "merges"])

        refusals = {
            (survivor, survivor, "1"): f"Item {survivor} cannot absorb itself.",
            (survivor, 9999, "1"): "No item with id 9999.",
            (survivor, absorbed, "1"): (
                f"Item {absorbed} is already merged into {survivor}."
            ),
            (absorbed, survivor, "1"): (
                f"Item {absorbed} is already merged into {survivor}."
            ),
            (db_ids[2], db_ids[3], "2"): f"No item with id {db_ids[2]}.",
        }
        for (keep, absorb, user), message in refusals.items():
            result = _merge(cli_runner, storage, keep, absorb, user=user)
            assert result.exit_code != 0, result.output
            assert f"Error: {message}" in result.output

        assert _json(cli_runner, storage, ["library", "merges"]) == before

    def test_declining_takes_only_two_live_rows_of_this_user(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        survivor, absorbed = db_ids[0], db_ids[1]
        assert _merge(cli_runner, storage, survivor, absorbed).exit_code == 0

        for one, other, user in (
            (survivor, survivor, "1"),
            (survivor, absorbed, "1"),
            (db_ids[2], db_ids[3], "2"),
        ):
            result = _decline(cli_runner, storage, one, other, user=user)
            assert result.exit_code != 0, result.output
            assert (
                f"Item {one} and item {other} are not a live pair to decline."
                in result.output
            )

        assert _json(cli_runner, storage, ["library", "declined-duplicates"]) == []

    def test_an_undo_addressed_to_another_user_finds_nothing_to_undo(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        merge_id = json.loads(
            _merge(cli_runner, storage, db_ids[0], db_ids[1], fmt="json").output
        )["id"]
        _decline(cli_runner, storage, db_ids[2], db_ids[3])

        unmerged = _invoke_with_mocks(
            cli_runner,
            ["library", "unmerge", "--merge-id", str(merge_id), "--user", "2"],
            storage,
        )
        undeclined = _invoke_with_mocks(
            cli_runner,
            ["library", "undecline-duplicate", "--one", str(db_ids[2]), "--other"]
            + [str(db_ids[3]), "--user", "2"],
            storage,
        )

        assert unmerged.exit_code != 0
        assert f"Merge {merge_id} not found." in unmerged.output
        assert undeclined.exit_code != 0
        assert "are not a declined pair" in undeclined.output
        assert len(_json(cli_runner, storage, ["library", "merges"])) == 1
        assert len(_json(cli_runner, storage, ["library", "declined-duplicates"])) == 1


class TestDuplicatesOperatorPath:
    def test_merging_a_pair_the_list_offered_keeps_the_survivor_in_the_library(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        survivor, absorbed = db_ids[0], db_ids[1]
        ignored = _invoke_with_mocks(
            cli_runner, ["library", "ignore", "--id", str(absorbed)], storage
        )
        assert ignored.exit_code == 0, ignored.output

        offered = _json(cli_runner, storage, ["library", "duplicates"])["suggestions"]
        assert [[copy["db_id"] for copy in block["copies"]] for block in offered] == [
            [survivor, absorbed],
            [db_ids[2], db_ids[3]],
        ]
        (block,) = [item for item in offered if item["copies"][1]["db_id"] == absorbed]
        merged = _merge(
            cli_runner, storage, block["survivor_id"], block["copies"][1]["db_id"]
        )
        assert merged.exit_code == 0, merged.output

        listed = _json(cli_runner, storage, ["library", "list"])
        kept = [item for item in listed if item["db_id"] == survivor]
        assert [item["ignored"] for item in kept] == [False]

    def test_lifting_a_refusal_a_merge_hides_says_which_merge_to_undo_first(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)
        one, other = db_ids[0], db_ids[1]
        assert _decline(cli_runner, storage, one, other).exit_code == 0
        merge_id = json.loads(
            _merge(cli_runner, storage, other, one, fmt="json").output
        )["id"]

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "undecline-duplicate", "--one", str(one)]
            + ["--other", str(other)],
            storage,
        )

        assert result.exit_code != 0
        assert f"before merge {merge_id}" in result.output
        assert len(_json(cli_runner, storage, ["library", "declined-duplicates"])) == 1

    def test_a_library_of_distinct_titles_offers_only_its_real_duplicates(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "big.db")
        titles = [f"Chapter {number} of the Long Road" for number in range(300)]
        twins = [
            f"{title} (The Long Road, Book {index})"
            for index, title in enumerate(titles[:5])
        ]
        for number, title in enumerate([*titles, *twins]):
            storage.save_content_item(
                ContentItem(
                    id=str(number),
                    source="goodreads_csv" if number < len(titles) else "calibre",
                    title=title,
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
                user_id=1,
            )

        offered = _json(cli_runner, storage, ["library", "duplicates"])

        assert offered["total"] == 5
        assert {block["copies"][0]["title"] for block in offered["suggestions"]} == set(
            titles[:5]
        )

    def test_no_library_verb_deletes_an_item(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_ids = _duplicate_library(tmp_path)

        gone = _invoke_with_mocks(
            cli_runner, ["library", "delete", "--id", str(db_ids[0])], storage
        )

        assert gone.exit_code != 0
        assert "delete" not in library.commands
        assert len(_json(cli_runner, storage, ["library", "list"])) == len(db_ids)


class TestLibraryEditCorrections:
    @staticmethod
    def _game_storage() -> MagicMock:
        mock_storage = make_storage_mock()
        mock_storage.get_content_item.return_value = _make_item(
            db_id=1, title="Doom", content_type=ContentType.VIDEO_GAME
        )
        mock_storage.update_item_from_ui.return_value = True
        return mock_storage

    def test_edit_forwards_the_corrected_year_and_creator(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = self._game_storage()

        result = _invoke_with_mocks(
            cli_runner,
            [
                "library",
                "edit",
                "--id",
                "1",
                "--release-year",
                "1993",
                "--creator",
                "id Software",
            ],
            mock_storage,
        )

        assert result.exit_code == 0
        call_kwargs = mock_storage.update_item_from_ui.call_args[1]
        assert call_kwargs["release_year"] == 1993
        assert call_kwargs["creator"] == "id Software"

    def test_edit_refuses_a_blank_creator(self, cli_runner: CliRunner) -> None:
        mock_storage = self._game_storage()

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--creator", "   "],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "--creator cannot be empty" in result.output
        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_refuses_a_correction_outside_the_web_bounds(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = self._game_storage()

        for extra_args in (
            ["--release-year", str(MIN_RELEASE_YEAR - 1)],
            ["--release-year", str(MAX_RELEASE_YEAR + 1)],
            ["--creator", "x" * (MAX_CREATOR_LENGTH + 1)],
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["library", "edit", "--id", "1", *extra_args],
                mock_storage,
            )
            assert result.exit_code != 0, extra_args

        mock_storage.update_item_from_ui.assert_not_called()

    def test_edit_reports_a_type_that_states_no_release_year(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = self._game_storage()
        mock_storage.update_item_from_ui.side_effect = UncorrectableFieldError(
            "A book has no release year to correct."
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", "1", "--release-year", "1965"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "no release year to correct" in result.output
