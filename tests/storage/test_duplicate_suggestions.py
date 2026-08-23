"""Duplicates already in a library are offered, and a refusal is durable.

Through ``StorageManager``, the seam both interfaces call. Every library here
is saved item by item, so a pair exists only if the save door leaves one.
"""

import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.duplicates import GROUP_BLOCK_MAX, GROUP_MEMBER_MAX
from src.storage.manager import (
    DuplicateSuggestion,
    MergeError,
    MergeEvidence,
    StorageManager,
    SuggestionEvidence,
)
from src.utils.duplicate_serialization import skipped_works_note


@contextmanager
def _within(seconds: int) -> Iterator[None]:
    """Fail rather than hang: the defect guarded here is a pass that never ends."""

    def ring(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError(f"still offering duplicates after {seconds}s")

    previous = signal.signal(signal.SIGALRM, ring)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def manager(tmp_path: Path) -> StorageManager:
    return StorageManager(tmp_path / "library.db")


def _save(
    manager: StorageManager, source: str, external_id: str, title: str, **fields: Any
) -> int:
    return manager.save_content_item(
        ContentItem(
            id=external_id,
            source=source,
            title=title,
            content_type=fields.pop("content_type", ContentType.BOOK),
            status=fields.pop("status", ConsumptionStatus.UNREAD),
            **fields,
        )
    )


def _blocks(manager: StorageManager) -> list[list[int]]:
    return [
        [copy.db_id for copy in suggestion.copies]
        for suggestion in manager.list_duplicate_suggestions().suggestions
    ]


def _offered(manager: StorageManager) -> list[DuplicateSuggestion]:
    return manager.list_duplicate_suggestions().suggestions


def _shelf(manager: StorageManager, count: int) -> list[int]:
    return [
        _save(
            manager,
            "calibre",
            str(index),
            f"The Odyssey (translation {index})",
            author="Homer",
        )
        for index in range(count)
    ]


def test_one_source_listing_a_book_twice_leaves_a_pair_the_pass_offers(
    manager: StorageManager,
) -> None:
    """The door skips a candidate holding another id of the incoming source."""
    first = _save(manager, "goodreads_csv", "1", "The Gate of the Feral Gods")
    second = _save(manager, "goodreads_csv", "2", "The Gate of The Feral Gods")
    assert first != second

    (suggestion,) = _offered(manager)

    assert [copy.db_id for copy in suggestion.copies] == [first, second]
    assert suggestion.evidence is SuggestionEvidence.NORMALIZED_TITLE
    assert suggestion.evidence_detail == "gate of the feral gods"
    assert suggestion.content_type == "book"


def test_a_parenthetical_the_matching_key_keeps_still_offers_the_pair(
    manager: StorageManager,
) -> None:
    """ "(Malazan Book 2)" is no series marker, so only the bare key joins these."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(
        manager,
        "goodreads_csv",
        "2",
        "Deadhouse Gates (Malazan Book 2)",
        author="Steven Erikson",
    )

    (suggestion,) = _offered(manager)

    assert [copy.db_id for copy in suggestion.copies] == [calibre, goodreads]
    assert suggestion.survivor_id == calibre
    assert [copy.creator for copy in suggestion.copies] == [None, "Steven Erikson"]
    assert suggestion.evidence is SuggestionEvidence.TITLE_QUALIFIER
    assert suggestion.evidence_detail == "deadhouse gates"
    assert [copy.source for copy in suggestion.copies] == ["calibre", "goodreads_csv"]


def test_a_pair_the_save_door_would_refuse_is_never_offered(
    manager: StorageManager,
) -> None:
    """Both vetoes are the door's own, and the merge door refuses a cross type."""
    _save(manager, "gog", "1", "Doom", content_type=ContentType.VIDEO_GAME)
    _save(manager, "steam", "2", "DOOM (2016)", content_type=ContentType.VIDEO_GAME)
    _save(manager, "calibre", "3", "Dune", author="Frank Herbert")
    _save(manager, "goodreads_csv", "4", "Dune", author="Alexander Freed")
    _save(manager, "trakt", "5", "Dune", content_type=ContentType.MOVIE)

    assert manager.count_items() == 5
    assert _offered(manager) == []


def test_two_regions_are_never_offered_though_each_pairs_with_the_bare_row(
    manager: StorageManager,
) -> None:
    """The bare key gathers all three, so the operator's US and AU rows meet
    here, and the row qualifying neither is all either may be offered against."""
    us = _save(
        manager, "sonarr", "1", "The Traitors (US)", content_type=ContentType.TV_SHOW
    )
    au = _save(
        manager, "sonarr", "2", "The Traitors (AU)", content_type=ContentType.TV_SHOW
    )
    bare = _save(
        manager, "sonarr", "3", "The Traitors", content_type=ContentType.TV_SHOW
    )

    assert _blocks(manager) == [[us, bare], [au, bare]]


def test_a_declined_pair_stays_declined_when_both_its_sources_sync_again(
    manager: StorageManager,
) -> None:
    """A re-sync lands on the rows it has, so a refused pair cannot come back."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")

    (refused,) = manager.decline_duplicate_suggestion(goodreads, [calibre])
    assert (refused.one_id, refused.other_id) == (calibre, goodreads)
    assert manager.decline_duplicate_suggestion(calibre, [goodreads]) == [refused]

    assert _save(manager, "calibre", "1", "Deadhouse Gates") == calibre
    assert (
        _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
        == goodreads
    )
    assert _offered(manager) == []


def test_declining_what_is_not_a_live_pair_reports_it_instead_of_raising(
    manager: StorageManager,
) -> None:
    """A stale id would insert against a foreign key; one row twice refuses none."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    manager.merge_content_items(calibre, goodreads, MergeEvidence.MANUAL)

    assert manager.decline_duplicate_suggestion(calibre, [calibre]) == []
    assert manager.decline_duplicate_suggestion(calibre, [9999]) == []
    assert manager.decline_duplicate_suggestion(calibre, [goodreads]) == []


def test_a_refusal_waits_for_every_merge_holding_a_side_not_only_the_first(
    manager: StorageManager,
) -> None:
    """Reported: lifting a refusal over a row a merge had hidden emptied the
    declined list and offered nothing back, losing the decision. Checking one
    side alone lifts one the other side's merge still hides."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    fourth = _save(manager, "hardcover", "4", "Deadhouse Gates (Book Two of Malazan)")
    assert manager.decline_duplicate_suggestion(first, [third]) != []
    holds_first = manager.merge_content_items(second, first, MergeEvidence.MANUAL)
    holds_third = manager.merge_content_items(fourth, third, MergeEvidence.MANUAL)

    with pytest.raises(MergeError, match=f"before merge {holds_first.id}"):
        manager.undecline_duplicate_suggestion(first, third)
    listed = manager.list_declined_duplicates()
    assert [(pair.one_id, pair.other_id) for pair in listed] == [(first, third)]
    assert manager.unmerge_content_items(holds_first.id) == holds_first
    with pytest.raises(MergeError, match=f"before merge {holds_third.id}"):
        manager.undecline_duplicate_suggestion(first, third)
    assert manager.decline_duplicate_suggestion(first, [third]) == []

    assert manager.unmerge_content_items(holds_third.id) == holds_third
    assert manager.undecline_duplicate_suggestion(first, third) is not None
    assert _blocks(manager) == [[first, second, third, fourth]]


def test_every_pair_the_pass_offers_can_be_merged_the_way_it_is_offered(
    manager: StorageManager,
) -> None:
    """A group resolved out of order offers a pair that has absorbed its own."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    manager.merge_content_items(second, third, MergeEvidence.MANUAL)

    (offered,) = _offered(manager)
    assert [copy.db_id for copy in offered.copies] == [first, second]
    manager.merge_content_items(offered.survivor_id, second, MergeEvidence.MANUAL)

    assert manager.count_items() == 1
    assert _offered(manager) == []


def test_a_group_of_four_settles_from_one_listing_without_a_fresh_offer(
    manager: StorageManager,
) -> None:
    """Every pair of a block stays mergeable as its copies are folded in, so a
    bulk pass never has to re-read the offer between them."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    fourth = _save(manager, "hardcover", "4", "Deadhouse Gates (Book Two of Malazan)")
    assert len({first, second, third, fourth}) == 4

    (offered,) = _offered(manager)

    assert [copy.db_id for copy in offered.copies] == [first, second, third, fourth]
    for copy in offered.copies:
        if copy.db_id != offered.survivor_id:
            manager.merge_content_items(
                offered.survivor_id, copy.db_id, MergeEvidence.MANUAL
            )

    kept = manager.get_content_item(first)
    assert kept is not None
    assert sorted(pair.source for pair in kept.external_ids) == [
        "calibre",
        "goodreads_csv",
        "hardcover",
        "storygraph_csv",
    ]
    assert manager.count_items() == 1
    assert _offered(manager) == []


def test_a_merge_inside_a_block_leaves_the_copies_it_did_not_touch_offered(
    manager: StorageManager,
) -> None:
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")

    assert _blocks(manager) == [[first, second, third]]

    manager.merge_content_items(first, second, MergeEvidence.MANUAL)

    assert _blocks(manager) == [[first, third]]


def test_a_copy_declined_out_of_a_block_leaves_the_others_pairing(
    manager: StorageManager,
) -> None:
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")

    refused = manager.decline_duplicate_suggestion(third, [first, second])

    assert [(pair.one_id, pair.other_id) for pair in refused] == [
        (first, third),
        (second, third),
    ]
    assert [
        (pair.one_id, pair.other_id) for pair in manager.list_declined_duplicates()
    ] == [(first, third), (second, third)]
    assert _blocks(manager) == [[first, second]]

    manager.merge_content_items(first, second, MergeEvidence.MANUAL)

    assert _offered(manager) == []


def test_a_decline_naming_one_dead_id_stores_none_of_the_pairs_it_named(
    manager: StorageManager,
) -> None:
    """Stored pair by pair, so a refusal reported as refused must leave nothing
    behind: a half-written one takes a pairing off the block with no row in the
    declined list to lift it back."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")

    assert manager.decline_duplicate_suggestion(third, [first, 9999]) == []

    assert manager.list_declined_duplicates() == []
    assert _blocks(manager) == [[first, second, third]]


