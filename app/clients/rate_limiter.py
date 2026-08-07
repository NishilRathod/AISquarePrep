import asyncio
import time
from collections.abc import Callable


class AsyncTokenBucket:
    """A simple async token bucket used to throttle outbound calls to a rate-limited API.

    A single shared instance paces all callers (including concurrent ones) to
    stay within `rate_per_minute` on aggregate.
    """

    def __init__(self, rate_per_minute: int, *, clock: Callable[[], float] = time.monotonic):
        self._capacity = float(rate_per_minute)
        self._tokens = float(rate_per_minute)
        self._refill_per_sec = rate_per_minute / 60.0
        self._clock = clock
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            elapsed = now - self._last
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)
            self._last = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self._refill_per_sec
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._last = self._clock()
            else:
                self._tokens -= 1
