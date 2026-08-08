"""Concurrency cap shared by the SSE endpoints.

A stream holds one of anyio's 40 threadpool tokens per generator step, so
uncapped, streams left open take the lot and no endpoint answers at all.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterator
from threading import BoundedSemaphore, Lock

from fastapi import HTTPException

#: One person's browser opens a handful of streams, and the rest of the
#: threadpool stays free. Authentication is in front of this, so it guards a
#: stuck tab rather than a hostile caller.
MAX_CONCURRENT_STREAMS = 8

TOO_MANY_STREAMS_DETAIL = "Too many streams in progress. Try again in a moment."

_slots = BoundedSemaphore(MAX_CONCURRENT_STREAMS)


class _HeldSlot:
    """One acquired slot, given back once however the stream ends.

    Two paths reach it and either may be the only one, so a second call has
    to be a no-op: over-releasing a ``BoundedSemaphore`` raises.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._held = True

    def give_back(self) -> None:
        with self._lock:
            if self._held:
                self._held = False
                _slots.release()


def bounded_sse(events: Iterator[str]) -> Iterator[str]:
    """Hold a stream slot for as long as *events* runs, or answer 503.

    Deliberately not a generator: a generator body first runs once Starlette
    iterates it, after the 200 is on the wire and too late to refuse.
    """
    if not _slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail=TOO_MANY_STREAMS_DETAIL)
    slot = _HeldSlot()
    stream = _releasing_slot(events, slot)
    # Closing a generator that has not taken its first step runs no frame
    # code, so the finally below never fires — the window a client that
    # disconnects before the first chunk lands in. This is its only release.
    weakref.finalize(stream, slot.give_back)
    return stream


def _releasing_slot(events: Iterator[str], slot: _HeldSlot) -> Iterator[str]:
    """Yield *events*, giving the slot back however the stream ends."""
    try:
        yield from events
    finally:
        slot.give_back()
