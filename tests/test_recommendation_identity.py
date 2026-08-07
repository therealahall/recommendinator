"""Tests for the identity keys the recommendation engine maps candidates by."""

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.identity import candidate_key, library_key
from src.utils.series import expand_tv_shows_to_seasons


def _book(db_id: int | None, item_id: str | None = None) -> ContentItem:
    """Build an unread book candidate."""
    return ContentItem(
        id=item_id,
        db_id=db_id,
        title="Hyperion",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )


def _seasons_of_one_show(db_id: int) -> list[ContentItem]:
    """Expand one two-season show into its season-level candidates."""
    return expand_tv_shows_to_seasons(
        [
            ContentItem(
                id=None,
                db_id=db_id,
                title="Uncharted Depths",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.UNREAD,
                metadata={"total_seasons": 2},
            )
        ]
    )


class TestLibraryKey:
    """The identity of the stored row an item came from."""

    def test_keys_on_the_database_row(self) -> None:
        assert library_key(_book(db_id=7)) == "db_7"

    def test_ignores_the_external_id(self) -> None:
        assert library_key(_book(db_id=7, item_id="goodreads:99")) == "db_7"

    def test_season_candidates_share_their_show_row(self) -> None:
        first, second = _seasons_of_one_show(db_id=7)
        assert library_key(first) == library_key(second) == "db_7"


class TestCandidateKey:
    """The identity of a single scoring candidate."""

    def test_matches_the_library_key_for_an_ordinary_candidate(self) -> None:
        item = _book(db_id=7)
        assert candidate_key(item) == library_key(item)

    def test_id_less_items_do_not_collide(self) -> None:
        assert candidate_key(_book(db_id=7)) != candidate_key(_book(db_id=8))

    def test_season_siblings_do_not_collide(self) -> None:
        first, second = _seasons_of_one_show(db_id=7)
        assert candidate_key(first) == "db_7#s1"
        assert candidate_key(second) == "db_7#s2"

    def test_items_with_no_database_row_do_not_collide(self) -> None:
        """Unsaved items are each their own identity, never a shared one.

        Both items are held in locals for the whole assertion: the keys are
        distinct because the objects coexist, not by luck of the allocator.
        """
        first = _book(db_id=None)
        second = _book(db_id=None)

        assert candidate_key(first) != candidate_key(second)