def test_a_shelf_where_every_copy_pairs_with_every_other_is_offered_at_once(
    manager: StorageManager,
) -> None:
    """A clique of 30 costs a search without a pivot 2^30 calls for one block."""
    shelf = _shelf(manager, 30)

    with _within(seconds=10):
        blocks = _blocks(manager)

    assert blocks == [shelf]


def test_a_group_past_the_cap_is_skipped_with_a_line_in_the_log_saying_so(
    manager: StorageManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Skipped, not dropped: nothing else would say the group is even there."""
    _shelf(manager, GROUP_MEMBER_MAX + 1)

    with caplog.at_level(logging.WARNING), _within(seconds=10):
        page = manager.list_duplicate_suggestions()

    assert page.suggestions == []
    assert page.skipped_works == 1
    assert f"the {GROUP_MEMBER_MAX + 1} copies" in caplog.text
    assert "is not offered for review" in caplog.text


def test_a_group_split_every_which_way_is_skipped_without_blaming_its_copies(
    manager: StorageManager, caplog: pytest.LogCaptureFixture
) -> None:
    """d disjoint refusals make 2^d blocks. This shelf sits at the copy cap
    rather than over it, so the note's old "too many copies" was a lie."""
    shelf = _shelf(manager, GROUP_MEMBER_MAX)
    for one, other in zip(shelf[::2], shelf[1::2], strict=True):
        assert manager.decline_duplicate_suggestion(one, [other]) != []

    with caplog.at_level(logging.WARNING), _within(seconds=10):
        page = manager.list_duplicate_suggestions()
    cause, _, advice = skipped_works_note(page.skipped_works).partition(". ")

    assert page.suggestions == []
    assert page.skipped_works == 1
    assert f"more than {GROUP_BLOCK_MAX} blocks" in caplog.text
    assert advice, "the cause is its own sentence, ahead of what to do about it"
    assert "copies" not in cause


def test_two_works_left_unsearched_read_as_the_plural_they_are_counted_in(
    manager: StorageManager,
) -> None:
    """Both shelves are over the copy cap, so the note counts two: catches the
    subject and the possessive drifting apart, "2 works are ... its copies"."""
    for title in ("The Wandering Inn", "The Odyssey"):
        for index in range(GROUP_MEMBER_MAX + 1):
            _save(manager, "calibre", f"{title} {index}", f"{title} ({index})")

    page = manager.list_duplicate_suggestions()
    note = skipped_works_note(page.skipped_works)

    assert page.skipped_works == 2
    assert "2 works" in note
    assert "its" not in note.split()


def test_one_refusal_inside_a_group_of_four_leaves_both_blocks_it_splits_into(
    manager: StorageManager,
) -> None:
    """The refused two each still pair with the other two, so the group is two
    overlapping blocks; a pass keeping one block per copy drops the second."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    fourth = _save(manager, "hardcover", "4", "Deadhouse Gates (Book Two of Malazan)")

    assert manager.decline_duplicate_suggestion(first, [second]) != []

    assert _blocks(manager) == [[first, third, fourth], [second, third, fourth]]
