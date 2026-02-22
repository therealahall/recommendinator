"""Selective progress logging for long-running operations."""

import logging


def should_log_progress(
    current: int,
    total: int,
    *,
    initial_count: int = 5,
    interval: int = 10,
) -> bool:
    """Decide whether to emit a progress log for the given index.

    Logs the first *initial_count* items, every *interval*-th item after
    that, and always the last item.  This keeps output informative without
    flooding the log on large collections.

    Args:
        current: 1-based index of the item being processed.
        total: Total number of items.
        initial_count: How many items at the start to log unconditionally.
        interval: Log every N-th item after the initial batch.

    Returns:
        True if a progress message should be emitted.
    """
    if current <= initial_count:
        return True
    if current == total:
        return True
    if current % interval == 0:
        return True
    return False


def log_progress(
    logger: logging.Logger,
    label: str,
    current: int,
    total: int,
    *,
    initial_count: int = 5,
    interval: int = 10,
) -> None:
    """Log a progress message if the current index warrants it.

    Emits messages like ``Processing game details: 10/150 (6%)``.

    Args:
        logger: Logger instance to write to.
        label: Human-readable description of the operation
               (e.g. ``"game details"``).
        current: 1-based index of the item being processed.
        total: Total number of items.
        initial_count: How many items at the start to log unconditionally.
        interval: Log every N-th item after the initial batch.
    """
    if should_log_progress(
        current, total, initial_count=initial_count, interval=interval
    ):
        percent = current * 100 // total
        logger.info("Processing %s: %d/%d (%d%%)", label, current, total, percent)
