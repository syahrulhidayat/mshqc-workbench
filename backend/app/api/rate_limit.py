from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


async def check_rate_limit(
    redis_client: Any,
    *,
    client_key: str,
    limit: int,
    window_s: int,
) -> RateLimitResult:
    if limit <= 0 or window_s <= 0:
        return RateLimitResult(allowed=True, limit=0, remaining=0, retry_after_s=0)

    redis_key = f"mshqc:ratelimit:{client_key}"
    count = int(await redis_client.incr(redis_key))
    if count == 1:
        await redis_client.expire(redis_key, window_s)
        ttl = window_s
    else:
        raw_ttl = int(await redis_client.ttl(redis_key))
        ttl = raw_ttl if raw_ttl > 0 else window_s

    remaining = max(0, limit - count)
    allowed = count <= limit
    return RateLimitResult(
        allowed=allowed,
        limit=limit,
        remaining=remaining,
        retry_after_s=0 if allowed else ttl,
    )
