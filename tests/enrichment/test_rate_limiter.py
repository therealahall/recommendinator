from unittest.mock import patch

from src.enrichment.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_try_acquire_exhausted(self) -> None:
        limiter = RateLimiter(requests_per_second=10.0, burst_size=2)

        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    def test_acquire_with_timeout_failure(self) -> None:
        limiter = RateLimiter(requests_per_second=1.0, burst_size=1)

        limiter.acquire()
        result = limiter.acquire(timeout=0.01)

        assert result is False

    def test_token_refill(self) -> None:
        fake_time = [0.0]
        with patch("src.enrichment.rate_limiter.time") as mock_time:
            mock_time.monotonic = lambda: fake_time[0]

            limiter = RateLimiter(requests_per_second=100.0, burst_size=5)

            for _ in range(5):
                limiter.try_acquire()

            assert limiter.available_tokens < 1.0

            fake_time[0] = 0.05

            assert limiter.available_tokens >= 4.0

    def test_tokens_cap_at_burst_size(self) -> None:
        fake_time = [0.0]
        with patch("src.enrichment.rate_limiter.time") as mock_time:
            mock_time.monotonic = lambda: fake_time[0]

            limiter = RateLimiter(requests_per_second=100.0, burst_size=5)

            fake_time[0] = 0.1

            assert limiter.available_tokens == 5.0
