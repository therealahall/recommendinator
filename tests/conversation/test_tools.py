"""Tests for conversation tool system."""

import tempfile
from collections.abc import Generator
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.conversation.tools import (
    CONVERSATION_TOOLS,
    ToolExecutor,
    get_tool_descriptions,
    parse_tool_call_from_text,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager

# The same list every other review-writing surface is checked against,
# imported rather than repeated so chat cannot come to refuse a different set
# from the CLI and the web without a test saying so.
from tests.test_interface_parity import BLANK_REVIEWS


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def tool_executor(storage_manager: StorageManager) -> ToolExecutor:
    """Create a tool executor for testing."""
    return ToolExecutor(storage_manager)


@pytest.fixture
def sample_items(storage_manager: StorageManager) -> list[int]:
    """Create sample content items and return their db_ids."""
    items = [
        ContentItem(
            id="book1",
            title="The Martian",
            author="Andy Weir",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="game1",
            title="Outer Wilds",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="game2",
            title="Red Dead Redemption 2",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
    ]
    db_ids = []
    for item in items:
        db_id = storage_manager.save_content_item(item, user_id=1)
        db_ids.append(db_id)
    return db_ids


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_conversation_tools_defined(self) -> None:
        """Test that conversation tools are properly defined."""
        assert len(CONVERSATION_TOOLS) > 0
        required_tools = [
            "mark_completed",
            "update_rating",
            "add_to_wishlist",
            "clarify_item",
            "save_memory",
            "search_items",
        ]
        tool_names = [t["name"] for t in CONVERSATION_TOOLS]
        for tool in required_tools:
            assert tool in tool_names

    def test_tools_have_required_fields(self) -> None:
        """Test that each tool has required fields."""
        for tool in CONVERSATION_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool

    def test_get_tool_descriptions(self) -> None:
        """Test getting formatted tool descriptions."""
        descriptions = get_tool_descriptions()
        assert "mark_completed" in descriptions
        assert "update_rating" in descriptions
        assert isinstance(descriptions, str)


class TestMarkCompleted:
    """Tests for mark_completed tool."""

    def test_mark_item_completed_with_rating(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test marking an item as completed with a rating."""
        item_id = sample_items[0]  # The Martian

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": item_id, "rating": 5},
            user_id=1,
        )

        assert result.success
        assert "The Martian" in result.message
        assert "completed" in result.message

        # Verify in database
        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.status == ConsumptionStatus.COMPLETED
        assert item.rating == 5

    def test_mark_item_completed_with_review(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test marking an item as completed with a review."""
        item_id = sample_items[1]  # Outer Wilds

        result = tool_executor.execute(
            "mark_completed",
            {
                "item_id": item_id,
                "rating": 5,
                "review": "Amazing exploration game!",
            },
            user_id=1,
        )

        assert result.success

        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.review == "Amazing exploration game!"

    def test_mark_item_completed_with_date(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test marking an item as completed with a specific date."""
        item_id = sample_items[0]

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": item_id, "date_completed": "2024-01-15"},
            user_id=1,
        )

        assert result.success

        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.date_completed == date(2024, 1, 15)

    def test_mark_item_completed_nonexistent(self, tool_executor: ToolExecutor) -> None:
        """Test marking a non-existent item."""
        result = tool_executor.execute(
            "mark_completed",
            {"item_id": 99999},
            user_id=1,
        )

        assert not result.success
        assert "not found" in result.message.lower()

    def test_mark_item_invalid_rating(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test marking an item with invalid rating."""
        result = tool_executor.execute(
            "mark_completed",
            {"item_id": sample_items[0], "rating": 10},
            user_id=1,
        )

        assert not result.success
        assert "1-5" in result.message

    def test_mark_item_missing_id(self, tool_executor: ToolExecutor) -> None:
        """Test marking without item_id."""
        result = tool_executor.execute("mark_completed", {}, user_id=1)

        assert not result.success
        assert "required" in result.message.lower()


class TestMarkCompletedDate:
    """Regression tests for the date a chat completion records.

    Bug reported: telling the assistant "I finished Dune" overwrote the
    completion date the item was imported with. Chat always sent a date —
    today's, when the user had not named one — and the user door writes an
    explicit date unconditionally, so an item imported as finished on
    2020-01-01 was re-dated to today. That date orders the variety ladder, and
    losing it is exactly the silent loss of user-owned state this door exists
    to prevent. The stamp was also ``date.today()`` where the other three
    surfaces used UTC, so the four disagreed about what day it was.
    Root cause: the handler decided the date, rather than saying nothing and
    letting the door decide.
    Fix: no date from the user means UNSET — the door fills an empty date with
    today in the host's zone and keeps a stored one. A date the user does name
    is still written as given.
    """

    def test_completion_without_a_date_keeps_the_stored_one_regression(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """ "I finished this" does not re-date an item imported with a date."""
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book-dated",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=date(2020, 1, 1),
            ),
            user_id=1,
        )

        result = tool_executor.execute(
            "mark_completed", {"item_id": db_id, "rating": 4}, user_id=1
        )

        assert result.success
        stored = storage_manager.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.date_completed == date(2020, 1, 1)
        assert stored.rating == 4

    def test_completion_without_a_date_stamps_the_host_day_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
        host_timezone,
    ) -> None:
        """An undated item is dated by the day the user is living."""
        host_timezone("America/Los_Angeles")

        with patch(
            "src.utils.dates.utc_now",
            return_value=datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        ):
            result = tool_executor.execute(
                "mark_completed", {"item_id": sample_items[0]}, user_id=1
            )

        assert result.success
        stored = storage_manager.get_content_item(sample_items[0], user_id=1)
        assert stored is not None
        assert stored.date_completed == date(2026, 3, 14)

    def test_an_explicit_date_still_replaces_the_stored_one(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """A date the user names is an instruction, so it is written as given."""
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book-dated",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=date(2020, 1, 1),
            ),
            user_id=1,
        )

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": db_id, "date_completed": "2024-01-15"},
            user_id=1,
        )

        assert result.success
        stored = storage_manager.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.date_completed == date(2024, 1, 15)

    def test_the_result_reports_the_stored_date(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """The tool result quotes the row as stored, not what it asked for."""
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book-dated",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=date(2020, 1, 1),
            ),
            user_id=1,
        )

        result = tool_executor.execute("mark_completed", {"item_id": db_id}, user_id=1)

        assert result.data is not None
        assert result.data["date_completed"] == "2020-01-01"


