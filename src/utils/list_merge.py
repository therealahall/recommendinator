def merge_string_lists(existing: list[str], new: list[str]) -> list[str]:
    """Preserves the original casing of the first occurrence."""
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
