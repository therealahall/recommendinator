"""Tests for the shared CLI/web ContentItem serialization helpers."""

from src.utils.item_serialization import item_to_dict
from src.web.api import ContentItemResponse
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


def test_the_cli_json_and_the_web_response_carry_the_same_keys() -> None:
    """``--format json`` emits this dict as it stands while the web validates
    it into ContentItemResponse, which drops a key the model does not declare.
    """
    assert set(item_to_dict(make_item())) == set(ContentItemResponse.model_fields)