class TestChatCompletionUsesTheCompletionDoor:
    """Regression tests for chat completions diverging from the other surfaces.

    Bug reported: telling the assistant "I finished Dune" left an item that was
    already marked completed but carried no date still undated, while
    ``complete --title Dune`` and ``POST /api/complete`` both stamped today.
    One user intention, three surfaces, two answers — and the item stayed
    undated for the variety ladder, which orders on that date.
    Root cause: chat persisted through ``update_item_from_ui``, the edit door,
    whose date rule fires only on a genuine transition into completed. That
    rule is right for an edit: it stops an unrelated genre change from dating a
    years-old import as finished today. But "I finished this" is a completion,
    not an edit, and the other two surfaces route it through
    ``complete_content_item``, which fills an empty date whether or not the
    status moved.
    Fix: mark_completed goes through ``complete_content_item`` too, so the door
    owns find-or-create, the user-owned fields and the date rule, and all three
    surfaces answer alike. update_rating stays on the edit door — re-rating is
    not completing.
    """

    def test_an_already_completed_undated_item_gets_the_host_day_regression(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
        host_timezone,
    ) -> None:
        """An undated completion is dated even though the status does not move."""
        host_timezone("America/Los_Angeles")
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book-undated",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )
        seeded = storage_manager.get_content_item(db_id, user_id=1)
        assert seeded is not None
        assert seeded.date_completed is None

        with patch(
            "src.utils.dates.utc_now",
            return_value=datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        ):
            result = tool_executor.execute(
                "mark_completed", {"item_id": db_id}, user_id=1
            )

        assert result.success
        stored = storage_manager.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.date_completed == date(2026, 3, 14)
        assert result.data is not None
        assert result.data["date_completed"] == "2026-03-14"

    def test_chat_and_the_complete_surfaces_agree_on_an_undated_item_regression(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
        host_timezone,
    ) -> None:
        """Chat dates an undated completion the same day ``complete`` does."""
        host_timezone("America/Los_Angeles")
        completed_undated = {
            "content_type": ContentType.BOOK,
            "status": ConsumptionStatus.COMPLETED,
        }
        via_chat = storage_manager.save_content_item(
            ContentItem(id="book-chat", title="Dune", **completed_undated),
            user_id=1,
        )
        via_command = storage_manager.save_content_item(
            ContentItem(id="book-command", title="Neuromancer", **completed_undated),
            user_id=1,
        )

        with patch(
            "src.utils.dates.utc_now",
            return_value=datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        ):
            chat_result = tool_executor.execute(
                "mark_completed", {"item_id": via_chat}, user_id=1
            )
            # The entry point behind both `complete` and POST /api/complete.
            storage_manager.complete_content_item(
                ContentItem(
                    id="book-command", title="Neuromancer", **completed_undated
                ),
                user_id=1,
            )

        assert chat_result.success
        from_chat = storage_manager.get_content_item(via_chat, user_id=1)
        from_command = storage_manager.get_content_item(via_command, user_id=1)
        assert from_chat is not None
        assert from_command is not None
        assert from_chat.date_completed == from_command.date_completed
        assert from_chat.date_completed == date(2026, 3, 14)

    def test_an_unknown_item_id_creates_nothing(
        self, tool_executor: ToolExecutor, storage_manager: StorageManager
    ) -> None:
        """The door's find-or-create half never fires for a chat completion.

        Passed before this change too — the handler has always rejected an
        item_id it cannot resolve. Kept because routing through a
        find-or-create door is precisely what could start inserting rows for a
        title the user never added.
        """
        result = tool_executor.execute("mark_completed", {"item_id": 99999}, user_id=1)

        assert not result.success
        assert "not found" in result.message.lower()
        assert storage_manager.count_items(user_id=1) == 0

    def test_an_explicit_date_earlier_than_the_stored_one_is_written_regression(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """A date the user names replaces a later one, not just an earlier one.

        Bug reported: correcting a completion date downward in chat — "I
        finished Dune last Tuesday", against a row an import had dated later —
        was accepted and never written.
        Root cause: the date rode in on the ContentItem, so the door's
        find-or-create half applied its later-date-wins sync rule to it before
        the completion write ever saw it.
        Fix: the door writes the date the caller supplied as given. Chat is the
        only surface that can name one, so this is the only surface the loss
        showed on.
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book-late",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=date(2026, 12, 1),
            ),
            user_id=1,
        )

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": db_id, "date_completed": "2026-07-28"},
            user_id=1,
        )

        assert result.success
        stored = storage_manager.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.date_completed == date(2026, 7, 28)
        assert result.data is not None
        assert result.data["date_completed"] == "2026-07-28"

    def test_update_rating_does_not_date_an_undated_completion(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Re-rating is an edit, so it keeps the edit door's transition rule.

        Passed before this change too. It is the guard that the two acts stay
        on different doors: moving update_rating to the completion door would
        date every re-rating as a fresh completion.
        """
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="book-undated",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            ),
            user_id=1,
        )

        result = tool_executor.execute(
            "update_rating", {"item_id": db_id, "rating": 2}, user_id=1
        )

        assert result.success
        stored = storage_manager.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.rating == 2
        assert stored.date_completed is None


