import os

import redis
from fastapi import HTTPException, Request

_r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

def rate_limit(bucket: str, identifier: str, limit: int, window_seconds: int) -> None:
    """"Fixed-window limiter. Raises 429 once `identifier` exceeds `limit` hits per window."""
    key = f"rl:{bucket}:{identifier}"
    try:
        pipe = _r.pipeline()
        pipe.set(key, 0, ex=window_seconds, nx=True)
        pipe.incr(key)
        _, count = pipe.execute()
        
        if count > limit:
            retry_after = max(_r.ttl(key), 1)
            raise HTTPException(
                status_code=429,
                detail="TOO many attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
    except (redis.RedisError, TypeError) as e:
        print(f"RATE LIMIT CHECK FAILED: {e}")
        
