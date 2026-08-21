"""Duplicates already in a library are offered, and a refusal is durable.

Through ``StorageManager``, the seam both interfaces call. Every library here
is saved item by item, so a pair exists only if the save door leaves one.
"""

from pathlib import Path
from typing import Any

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import (
    DuplicateSuggestion,
    MergeError,
    MergeEvidence,
    StorageManager,
    SuggestionEvidence,
)


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
        for suggestion in manager.list_duplicate_suggestions().suggestions
    ]


def _offered(manager: StorageManager) -> list[DuplicateSuggestion]:
    return manager.list_duplicate_suggestions().suggestions


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

    (suggestion,) = _offered(manager)

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

    (suggestion,) = _offered(manager)

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
    assert _offered(manager) == []


def test_two_regions_are_never_offered_though_each_pairs_with_the_bare_row(
    manager: StorageManager,
) -> None:
    """The bare key gathers every Traitors, so this pass is where the operator's
    US and AU rows meet; the row qualifying neither is still one of them."""
    us = _save(
        manager, "sonarr", "1", "The Traitors (US)", content_type=ContentType.TV_SHOW
    )
    au = _save(
        manager, "sonarr", "2", "The Traitors (AU)", content_type=ContentType.TV_SHOW
    )
    bare = _save(
        manager, "sonarr", "3", "The Traitors", content_type=ContentType.TV_SHOW
    )

    assert _pairs(manager) == [(us, bare), (au, bare)]


def test_a_declined_pair_stays_declined_when_both_its_sources_sync_again(
    manager: StorageManager,
) -> None:
    """A re-sync lands on the rows it has, so a refused pair cannot come back."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")

    refused = manager.decline_duplicate_suggestion(goodreads, calibre)
    assert refused is not None
    assert (refused.one_id, refused.other_id) == (calibre, goodreads)
    assert manager.decline_duplicate_suggestion(calibre, goodreads) == refused

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

    assert manager.decline_duplicate_suggestion(calibre, calibre) is None
    assert manager.decline_duplicate_suggestion(calibre, 9999) is None
    assert manager.decline_duplicate_suggestion(calibre, goodreads) is None


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
    assert manager.decline_duplicate_suggestion(first, third) is not None
    holds_first = manager.merge_content_items(second, first, MergeEvidence.MANUAL)
    holds_third = manager.merge_content_items(fourth, third, MergeEvidence.MANUAL)

    with pytest.raises(MergeError, match=f"before merge {holds_first.id}"):
        manager.undecline_duplicate_suggestion(first, third)
    listed = manager.list_declined_duplicates()
    assert [(pair.one_id, pair.other_id) for pair in listed] == [(first, third)]
    assert manager.unmerge_content_items(holds_first.id) == holds_first
    with pytest.raises(MergeError, match=f"before merge {holds_third.id}"):
        manager.undecline_duplicate_suggestion(first, third)
    assert manager.decline_duplicate_suggestion(first, third) is None

    assert manager.unmerge_content_items(holds_third.id) == holds_third
    assert manager.undecline_duplicate_suggestion(first, third) is not None
    assert (first, third) in _pairs(manager)


def test_every_pair_the_pass_offers_can_be_merged_the_way_it_is_offered(
    manager: StorageManager,
) -> None:
    """A group resolved out of order offers a pair that has absorbed its own."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")
    manager.merge_content_items(second, third, MergeEvidence.MANUAL)

    (offered,) = _offered(manager)
    assert (offered.survivor.db_id, offered.absorbed.db_id) == (first, second)
    manager.merge_content_items(
        offered.survivor.db_id, offered.absorbed.db_id, MergeEvidence.MANUAL
    )

    assert manager.count_items() == 1
    assert _offered(manager) == []


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

    (offered,) = _offered(manager)
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
    assert _offered(manager) == []


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
