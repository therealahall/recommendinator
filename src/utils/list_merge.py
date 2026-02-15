"""Utilities for merging string lists with case-insensitive deduplication."""


def merge_string_lists(existing: list[str], new: list[str]) -> list[str]:
    """Merge two string lists, deduplicating case-insensitively.

    Preserves the original casing of the first occurrence.  Items from
    *existing* appear before items from *new*.

    Args:
        existing: Current list of strings (these take priority for casing).
        new: Incoming list of strings to merge.

    Returns:
        Merged list with duplicates removed (case-insensitive comparison).
    """
    seen_lower: set[str] = set()
    result: list[str] = []

    for item in existing:
        lower = item.lower()
        if lower not in seen_lower:
            seen_lower.add(lower)
            result.append(item)

    for item in new:
        lower = item.lower()
        if lower not in seen_lower:
            seen_lower.add(lower)
            result.append(item)

    return result