class TestUpdateRating:
    """Tests for update_rating tool."""

    def test_update_rating_success(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test updating the rating of an item."""
        item_id = sample_items[2]  # RDR2, rated 5

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 4},
            user_id=1,
        )

        assert result.success
        assert "4/5" in result.message
        assert result.data is not None
        assert result.data["old_rating"] == 5
        assert result.data["new_rating"] == 4

    def test_update_rating_with_review(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Test updating rating with a new review."""
        item_id = sample_items[2]

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 5, "review": "Updated review"},
            user_id=1,
        )

        assert result.success

        item = storage_manager.get_content_item(item_id, user_id=1)
        assert item is not None
        assert item.review == "Updated review"

    def test_update_rating_missing_rating(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test updating without rating value."""
        result = tool_executor.execute(
            "update_rating",
            {"item_id": sample_items[0]},
            user_id=1,
        )

        assert not result.success
        assert "required" in result.message.lower()


class TestRatingUpdatePersists:
    """Regression tests for chat rating updates that never reached the database.

    Bug reported: chat re-rating ("rate Dune 2 stars") replied "Updated! Dune
    is now rated 2/5" while the stored rating stayed at 5, so preference
    analysis kept scoring on the value the user had just corrected.
    Root cause: both handlers persisted through ``save_content_item``, the
    sync door, whose fill-only rule discards a rating for any item that
    already has one — exactly the case update_rating exists for — and then
    returned success unconditionally.
    Fix: an explicit chat edit goes through ``update_item_from_ui``, and both
    handlers report the row as actually stored rather than assuming the write
    landed.
    """

    def test_update_rating_overwrites_existing_rating_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Re-rating an already-rated item changes the stored rating."""
        item_id = sample_items[2]  # RDR2, completed, rated 5

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 2},
            user_id=1,
        )

        assert result.success is True
        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.rating == 2

    def test_update_rating_leaves_status_and_review_alone(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """A rating-only chat edit does not disturb the status or the review."""
        item_id = sample_items[2]
        storage_manager.update_item_from_ui(
            db_id=item_id, status="completed", review="Still thinking about it"
        )

        tool_executor.execute(
            "update_rating", {"item_id": item_id, "rating": 3}, user_id=1
        )

        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.rating == 3
        assert stored.review == "Still thinking about it"
        assert stored.status == ConsumptionStatus.COMPLETED

    def test_mark_completed_with_rating_overwrites_existing_rating_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """Finishing an item with a rating replaces the rating it already had."""
        item_id = sample_items[2]  # RDR2, completed, rated 5

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": item_id, "rating": 4},
            user_id=1,
        )

        assert result.success is True
        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.rating == 4

    def test_result_carries_the_new_rating_and_the_one_it_replaced(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """The result reports both ratings, and the message quotes the new one.

        The stronger property — that the number reported is the one read back
        rather than the one asked for — is not pinned here: both are 1, so a
        handler echoing the request would pass every assertion below. Its
        date-side twin, ``test_the_result_reports_the_stored_date``, does pin
        it, because there the requested and stored values genuinely differ.
        """
        item_id = sample_items[2]

        result = tool_executor.execute(
            "update_rating", {"item_id": item_id, "rating": 1}, user_id=1
        )

        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.rating == 1
        assert result.data is not None
        assert result.data["new_rating"] == stored.rating
        assert result.data["old_rating"] == 5
        assert "1/5" in result.message

    def test_failed_write_reports_failure(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """A write that does not land is reported as a failure, not a success.

        The stubbed update stands in for the row being deleted by a
        concurrent sync between the read and the write; the handler must not
        claim the rating changed.
        """
        item_id = sample_items[2]

        with patch.object(storage_manager, "update_item_from_ui", return_value=False):
            result = tool_executor.execute(
                "update_rating", {"item_id": item_id, "rating": 2}, user_id=1
            )

        assert result.success is False
        assert "Could not update" in result.message
        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.rating == 5

    def test_sync_path_still_fill_only(
        self, storage_manager: StorageManager, sample_items: list[int]
    ) -> None:
        """Guard: the sync door is unchanged — it still cannot overwrite a rating."""
        item = storage_manager.get_content_item(sample_items[2], user_id=1)
        assert item is not None
        item.rating = 1

        storage_manager.save_content_item(item, user_id=1)

        stored = storage_manager.get_content_item(sample_items[2], user_id=1)
        assert stored is not None
        assert stored.rating == 5


class TestChatNeverStoresABlankReview:
    """Regression tests for a chat review that is blank but not empty.

    Bug reported: both handlers filtered the parameter with
    ``params.get("review") or None``, which drops ``""`` but hands ``"   "``
    straight to a door that writes it. ``update_rating`` then replaced a review
    the user had written with whitespace and reported success, and
    ``mark_completed`` wrote the whitespace into an empty column inside
    ``_upsert_content_item`` — before ``_write_completion``'s own blank guard
    could see it — where it reads as a review the user wrote and permanently
    stops a later import from filling the field.
    Root cause: chat is the fourth review-writing surface and the only one with
    no request schema to refuse anything. Its parameters are LLM output rather
    than a validated form, so a blank string, or a value that is not a string
    at all, arrives unchecked; the other three surfaces got blank guards while
    this one was rewired onto the overwriting door without one.
    Fix: chat normalises the parameter once, in ``_supplied_review``, so a
    value it does not count as a written review is never handed to a door.
    """

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS)
    def test_update_rating_leaves_a_stored_review_intact_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
        blank_review: str,
    ) -> None:
        """A blank review does not overwrite the one the user wrote."""
        item_id = sample_items[2]
        storage_manager.update_item_from_ui(
            db_id=item_id, status="completed", review="Loved it"
        )

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 3, "review": blank_review},
            user_id=1,
        )

        assert result.success is True
        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.review == "Loved it"
        assert stored.rating == 3

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS)
    def test_mark_completed_leaves_an_empty_review_null_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
        blank_review: str,
    ) -> None:
        """A blank review leaves the column NULL rather than storing spaces."""
        item_id = sample_items[0]  # The Martian, unread, no review

        result = tool_executor.execute(
            "mark_completed",
            {"item_id": item_id, "review": blank_review},
            user_id=1,
        )

        assert result.success is True
        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.review is None
        assert stored.status == ConsumptionStatus.COMPLETED

    @pytest.mark.parametrize("not_a_review", [*BLANK_REVIEWS, 5])
    def test_mark_completed_hands_the_door_no_review_at_all_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
        not_a_review: str | int,
    ) -> None:
        """Nothing chat refuses as a review reaches the completion door.

        The stored-column assertions above hold with or without this guard,
        because the completion door refuses a blank on its own — they pass
        against a handler that hands ``params["review"]`` straight over. This
        one pins the guard chat owns: the door is stubbed out, so the only
        thing under test is what chat handed it.

        The number is a case here because ``mark_completed`` fails differently
        from ``update_rating``: a non-string does not reach the door at all, it
        reaches ``ContentItem`` construction, where Pydantic refuses the whole
        completion rather than just the review.
        """
        item_id = sample_items[0]

        with patch.object(
            storage_manager, "complete_content_item", return_value=item_id
        ) as completion_door:
            result = tool_executor.execute(
                "mark_completed",
                {"item_id": item_id, "review": not_a_review},
                user_id=1,
            )

        assert result.success is True
        completion_door.assert_called_once()
        assert completion_door.call_args.args[0].review is None

    def test_a_blanked_completion_can_still_be_filled_by_an_import_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """The harm the NULL prevents: the fill-only door can still fill it.

        The sync door fills ``review`` only while the stored value is empty, so
        a stored ``"   "`` is indistinguishable from a review the user wrote
        and blocks the field for good.
        """
        item_id = sample_items[0]
        tool_executor.execute(
            "mark_completed", {"item_id": item_id, "review": "   "}, user_id=1
        )

        storage_manager.save_content_item(
            ContentItem(
                id="book1",
                title="The Martian",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                review="Imported from Goodreads",
            ),
            user_id=1,
        )

        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.review == "Imported from Goodreads"

    def test_a_non_string_review_is_not_stored_regression(
        self,
        tool_executor: ToolExecutor,
        sample_items: list[int],
        storage_manager: StorageManager,
    ) -> None:
        """An LLM-supplied number is not a review and never reaches the column.

        Handed ``5``, ``update_item_from_ui``'s own blank check calls
        ``.strip()`` on it and raises ``AttributeError``, which ``execute``
        turns into a failed tool call — so without chat's guard the user gets
        an error and no rating change, rather than a coerced review.

        Dropping the review is all the guard does, though: a non-string review
        that made chat refuse the whole call would be a regression of its own,
        because the user did ask for a rating change. That is what the success
        and rating assertions below hold.
        """
        item_id = sample_items[2]
        storage_manager.update_item_from_ui(
            db_id=item_id, status="completed", review="Loved it"
        )

        result = tool_executor.execute(
            "update_rating",
            {"item_id": item_id, "rating": 3, "review": 5},
            user_id=1,
        )

        assert result.success is True
        stored = storage_manager.get_content_item(item_id, user_id=1)
        assert stored is not None
        assert stored.review == "Loved it"
        assert stored.rating == 3


