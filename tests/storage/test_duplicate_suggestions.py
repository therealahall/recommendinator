"""Duplicates already in a library are offered, and a refusal is durable.

Through ``StorageManager``, the seam both interfaces call. Every library here
is saved item by item, so a pair exists only if the save door leaves one.
"""

from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import MergeEvidence, StorageManager, SuggestionEvidence


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


def _pairs(manager: StorageManager) -> list[tuple[int, int]]:
    return [
        (suggestion.survivor.db_id, suggestion.absorbed.db_id)
        for suggestion in manager.list_duplicate_suggestions()
    ]


def test_one_source_listing_a_book_twice_leaves_a_pair_the_pass_offers(
    manager: StorageManager,
) -> None:
    """The door skips a candidate holding another id of the incoming source."""
    first = _save(manager, "goodreads_csv", "1", "The Gate of the Feral Gods")
    second = _save(
        manager,
        "goodreads_csv",
        "2",
        "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
    )
    assert first != second

    (suggestion,) = manager.list_duplicate_suggestions()

    assert (suggestion.survivor.db_id, suggestion.absorbed.db_id) == (first, second)
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

    (suggestion,) = manager.list_duplicate_suggestions()

    assert (suggestion.survivor.db_id, suggestion.absorbed.db_id) == (
        calibre,
        goodreads,
    )
    assert (suggestion.survivor.creator, suggestion.absorbed.creator) == (
        None,
        "Steven Erikson",
    )
    assert suggestion.evidence is SuggestionEvidence.TITLE_QUALIFIER
    assert suggestion.evidence_detail == "deadhouse gates"
    assert (suggestion.survivor.source, suggestion.absorbed.source) == (
        "calibre",
        "goodreads_csv",
    )


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
    assert manager.list_duplicate_suggestions() == []


def test_a_declined_pair_stays_declined_when_both_its_sources_sync_again(
    manager: StorageManager,
) -> None:
    """A re-sync lands on the rows it has, so a refused pair cannot come back."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")

    assert manager.decline_duplicate_suggestion(goodreads, calibre) is True
    assert manager.decline_duplicate_suggestion(calibre, goodreads) is True

    assert _save(manager, "calibre", "1", "Deadhouse Gates") == calibre
    assert (
        _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
        == goodreads
    )
    assert manager.list_duplicate_suggestions() == []


def test_declining_what_is_not_a_live_pair_reports_it_instead_of_raising(
    manager: StorageManager,
) -> None:
    """A stale id would insert against a foreign key; one row twice refuses none."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    manager.merge_content_items(calibre, goodreads, MergeEvidence.MANUAL)

    assert manager.decline_duplicate_suggestion(calibre, calibre) is False
    assert manager.decline_duplicate_suggestion(calibre, 9999) is False
    assert manager.decline_duplicate_suggestion(calibre, goodreads) is False


def test_every_pair_the_pass_offers_can_be_merged_the_way_it_is_offered(
    manager: StorageManager,
) -> None:
    """A group resolved out of order leaves the pass offering a pair whose
    absorbed side has absorbed one of its own."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    manager.merge_content_items(second, third, MergeEvidence.MANUAL)

    (offered,) = manager.list_duplicate_suggestions()
    assert (offered.survivor.db_id, offered.absorbed.db_id) == (first, second)
    manager.merge_content_items(
        offered.survivor.db_id, offered.absorbed.db_id, MergeEvidence.MANUAL
    )

    assert manager.count_items() == 1
    assert manager.list_duplicate_suggestions() == []


def test_a_group_of_four_settles_from_the_pairs_the_pass_offers(
    manager: StorageManager,
) -> None:
    """Two offered merges leave a pair with a row behind each side."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    fourth = _save(manager, "hardcover", "4", "Deadhouse Gates (Book Two of Malazan)")
    assert len({first, second, third, fourth}) == 4

    manager.merge_content_items(first, second, MergeEvidence.MANUAL)
    manager.merge_content_items(third, fourth, MergeEvidence.MANUAL)

    (offered,) = manager.list_duplicate_suggestions()
    assert (offered.survivor.db_id, offered.absorbed.db_id) == (first, third)
    manager.merge_content_items(
        offered.survivor.db_id, offered.absorbed.db_id, MergeEvidence.MANUAL
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
    assert manager.list_duplicate_suggestions() == []


def test_a_new_row_taking_a_deleted_ones_place_does_not_inherit_the_refusal(
    manager: StorageManager,
) -> None:
    """An id handed out twice hides a duplicate; the DELETE lands by cascade."""
    kept = _save(manager, "calibre", "1", "Deadhouse Gates")
    refused = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    assert manager.decline_duplicate_suggestion(kept, refused) is True

    assert manager.delete_content_item(refused) is True
    replacement = _save(
        manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)"
    )

    assert replacement != refused
    assert _pairs(manager) == [(kept, replacement)]


def test_a_group_of_three_offers_every_pair_and_drops_the_ones_a_merge_settles(
    manager: StorageManager,
) -> None:
    """A group is resolved one merge at a time, so each pair is offered apart."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")

    assert _pairs(manager) == [(first, second), (first, third), (second, third)]

    manager.merge_content_items(first, second, MergeEvidence.MANUAL)

    assert _pairs(manager) == [(first, third)]
