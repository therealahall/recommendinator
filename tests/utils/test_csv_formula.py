from typing import Any

import pytest

from src.utils.csv_formula import (
    _FORMULA_PREFIXES,
    guard_csv_formula,
    strip_csv_formula_guard,
)


class TestCsvFormulaGuardHelpers:
    @pytest.mark.parametrize("prefix", _FORMULA_PREFIXES)
    def test_every_prefix_is_guarded_and_stripped_back(self, prefix: str) -> None:
        original = f"{prefix}1+1"

        guarded = guard_csv_formula(original)

        assert guarded == f"'{original}"
        assert strip_csv_formula_guard(guarded) == original

    @pytest.mark.parametrize(
        "value",
        [None, 42, ["=1+1"]],
        ids=["none", "int", "list"],
    )
    def test_a_non_string_cell_passes_through_both_ways(self, value: Any) -> None:
        assert guard_csv_formula(value) is value
        assert strip_csv_formula_guard(value) is value

    def test_a_quote_without_a_formula_behind_it_is_not_a_guard(self) -> None:
        assert strip_csv_formula_guard("'Salem's Lot") == "'Salem's Lot"
