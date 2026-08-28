import threading
import time


class RateLimiter:
    def __init__(
        self,
        requests_per_second: float = 1.0,
        burst_size: int | None = None,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")

        self.requests_per_second = requests_per_second
        self.burst_size = burst_size or max(1, int(requests_per_second))

        self._interval = 1.0 / requests_per_second
        self._tokens = float(self.burst_size)
        self._last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Blocks until a token is available or timeout expires."""
        deadline = None
        if timeout is not None:
            deadline = time.monotonic() + timeout

        while True:
            with self._lock:
                self._refill_tokens()

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

                wait_time = self._interval * (1.0 - self._tokens)

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

            # Sleep outside the lock
            time.sleep(wait_time)

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill_tokens()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def _refill_tokens(self) -> None:
        """Must be called with lock held."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._last_update = now

        new_tokens = elapsed * self.requests_per_second
        self._tokens = min(self._tokens + new_tokens, float(self.burst_size))

    @property
    def available_tokens(self) -> float:
        with self._lock:
            self._refill_tokens()
            return self._tokens

    def reset(self) -> None:
        with self._lock:
            self._tokens = float(self.burst_size)
            self._last_update = time.monotonic()
