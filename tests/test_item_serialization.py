"""Tests for the shared CLI/web ContentItem serialization helpers."""

from src.utils.item_serialization import item_to_dict
from tests.factories import make_item


def test_unknown_enriched_serializes_as_false() -> None:
    """A default ContentItem (enriched=None) serializes enriched as False.

    The wire type is a non-nullable bool, so an unknown enrichment state
    (an item not read back from storage) intentionally collapses to False.
    """
    item = make_item()
    assert item.enriched is None

    serialized = item_to_dict(item)

    assert serialized["enriched"] is False
