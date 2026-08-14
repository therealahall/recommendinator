"""Identity keys the recommendation engine maps candidates by.

``ContentItem.id`` is the source's external id and is nullable: CSV imports,
chat additions and manual completions all carry ``None``.  Keying a lookup on
it collapses every one of those items onto a single entry, which is why
nothing in ``src/recommendations`` keys on it.  These helpers key on the
database row instead, which every stored item has.
"""

from src.models.content import ContentItem


def library_key(item: ContentItem) -> str:
    """Return the identity of the library row *item* came from.

    Season-level candidates expanded from a TV show share their parent's row
    and therefore share this key.

    Args:
        item: A candidate or a library item.

    Returns:
        ``db_<db_id>``, or a key scoped to this object for an item that has no
        row yet — a missing ``db_id`` is not a shared identity.
    """
    if item.db_id is None:
        return f"obj_{id(item)}"
    return f"db_{item.db_id}"


def candidate_key(item: ContentItem) -> str:
    """Return the identity of *item* as one recommendation candidate.

    Season-expanded TV candidates share their parent show's ``db_id``, so the
    season number is appended to keep siblings distinct.

    A season read back out of a stored metadata blob arrives as the string
    ``"1"``, which must key the same candidate as the integer 1: keying it as a
    show instead would collapse it onto its show's bare key and back onto the
    sibling seasons sharing it.

    Args:
        item: A candidate being scored, filtered, ranked or formatted.

    Returns:
        The library key, suffixed ``#s<n>`` for a season-level candidate.
    """
    season = item.metadata.get("season")
    if isinstance(season, str):
        try:
            season = int(season)
        except ValueError:
            # Season metadata is user-supplied, and this runs on every
            # candidate, so a value int() refuses names no season rather than
            # failing the request.  Attempted rather than pre-checked: "²"
            # passes str.isdigit(), and int() refuses any string past 4300
            # digits however ordinary its characters.
            return library_key(item)
    if isinstance(season, int):
        return f"{library_key(item)}#s{season}"
    return library_key(item)
