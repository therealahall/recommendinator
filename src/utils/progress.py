import logging

from src.utils.text import sanitize_for_log


def should_log_progress(
    current: int,
    total: int,
    *,
    initial_count: int = 5,
    interval: int = 10,
) -> bool:
    """This keeps output informative without flooding the log on large collections."""
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
    if should_log_progress(
        current, total, initial_count=initial_count, interval=interval
    ):
        percent = current * 100 // total
        logger.info(
            "Processing %s: %d/%d (%d%%)",
            sanitize_for_log(label),
            current,
            total,
            percent,
        )
