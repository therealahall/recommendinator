from __future__ import annotations

from typing import TYPE_CHECKING

from src.storage.field_writes import WriterBand
from src.utils.item_serialization import field_writers_to_dict

if TYPE_CHECKING:
    from src.storage.manager import StorageManager


class UnfollowableWriterError(ValueError):
    """A choice naming a writer the field cannot follow, worded here so both
    interfaces refuse it in the same words.
    """


def follow_writer(
    storage: StorageManager,
    db_id: int,
    field: str,
    writer: str | None,
    user_id: int,
) -> bool:
    """*writer* ``None`` returns the field to the default order, ``False`` when
    no choice stood. A hold outranks a choice, so picking a writer releases it —
    which is why an unfollowable writer is refused before anything is released.
    """
    named = field.lower()
    if writer is not None:
        _refuse_unfollowable(storage, db_id, named, writer, user_id)
        storage.clear_manual_field(db_id, named, user_id=user_id)
    return storage.set_field_choice(db_id, named, writer, user_id=user_id)


def _refuse_unfollowable(
    storage: StorageManager, db_id: int, field: str, writer: str, user_id: int
) -> None:
    followable = _followable_writers(storage, db_id, field, user_id)
    if writer not in followable:
        raise UnfollowableWriterError(
            f"Item {db_id} cannot follow {writer} for {field}."
            f" It can follow: {', '.join(followable) or 'nothing'}."
        )


def _followable_writers(
    storage: StorageManager, db_id: int, field: str, user_id: int
) -> list[str]:
    """The writers the switcher offers for this field, which is the set a choice
    may name. The operator's own entry is not among them: it is the value in
    force, and following it would mean releasing it.
    """
    stated = storage.field_writers(db_id, field, user_id=user_id)
    offers = field_writers_to_dict(db_id, stated, {})["fields"]
    return [
        row["writer"]
        for offer in offers
        for row in offer["writers"]
        if row["band"] != WriterBand.MANUAL.value
    ]