class TestAddToWishlist:
    """Tests for add_to_wishlist tool."""

    def test_add_to_wishlist_book(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Test adding a book to wishlist."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {
                "title": "Project Hail Mary",
                "content_type": "book",
                "author": "Andy Weir",
            },
            user_id=1,
        )

        assert result.success
        assert "Project Hail Mary" in result.message
        assert result.data is not None
        assert "item_id" in result.data

        # Verify in database
        items = storage_manager.get_unconsumed_items(
            user_id=1, content_type=ContentType.BOOK
        )
        assert any(item.title == "Project Hail Mary" for item in items)

    def test_add_to_wishlist_game(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Test adding a game to wishlist."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {"title": "Disco Elysium", "content_type": "video_game"},
            user_id=1,
        )

        assert result.success
        assert "video game" in result.message.lower()

    def test_add_to_wishlist_invalid_type(self, tool_executor: ToolExecutor) -> None:
        """Test adding with invalid content type."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {"title": "Something", "content_type": "podcast"},
            user_id=1,
        )

        assert not result.success
        assert "invalid" in result.message.lower()

    def test_add_to_wishlist_missing_title(self, tool_executor: ToolExecutor) -> None:
        """Test adding without title."""
        result = tool_executor.execute(
            "add_to_wishlist",
            {"content_type": "book"},
            user_id=1,
        )

        assert not result.success


class TestClarifyItem:
    """Tests for clarify_item tool."""

    def test_clarify_item_success(self, tool_executor: ToolExecutor) -> None:
        """Test clarification request."""
        matches = [
            {
                "id": 1,
                "title": "Dune",
                "author": "Frank Herbert",
                "content_type": "book",
            },
            {"id": 2, "title": "Dune", "content_type": "movie"},
        ]

        result = tool_executor.execute(
            "clarify_item",
            {"query": "dune", "matches": matches},
            user_id=1,
        )

        assert result.success
        assert result.needs_clarification
        assert result.clarification_options == matches

    def test_clarify_item_empty_matches(self, tool_executor: ToolExecutor) -> None:
        """Test clarification with no matches."""
        result = tool_executor.execute(
            "clarify_item",
            {"query": "dune", "matches": []},
            user_id=1,
        )

        assert not result.success


class TestSaveMemory:
    """Tests for save_memory tool."""

    def test_save_memory_success(
        self,
        tool_executor: ToolExecutor,
        storage_manager: StorageManager,
    ) -> None:
        """Test saving a user preference."""
        result = tool_executor.execute(
            "save_memory",
            {"memory_text": "I prefer shorter games during weekdays"},
            user_id=1,
        )

        assert result.success
        assert "Noted" in result.message
        assert result.data is not None
        assert "memory_id" in result.data

        # Verify in database
        memories = storage_manager.get_core_memories(user_id=1)
        assert any(
            m["memory_text"] == "I prefer shorter games during weekdays"
            for m in memories
        )

    def test_save_memory_missing_text(self, tool_executor: ToolExecutor) -> None:
        """Test saving without memory text."""
        result = tool_executor.execute(
            "save_memory",
            {},
            user_id=1,
        )

        assert not result.success


class TestSearchItems:
    """Tests for search_items tool."""

    def test_search_items_single_match(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with a single match."""
        result = tool_executor.execute(
            "search_items",
            {"query": "Martian"},
            user_id=1,
        )

        assert result.success
        assert "The Martian" in result.message
        assert result.data is not None
        assert len(result.data["matches"]) == 1

    def test_search_items_multiple_matches(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with multiple matches."""
        result = tool_executor.execute(
            "search_items",
            {"query": "d"},  # Matches "Red Dead", "Outer Wilds" (has 'd')
            user_id=1,
        )

        assert result.success
        # Should indicate multiple matches
        if result.data and len(result.data.get("matches", [])) > 1:
            assert result.needs_clarification

    def test_search_items_no_matches(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with no matches."""
        result = tool_executor.execute(
            "search_items",
            {"query": "nonexistent title xyz"},
            user_id=1,
        )

        assert result.success
        assert "No items found" in result.message

    def test_search_items_with_content_type(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test searching with content type filter."""
        result = tool_executor.execute(
            "search_items",
            {"query": "Outer", "content_type": "video_game"},
            user_id=1,
        )

        assert result.success
        assert result.data is not None
        matches = result.data.get("matches", [])
        assert all(m["content_type"] == "video_game" for m in matches)


class TestUnknownTool:
    """Tests for unknown tool handling."""

    def test_unknown_tool_returns_error(self, tool_executor: ToolExecutor) -> None:
        """Test that unknown tools return an error."""
        result = tool_executor.execute(
            "unknown_tool",
            {},
            user_id=1,
        )

        assert not result.success
        assert "Unknown tool" in result.message


class TestFindMatchingItems:
    """Tests for find_matching_items helper."""

    def test_find_matching_items(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test finding items by title."""
        matches = tool_executor.find_matching_items("Outer", user_id=1)

        assert len(matches) == 1
        assert matches[0].title == "Outer Wilds"

    def test_find_matching_items_with_filter(
        self, tool_executor: ToolExecutor, sample_items: list[int]
    ) -> None:
        """Test finding items with content type filter."""
        matches = tool_executor.find_matching_items(
            "Outer", user_id=1, content_type=ContentType.BOOK
        )

        # Should not find Outer Wilds since it's a game
        assert len(matches) == 0


class TestParseToolCall:
    """Tests for parsing tool calls from text."""

    def test_parse_json_tool_call(self) -> None:
        """Test parsing a JSON tool call."""
        text = '{"tool": "mark_completed", "params": {"item_id": 1, "rating": 5}}'
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name == "mark_completed"
        assert params == {"item_id": 1, "rating": 5}

    def test_parse_function_format(self) -> None:
        """Test parsing alternative function format."""
        text = '{"function": "save_memory", "arguments": {"memory_text": "test"}}'
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name == "save_memory"
        assert params == {"memory_text": "test"}

    def test_parse_no_tool_call(self) -> None:
        """Test that non-tool text returns None."""
        text = "This is just a regular response with no tool call."
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name is None
        assert params is None

    def test_parse_invalid_json(self) -> None:
        """Test that invalid JSON returns None."""
        text = "Here's some text with {invalid json"
        tool_name, params = parse_tool_call_from_text(text)

        assert tool_name is None
        assert params is None
