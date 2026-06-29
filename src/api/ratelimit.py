"""In-process sliding-window rate limiter for the RelayOps API (v2.1).

A small, dependency-free limiter so a single client cannot flood the turn
endpoint. Keyed per caller (resolved customer, or "anon" when unauthenticated)
with a sliding window of recent hit timestamps. This is a single-process guard
suitable for the prototype; a real deployment would use a shared store (Redis)
with the same window semantics.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

RATE_LIMIT = int(os.environ.get("RELAYOPS_RATE_LIMIT", "60"))  # requests per window
RATE_WINDOW = float(os.environ.get("RELAYOPS_RATE_WINDOW", "60"))  # seconds


class RateLimiter:
    """Sliding-window limiter. ``allow(key)`` records a hit and returns whether
    the caller is under the limit; thread-safe for FastAPI's worker threads."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, dq: deque[float], now: float) -> None:
        cutoff = now - self.window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            self._trim(dq, moment)
            if len(dq) >= self.limit:
                return False
            dq.append(moment)
            return True

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        """Whole seconds until the oldest hit in the window expires (>= 1)."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return 0
            self._trim(dq, moment)
            if len(dq) < self.limit:
                return 0
            return max(1, int(self.window - (moment - dq[0]) + 0.999))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
