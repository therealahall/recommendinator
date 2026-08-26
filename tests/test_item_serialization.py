"""Tests for the shared CLI/web ContentItem serialization helpers."""

import pytest

from src.storage.duplicates import (
    DeclinedPair,
    DuplicateSide,
    DuplicateSuggestion,
    SuggestionEvidence,
    SuggestionPage,
)
from src.storage.item_merges import MergeEvidence, MergeRecord
from src.utils.duplicate_serialization import (
    declined_pair_to_dict,
    merge_to_dict,
    suggestion_page_to_dict,
    suggestion_to_dict,
)
from src.utils.item_serialization import item_to_dict
from src.web.api import (
    ContentItemResponse,
    DeclinedPairResponse,
    DuplicateSideResponse,
    DuplicateSuggestionPageResponse,
    DuplicateSuggestionResponse,
    MergeResponse,
)
from tests.factories import make_item

_A_SIDE = DuplicateSide(
    db_id=3, title="Deadhouse Gates", source="calibre", creator=None, release_year=None
)

_A_SUGGESTION = DuplicateSuggestion(
    content_type="book",
    evidence=SuggestionEvidence.TITLE_QUALIFIER,
    evidence_detail="deadhouse gates",
    survivor_id=_A_SIDE.db_id,
    copies=(_A_SIDE, _A_SIDE),
)

_A_MERGE = MergeRecord(
    id=1,
    survivor_id=3,
    survivor_title="Deadhouse Gates",
    absorbed_id=4,
    absorbed_title="Deadhouse Gates (Malazan Book 2)",
    evidence=MergeEvidence.MANUAL,
    evidence_detail=None,
    merged_at="2026-08-20 00:00:00",
)

_A_DECLINE = DeclinedPair(one_id=3, one_title="One", other_id=4, other_title="Other")


def test_unknown_enriched_serializes_as_false() -> None:
    """A default ContentItem (enriched=None) serializes enriched as False.

    The wire type is a non-nullable bool, so an unknown enrichment state
    (an item not read back from storage) intentionally collapses to False.
    """
    item = make_item()
    assert item.enriched is None

    serialized = item_to_dict(item)

    assert serialized["enriched"] is False


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"series": "The Murderbot Diaries", "series_index": 1.0}, 1.0),
        ({"series": "The Murderbot Diaries"}, None),
        ({"series": "The Murderbot Diaries", "series_index": "nonsense"}, None),
    ],
    ids=["stated", "no-position", "unreadable-position"],
)
def test_a_series_the_title_no_longer_states_reaches_both_interfaces(
    metadata: dict[str, object], expected: float | None
) -> None:
    serialized = item_to_dict(make_item(title="All Systems Red", metadata=metadata))

    assert serialized["series"] == "The Murderbot Diaries"
    assert serialized["series_index"] == expected


def test_the_cli_json_and_the_web_response_carry_the_same_keys() -> None:
    """``--format json`` emits this dict as it stands while the web validates
    it into ContentItemResponse, which drops a key the model does not declare.
    """
    assert set(item_to_dict(make_item())) == set(ContentItemResponse.model_fields)


def test_every_duplicates_payload_carries_the_web_response_model_s_keys() -> None:
    assert set(suggestion_to_dict(_A_SUGGESTION)) == set(
        DuplicateSuggestionResponse.model_fields
    )
    copy, _ = suggestion_to_dict(_A_SUGGESTION)["copies"]  # type: ignore[misc]
    assert set(copy) == set(DuplicateSideResponse.model_fields)
    assert set(suggestion_page_to_dict(SuggestionPage(total=1, suggestions=[]))) == set(
        DuplicateSuggestionPageResponse.model_fields
    )
    assert set(merge_to_dict(_A_MERGE)) == set(MergeResponse.model_fields)
    assert set(declined_pair_to_dict(_A_DECLINE)) == set(
        DeclinedPairResponse.model_fields
    )
