"""Token bucket rate limiter for API calls."""

import threading
import time


class RateLimiter:
    """Thread-safe token bucket rate limiter.

    Implements a token bucket algorithm to limit the rate of API calls.
    Tokens are added at a fixed rate, and each operation consumes one token.
    If no tokens are available, the caller blocks until one becomes available.

    Example usage:
        # Limit to 10 requests per second
        limiter = RateLimiter(requests_per_second=10.0)

        # Each call blocks if necessary to respect the rate limit
        for item in items:
            limiter.acquire()  # Blocks if rate exceeded
            make_api_call(item)

    Thread safety:
        All methods are thread-safe. Multiple threads can safely call
        acquire() concurrently.
    """

    def __init__(
        self,
        requests_per_second: float = 1.0,
        burst_size: int | None = None,
    ) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_second: Maximum sustained request rate.
            burst_size: Maximum burst size (tokens). Defaults to
                max(1, int(requests_per_second)) for small bursts.
        """
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")

        self.requests_per_second = requests_per_second
        self.burst_size = burst_size or max(1, int(requests_per_second))

        # Time between token additions
        self._interval = 1.0 / requests_per_second

        # Current tokens available
        self._tokens = float(self.burst_size)

        # Last time tokens were updated
        self._last_update = time.monotonic()

        # Lock for thread safety
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Acquire a token, blocking if necessary.

        Blocks until a token is available or timeout expires.

        Args:
            timeout: Maximum time to wait in seconds. None means wait forever.

        Returns:
            True if a token was acquired, False if timeout expired.
        """
        deadline = None
        if timeout is not None:
            deadline = time.monotonic() + timeout

        while True:
            with self._lock:
                self._refill_tokens()

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

                # Calculate time until next token
                wait_time = self._interval * (1.0 - self._tokens)

            # Check if we would exceed the deadline
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

            # Sleep outside the lock
            time.sleep(wait_time)

    def try_acquire(self) -> bool:
        """Try to acquire a token without blocking.

        Returns:
            True if a token was acquired, False if no tokens available.
        """
        with self._lock:
            self._refill_tokens()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def _refill_tokens(self) -> None:
        """Refill tokens based on elapsed time.

        Must be called with lock held.
        """
        now = time.monotonic()
        elapsed = now - self._last_update
        self._last_update = now

        # Add tokens based on elapsed time
        new_tokens = elapsed * self.requests_per_second
        self._tokens = min(self._tokens + new_tokens, float(self.burst_size))

    @property
    def available_tokens(self) -> float:
        """Get the current number of available tokens.

        Returns:
            Number of tokens currently available (may be fractional).
        """
        with self._lock:
            self._refill_tokens()
            return self._tokens

    def reset(self) -> None:
        """Reset the rate limiter to full capacity.

        Useful for testing or after a period of inactivity.
        """
        with self._lock:
            self._tokens = float(self.burst_size)
            self._last_update = time.monotonic()
