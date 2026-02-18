"""Tests for the selective progress logging utility."""

import logging

import pytest

from src.utils.progress import log_progress, should_log_progress


class TestShouldLogProgress:
    """Tests for should_log_progress()."""

    def test_first_items_always_logged(self) -> None:
        """Items 1 through initial_count are always logged."""
        for current in range(1, 6):
            assert should_log_progress(current, 100) is True

    def test_last_item_always_logged(self) -> None:
        """The last item is always logged regardless of position."""
        assert should_log_progress(100, 100) is True
        assert should_log_progress(37, 37) is True

    def test_interval_items_logged(self) -> None:
        """Every interval-th item is logged."""
        assert should_log_progress(10, 100) is True
        assert should_log_progress(20, 100) is True
        assert should_log_progress(50, 100) is True

    def test_non_interval_items_skipped(self) -> None:
        """Items not matching initial/interval/last are skipped."""
        assert should_log_progress(6, 100) is False
        assert should_log_progress(7, 100) is False
        assert should_log_progress(11, 100) is False
        assert should_log_progress(99, 100) is False

    def test_custom_initial_count(self) -> None:
        """Custom initial_count changes the initial logging window."""
        assert should_log_progress(3, 100, initial_count=3) is True
        assert should_log_progress(4, 100, initial_count=3) is False

    def test_custom_interval(self) -> None:
        """Custom interval changes the logging frequency."""
        assert should_log_progress(15, 100, interval=15) is True
        assert should_log_progress(10, 100, interval=15) is False

    def test_small_total(self) -> None:
        """When total <= initial_count, every item is logged."""
        for current in range(1, 4):
            assert should_log_progress(current, 3) is True


class TestLogProgress:
    """Tests for log_progress()."""

    def test_emits_message_at_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        """log_progress emits a formatted message when should_log_progress is True."""
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "game details", 10, 100)

        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Processing game details: 10/100 (10%)"

    def test_skips_non_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        """log_progress emits nothing when should_log_progress is False."""
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "game details", 7, 100)

        assert len(caplog.records) == 0

    def test_first_item(self, caplog: pytest.LogCaptureFixture) -> None:
        """First item is always logged."""
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "product details", 1, 50)

        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Processing product details: 1/50 (2%)"

    def test_last_item(self, caplog: pytest.LogCaptureFixture) -> None:
        """Last item is always logged."""
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "product details", 50, 50)

        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Processing product details: 50/50 (100%)"

    def test_percent_calculation(self, caplog: pytest.LogCaptureFixture) -> None:
        """Percentage is calculated as integer division."""
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "items", 1, 3)

        assert "(33%)" in caplog.records[0].message
