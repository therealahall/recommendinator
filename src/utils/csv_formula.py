"""Formula guarding for CSV cells, shared by the CSV import and the export."""

from typing import Any

# Spreadsheets evaluate a cell opening with one of these, and an exported
# title or genre can be text TMDB or RAWG supplied. The guard and its strip
# live together because they only work if they agree.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_GUARDED_PREFIXES = tuple(f"'{prefix}" for prefix in _FORMULA_PREFIXES)


def guard_csv_formula(value: Any) -> Any:
    """Prefix a leading formula character with an apostrophe.

    Non-string cells — a rating, a year — are returned untouched.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def strip_csv_formula_guard(value: Any) -> Any:
    """Undo :func:`guard_csv_formula` so a re-import restores the original.

    Only an apostrophe with a formula character right behind it is a guard,
    so a title that genuinely opens with a quote survives.
    """
    if isinstance(value, str) and value.startswith(_GUARDED_PREFIXES):
        return value[1:]
    return value
