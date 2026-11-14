"""Fixed-window rate limiter, in memory.

Good for one instance. With several instances behind a load balancer, move the counters to Redis
(INCR + EXPIRE per window) or enforce limits at the gateway (API Gateway, ALB + WAF).
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, clock=time.monotonic, max_keys: int = 50_000):
        self._windows: dict[str, tuple[int, int]] = {}
        self._clock = clock
        self._max_keys = max_keys

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        """Count one request; True while still within the limit."""
        window = int(self._clock() // window_seconds)
        if len(self._windows) > self._max_keys:  # drop finished windows so memory stays bounded
            self._windows = {k: v for k, v in self._windows.items() if v[0] == window}
        start, count = self._windows.get(key, (window, 0))
        if start != window:
            start, count = window, 0
        count += 1
        self._windows[key] = (start, count)
        return count <= limit
