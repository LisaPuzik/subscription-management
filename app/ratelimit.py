import time
from collections import defaultdict, deque

from fastapi import Request

from app.config import get_settings
from app.errors import AppError


class RateLimiter:
    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, key: tuple[str, str], limit: int, window: int) -> None:
        now = time.monotonic()
        hits = self.hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            raise AppError(429, "RATE_LIMIT_EXCEEDED", "Too many requests")
        hits.append(now)

    def reset(self) -> None:
        self.hits.clear()


limiter = RateLimiter()


async def rate_limit(request: Request) -> None:
    settings = get_settings()
    client = request.client.host if request.client else "unknown"
    limiter.check((client, request.url.path), settings.rate_limit_requests, settings.rate_limit_window_seconds)
