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
    """The save door skips a candidate already holding another of the incoming
    source's ids, so the two rows it lands are never reconsidered on any sync."""
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
    """The normalizer strips a series marker, and "(Malazan Book 2)" is not one,
    so nothing but the bare title brings these two keys together."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")

    (suggestion,) = manager.list_duplicate_suggestions()

    assert (suggestion.survivor.db_id, suggestion.absorbed.db_id) == (
        calibre,
        goodreads,
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
    """Both vetoes are the save door's own — a remake and another author's book are
    two works — and a cross-type pair would be a suggestion the merge door refuses."""
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
    """A decline names two rows, and a re-sync lands on the rows it already has,
    so the pair the operator refused cannot come back."""
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
    """An id from a stale surface would insert against a foreign key, and one row
    twice would record a refusal no pair can ever match."""
    calibre = _save(manager, "calibre", "1", "Deadhouse Gates")
    goodreads = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    manager.merge_content_items(calibre, goodreads, MergeEvidence.MANUAL)

    assert manager.decline_duplicate_suggestion(calibre, calibre) is False
    assert manager.decline_duplicate_suggestion(calibre, 9999) is False
    assert manager.decline_duplicate_suggestion(calibre, goodreads) is False


def test_a_group_of_three_offers_every_pair_and_drops_the_ones_a_merge_settles(
    manager: StorageManager,
) -> None:
    """A group is resolved one merge at a time, so the pairs have to be offered
    separately and the row a merge hid must not come back as half of one."""
    first = _save(manager, "calibre", "1", "Deadhouse Gates")
    second = _save(manager, "goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)")
    third = _save(manager, "storygraph_csv", "3", "Deadhouse Gates (Malazan, Book Two)")

    assert _pairs(manager) == [(first, second), (first, third), (second, third)]

    manager.merge_content_items(first, second, MergeEvidence.MANUAL)

    assert _pairs(manager) == [(first, third)]
