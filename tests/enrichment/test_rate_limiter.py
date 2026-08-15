"""Tests for the rate limiter."""

from unittest.mock import patch

from src.enrichment.rate_limiter import RateLimiter


class TestRateLimiter:
    """Tests for the RateLimiter class."""

    def test_try_acquire_exhausted(self) -> None:
        """Test non-blocking acquire when no tokens available."""
        limiter = RateLimiter(requests_per_second=10.0, burst_size=2)

        # Exhaust tokens
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_acquire_with_timeout_failure(self) -> None:
        """Test acquire with insufficient timeout."""
        limiter = RateLimiter(requests_per_second=1.0, burst_size=1)

        limiter.acquire()  # Use the one token
        result = limiter.acquire(timeout=0.01)

        # Should fail because we need to wait ~1 second for next token
        assert result is False

    def test_token_refill(self) -> None:
        """Test that tokens refill over time."""
        fake_time = [0.0]
        with patch("src.enrichment.rate_limiter.time") as mock_time:
            mock_time.monotonic = lambda: fake_time[0]

            limiter = RateLimiter(requests_per_second=100.0, burst_size=5)

            # Use all tokens
            for _ in range(5):
                limiter.try_acquire()

            assert limiter.available_tokens < 1.0

            # Advance clock by 0.05s — should refill 5 tokens at 100/s
            fake_time[0] = 0.05

            assert limiter.available_tokens >= 4.0

    def test_tokens_cap_at_burst_size(self) -> None:
        """Test that tokens don't exceed burst size."""
        fake_time = [0.0]
        with patch("src.enrichment.rate_limiter.time") as mock_time:
            mock_time.monotonic = lambda: fake_time[0]

            limiter = RateLimiter(requests_per_second=100.0, burst_size=5)

            # Advance clock well past burst refill
            fake_time[0] = 0.1

            # Tokens should still be capped at burst_size
            assert limiter.available_tokens == 5.0
