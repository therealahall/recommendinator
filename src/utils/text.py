"""Text formatting utilities."""

from __future__ import annotations

_UPPERCASE_WORDS: dict[str, str] = {
    "tv": "TV",
    "gog": "GOG",
    "api": "API",
    "id": "ID",
    "csv": "CSV",
    "json": "JSON",
}


def humanize_source_id(source_id: str) -> str:
    """Convert a snake_case source ID to a human-readable title.

    Applies title-casing with special handling for known acronyms.

    Examples:
        ``finished_tv_shows`` → ``Finished TV Shows``
        ``gog`` → ``GOG``
        ``my_books`` → ``My Books``
        ``personal_site_games`` → ``Personal Site Games``
    """
    words = source_id.split("_")
    return " ".join(_UPPERCASE_WORDS.get(word, word.capitalize()) for word in words)
