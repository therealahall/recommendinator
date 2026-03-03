"""Tests for the rate limiter."""

import threading
import time
from unittest.mock import patch

import pytest

from src.enrichment.rate_limiter import RateLimiter


class TestRateLimiter:
    """Tests for the RateLimiter class."""

    def test_create_rate_limiter(self) -> None:
        """Test creating a rate limiter with default settings."""
        limiter = RateLimiter(requests_per_second=10.0)

        assert limiter.requests_per_second == 10.0
        assert limiter.burst_size == 10
        assert limiter.available_tokens == 10.0

    def test_create_rate_limiter_with_burst(self) -> None:
        """Test creating a rate limiter with custom burst size."""
        limiter = RateLimiter(requests_per_second=5.0, burst_size=20)

        assert limiter.requests_per_second == 5.0
        assert limiter.burst_size == 20
        assert limiter.available_tokens == 20.0

    def test_create_rate_limiter_small_rate(self) -> None:
        """Test creating a rate limiter with rate less than 1."""
        limiter = RateLimiter(requests_per_second=0.5)

        assert limiter.requests_per_second == 0.5
        assert limiter.burst_size == 1  # min(1, int(0.5)) = 1
        assert limiter.available_tokens == 1.0

    def test_invalid_rate_raises(self) -> None:
        """Test that invalid rate raises ValueError."""
        with pytest.raises(ValueError, match="requests_per_second must be positive"):
            RateLimiter(requests_per_second=0)

        with pytest.raises(ValueError, match="requests_per_second must be positive"):
            RateLimiter(requests_per_second=-1)

    def test_try_acquire_success(self) -> None:
        """Test non-blocking acquire when tokens available."""
        limiter = RateLimiter(requests_per_second=10.0)

        assert limiter.try_acquire() is True
        assert limiter.available_tokens < 10.0

    def test_try_acquire_exhausted(self) -> None:
        """Test non-blocking acquire when no tokens available."""
        limiter = RateLimiter(requests_per_second=10.0, burst_size=2)

        # Exhaust tokens
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_acquire_blocking(self) -> None:
        """Test that acquire blocks until token available."""
        limiter = RateLimiter(requests_per_second=100.0, burst_size=1)

        # First acquire should be instant
        limiter.acquire()

        # Second acquire should block briefly
        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start

        # Should have waited approximately 0.01 seconds (1/100)
        assert elapsed >= 0.005  # Allow some margin
        assert elapsed < 0.1  # But not too long

    def test_acquire_with_timeout_success(self) -> None:
        """Test acquire with sufficient timeout."""
        limiter = RateLimiter(requests_per_second=100.0, burst_size=1)

        limiter.acquire()  # Use the one token
        result = limiter.acquire(timeout=0.5)

        assert result is True

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

    def test_reset(self) -> None:
        """Test resetting the rate limiter."""
        limiter = RateLimiter(requests_per_second=10.0, burst_size=5)

        # Use some tokens
        limiter.try_acquire()
        limiter.try_acquire()

        assert limiter.available_tokens < 5.0

        # Reset
        limiter.reset()

        assert limiter.available_tokens == 5.0

    def test_thread_safety(self) -> None:
        """Test that rate limiter is thread-safe."""
        limiter = RateLimiter(requests_per_second=1000.0, burst_size=100)
        acquired_count = 0
        lock = threading.Lock()

        def acquire_tokens() -> None:
            nonlocal acquired_count
            for _ in range(10):
                if limiter.try_acquire():
                    with lock:
                        acquired_count += 1

        # Start multiple threads
        threads = [threading.Thread(target=acquire_tokens) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # All tokens should be acquired (burst_size=100, 10 threads x 10 = 100)
        assert acquired_count == 100

    def test_fractional_tokens(self) -> None:
        """Test that fractional tokens accumulate correctly."""
        fake_time = [0.0]
        with patch("src.enrichment.rate_limiter.time") as mock_time:
            mock_time.monotonic = lambda: fake_time[0]

            limiter = RateLimiter(requests_per_second=10.0, burst_size=1)

            # Use the token
            limiter.try_acquire()

            # Advance 0.05s — ~0.5 tokens at 10/s, not enough
            fake_time[0] = 0.05
            assert limiter.try_acquire() is False

            # Advance to 0.11s total — ~1.1 tokens at 10/s
            fake_time[0] = 0.11
            assert limiter.try_acquire() is True
