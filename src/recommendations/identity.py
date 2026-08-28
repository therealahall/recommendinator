"""``ContentItem.id`` is the source's external id and is nullable. Keying a
lookup on it collapses every one of those items onto a single entry, which is
why nothing in ``src/recommendations`` keys on it.
"""

from src.models.content import ContentItem


def library_key(item: ContentItem) -> str:
    """Return the identity of the library row *item* came from."""
    if item.db_id is None:
        return f"obj_{id(item)}"
    return f"db_{item.db_id}"


def candidate_key(item: ContentItem) -> str:
    """A season read back out of a stored metadata blob arrives as the string
    ``"1"``, which must key the same candidate as the integer 1.
    """
    season = item.metadata.get("season")
    if isinstance(season, str):
        try:
            season = int(season)
        except ValueError:
            # Season metadata is user-supplied, and this runs on every
            # candidate, so a value int() refuses names no season rather than
            # failing the request.
            return library_key(item)
    if isinstance(season, int):
        return f"{library_key(item)}#s{season}"
    return library_key(item)
