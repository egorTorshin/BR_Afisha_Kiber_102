import time
from collections import defaultdict, deque
from typing import Deque, Dict


class RateLimiter:
    """Ограничение частоты запросов от одного пользователя (Раздел 10 ТЗ)."""

    def __init__(self, max_requests_per_minute: int = 30) -> None:
        self.max_requests = max(1, max_requests_per_minute)
        self.window_seconds = 60.0
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, user_key: str) -> bool:
        now = time.monotonic()
        q = self._buckets[str(user_key)]
        while q and (now - q[0]) > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True
