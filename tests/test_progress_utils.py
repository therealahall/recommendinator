import logging

import pytest

from src.utils.progress import log_progress, should_log_progress
from src.utils.text import LINE_BREAKS


class TestShouldLogProgress:
    def test_interval_items_logged(self) -> None:
        assert should_log_progress(10, 100) is True
        assert should_log_progress(20, 100) is True
        assert should_log_progress(50, 100) is True

    def test_non_interval_items_skipped(self) -> None:
        assert should_log_progress(6, 100) is False
        assert should_log_progress(7, 100) is False
        assert should_log_progress(11, 100) is False
        assert should_log_progress(99, 100) is False

    def test_last_item_always_logged(self) -> None:
        assert should_log_progress(100, 100) is True
        assert should_log_progress(37, 37) is True


class TestLogProgress:
    def test_emits_message_at_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "game details", 10, 100)

        assert len(caplog.records) == 1
        assert "game details" in caplog.records[0].message
        assert "10/100" in caplog.records[0].message

    def test_skips_non_interval(self, caplog: pytest.LogCaptureFixture) -> None:
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, "game details", 7, 100)

        assert len(caplog.records) == 0


class TestTheLabelCannotForgeAnEntry:
    """The label is a caller's f-string, so an item title reaches it."""

    @pytest.mark.parametrize("breaker", [LINE_BREAKS[0], "\0"])
    def test_no_line_break_survives_the_label(
        self, caplog: pytest.LogCaptureFixture, breaker: str
    ) -> None:
        test_logger = logging.getLogger("test.progress")
        with caplog.at_level(logging.INFO, logger="test.progress"):
            log_progress(test_logger, f"a{breaker}INFO forged", 1, 3)

        assert breaker not in caplog.records[0].message
        assert "INFO forged" in caplog.records[0].message
