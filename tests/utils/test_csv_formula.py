"""Tests for the CSV formula guard and its strip."""

from typing import Any

import pytest

from src.utils.csv_formula import (
    _FORMULA_PREFIXES,
    guard_csv_formula,
    strip_csv_formula_guard,
)


class TestCsvFormulaGuardHelpers:
    """The pair the export and the import each hold one end of.

    ``tests/utils/test_export.py`` exercises them through a file, which cannot
    reach a cell the writer quotes or the reader strips as whitespace.
    """

    @pytest.mark.parametrize("prefix", _FORMULA_PREFIXES)
    def test_every_prefix_is_guarded_and_stripped_back(self, prefix: str) -> None:
        """Parametrised off the constant so a new prefix arrives covered.

        A carriage return is the one no file-level test reaches.
        """
        original = f"{prefix}1+1"

        guarded = guard_csv_formula(original)

        assert guarded == f"'{original}"
        assert strip_csv_formula_guard(guarded) == original

    @pytest.mark.parametrize(
        "value",
        [None, 42, 4.5, True, False, ["=1+1"]],
        ids=["none", "int", "float", "true", "false", "list"],
    )
    def test_a_non_string_cell_passes_through_both_ways(self, value: Any) -> None:
        """The import hands the strip whatever a row holds, lists included."""
        assert guard_csv_formula(value) is value
        assert strip_csv_formula_guard(value) is value

    def test_a_string_opening_with_anything_else_is_untouched(self) -> None:
        assert guard_csv_formula("Dune") == "Dune"
        assert strip_csv_formula_guard("Dune") == "Dune"

    def test_a_quote_without_a_formula_behind_it_is_not_a_guard(self) -> None:
        assert strip_csv_formula_guard("'Salem's Lot") == "'Salem's Lot"

    @pytest.mark.parametrize("value", ["", "'"], ids=["blank", "lone-quote"])
    def test_a_cell_too_short_to_hold_a_guard_survives_both_ways(
        self, value: str
    ) -> None:
        """A blank cell is the commonest one in these files, and a bare
        apostrophe is the shortest string a prefix test can misread.
        """
        assert guard_csv_formula(value) == value
        assert strip_csv_formula_guard(value) == value

    @pytest.mark.parametrize("prefix", _FORMULA_PREFIXES)
    def test_a_cell_holding_nothing_but_the_prefix_round_trips(
        self, prefix: str
    ) -> None:
        """The shortest cell a spreadsheet still evaluates."""
        assert strip_csv_formula_guard(guard_csv_formula(prefix)) == prefix

    def test_a_value_already_opening_with_a_guard_is_left_alone_then_stripped(
        self,
    ) -> None:
        """Where the round trip loses a character, as the README says."""
        assert guard_csv_formula("'=1+1") == "'=1+1"
        assert strip_csv_formula_guard("'=1+1") == "=1+1"
