"""Regression tests for _fix_author_attributions edge cases.

Bug reported: LLM batch recommendation prompts produce reasoning
that cross-contaminates author names between items (e.g. Dune's
reasoning mentions "Isaac Asimov" instead of "Frank Herbert").

Root cause: _fix_author_attributions post-processes LLM output
to detect and correct single-wrong-author references. These tests
cover edge cases not hit by the happy-path correction.

Fix: _fix_author_attributions already handles the happy path. These
tests guard the edge-case boundaries: ambiguous multi-wrong-author,
None author items, backslash characters, empty inputs.
"""

from typing import Any

from src.llm.recommendations import _fix_author_attributions
from src.models.content import ConsumptionStatus, ContentItem, ContentType


def _make_rec(
    title: str,
    author: str | None,
    reasoning: str,
) -> dict[str, Any]:
    """Build a recommendation dict with a ContentItem."""
    return {
        "item": ContentItem(
            id=title.lower().replace(" ", "_"),
            title=title,
            author=author,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        "reasoning": reasoning,
    }


class TestAmbiguousMultipleWrongAuthors:
    """When 2+ wrong batch authors appear in reasoning, leave it untouched.

    Regression: If the LLM cross-contaminated reasoning with multiple
    wrong authors from the batch, substituting just one would produce
    misleading text. The function correctly skips substitution when
    the wrong-author count is ambiguous (>1).
    """

    def test_no_substitution_when_two_wrong_authors_present(self) -> None:
        """Reasoning mentioning two wrong authors should not be modified."""
        recs = [
            _make_rec("Book A", "Alice Author", "Written by Bob Writer and Carol Pen"),
            _make_rec("Book B", "Bob Writer", "A great book by Bob Writer"),
            _make_rec("Book C", "Carol Pen", "Carol Pen delivers again"),
        ]

        original_reasoning = recs[0]["reasoning"]
        _fix_author_attributions(recs)

        # Two wrong authors found in reasoning for Book A — no substitution
        assert recs[0]["reasoning"] == original_reasoning


class TestNoneAuthorItems:
    """Items with None author should be skipped gracefully."""

    def test_none_author_item_skipped(self) -> None:
        """Items without an author are not processed but don't crash."""
        recs = [
            _make_rec("Book A", None, "Some reasoning about Bob Writer"),
            _make_rec("Book B", "Bob Writer", "A great book by Bob Writer"),
        ]

        # Should not raise
        _fix_author_attributions(recs)

        # Reasoning unchanged for the None-author item
        assert recs[0]["reasoning"] == "Some reasoning about Bob Writer"

    def test_none_author_not_in_batch_authors(self) -> None:
        """None-author items should not contribute to the batch author pool."""
        recs = [
            _make_rec("Book A", None, "Reasoning text"),
            _make_rec("Book B", "Bob Writer", "Mentions Alice Author here"),
            _make_rec("Book C", "Alice Author", "Written by Alice Author"),
        ]

        _fix_author_attributions(recs)

        # Book B mentions wrong author "Alice Author" without mentioning "Bob Writer".
        # Exactly one wrong author found — should be substituted.
        assert "Bob Writer" in recs[1]["reasoning"]


class TestBackslashInAuthorName:
    """Author names containing backslashes should not break regex substitution.

    Regression: re.sub treats backslashes specially in both patterns and
    replacements. Author names with backslashes (pen names, transliterated
    names) must be escaped to avoid regex errors or garbled output.
    """

    def test_backslash_in_correct_author(self) -> None:
        r"""Author name with backslash (e.g. pen name like 'A\\B') is handled."""
        recs = [
            _make_rec("Book A", "Auth\\or A", "This was written by Author B"),
            _make_rec("Book B", "Author B", "Great work by Author B"),
        ]

        _fix_author_attributions(recs)

        # Should substitute without regex error
        assert "Auth\\or A" in recs[0]["reasoning"]
        # Wrong author should be removed
        assert "Author B" not in recs[0]["reasoning"]

    def test_backslash_in_wrong_author(self) -> None:
        r"""Wrong author name with backslash is properly escaped in regex."""
        recs = [
            _make_rec("Book A", "Author A", "Written by Auth\\or B"),
            _make_rec("Book B", "Auth\\or B", "Great work by Auth\\or B"),
        ]

        _fix_author_attributions(recs)

        assert "Author A" in recs[0]["reasoning"]
        # Wrong author with backslash should be removed
        assert "Auth\\or B" not in recs[0]["reasoning"]


class TestNoneItemInRecommendation:
    """Recommendations with item=None should be skipped gracefully."""

    def test_none_item_skipped(self) -> None:
        """A recommendation dict with item=None does not crash the function."""
        recs = [
            {"item": None, "reasoning": "Some reasoning about Author B"},
            _make_rec("Book B", "Author B", "A great book by Author B"),
        ]

        # Should not raise
        _fix_author_attributions(recs)

        # None-item reasoning is unchanged
        assert recs[0]["reasoning"] == "Some reasoning about Author B"


class TestEmptyInputList:
    """Empty recommendation list should be handled gracefully."""

    def test_empty_list_no_crash(self) -> None:
        """_fix_author_attributions with empty list does not crash."""
        recs: list[dict[str, Any]] = []
        _fix_author_attributions(recs)
        assert recs == []


class TestSingleWrongAuthorSubstitution:
    """Verify the happy-path: exactly one wrong author gets replaced."""

    def test_single_wrong_author_replaced(self) -> None:
        """When exactly one wrong author is in reasoning, substitute the correct one."""
        recs = [
            _make_rec("Dune", "Frank Herbert", "Written by Isaac Asimov, this epic..."),
            _make_rec("Foundation", "Isaac Asimov", "Asimov delivers a masterpiece"),
        ]

        _fix_author_attributions(recs)

        assert "Frank Herbert" in recs[0]["reasoning"]
        assert "Isaac Asimov" not in recs[0]["reasoning"]

    def test_correct_author_already_present_no_change(self) -> None:
        """When reasoning already mentions the correct author, skip it."""
        recs = [
            _make_rec("Dune", "Frank Herbert", "Frank Herbert created a vast world"),
            _make_rec("Foundation", "Isaac Asimov", "Asimov delivers a masterpiece"),
        ]

        _fix_author_attributions(recs)

        # No change needed — correct author already present
        assert recs[0]["reasoning"] == "Frank Herbert created a vast world"
