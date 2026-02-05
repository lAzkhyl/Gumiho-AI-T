from __future__ import annotations

from typing import Any

import orjson
import structlog
from redis.asyncio import Redis

from config import Settings

logger = structlog.get_logger("twomoon.redis")

RATE_LIMIT_PREFIX = "rl:"
CONTEXT_PREFIX = "ctx:"
CIRCUIT_PREFIX = "cb:"
LURKER_PREFIX = "lurk:"


# ═══════════════════════════════════════════════
# RATE LIMITING (Sliding Window)
# ═══════════════════════════════════════════════

async def check_rate_limit(
    redis: Redis,
    user_id: str,
    max_requests: int,
    window_seconds: int,
) -> dict[str, Any]:
    key = f"{RATE_LIMIT_PREFIX}{user_id}"
    try:
        current = await redis.get(key)
        if current is None:
            await redis.setex(key, window_seconds, 1)
            return {"allowed": True, "remaining": max_requests - 1}

        count = int(current)
        if count >= max_requests:
            ttl = await redis.ttl(key)
            return {"allowed": False, "reset_in": max(ttl, 1)}

        await redis.incr(key)
        return {"allowed": True, "remaining": max_requests - count - 1}
    except Exception as error:
        logger.error("rate_limit_check_failed", error=str(error))
        return {"allowed": True, "remaining": max_requests}


# ═══════════════════════════════════════════════
# CONTEXT CACHE (Channel History)
# ═══════════════════════════════════════════════

async def get_context(redis: Redis, channel_id: str) -> list[dict] | None:
    key = f"{CONTEXT_PREFIX}{channel_id}"
    try:
        data = await redis.get(key)
        if data is None:
            return None
        return orjson.loads(data)
    except Exception as error:
        logger.error("context_get_failed", error=str(error))
        return None


async def set_context(
    redis: Redis,
    channel_id: str,
    messages: list[dict],
    ttl_seconds: int = 300,
) -> None:
    key = f"{CONTEXT_PREFIX}{channel_id}"
    try:
        await redis.setex(key, ttl_seconds, orjson.dumps(messages))
    except Exception as error:
        logger.error("context_set_failed", error=str(error))


# ═══════════════════════════════════════════════
# CIRCUIT BREAKER STATE
# ═══════════════════════════════════════════════

async def get_circuit_state(redis: Redis, provider: str) -> str:
    key = f"{CIRCUIT_PREFIX}{provider}"
    try:
        state = await redis.get(key)
        return state or "closed"
    except Exception as error:
        logger.error("circuit_get_failed", error=str(error))
        return "closed"


async def set_circuit_state(
    redis: Redis,
    provider: str,
    state: str,
    ttl_seconds: int = 60,
) -> None:
    key = f"{CIRCUIT_PREFIX}{provider}"
    try:
        await redis.setex(key, ttl_seconds, state)
    except Exception as error:
        logger.error("circuit_set_failed", error=str(error))


async def increment_circuit_failures(redis: Redis, provider: str) -> int:
    key = f"{CIRCUIT_PREFIX}{provider}:failures"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 120)
        return count
    except Exception as error:
        logger.error("circuit_incr_failed", error=str(error))
        return 0


async def reset_circuit_failures(redis: Redis, provider: str) -> None:
    key = f"{CIRCUIT_PREFIX}{provider}:failures"
    try:
        await redis.delete(key)
    except Exception as error:
        logger.error("circuit_reset_failed", error=str(error))


# ═══════════════════════════════════════════════
# LURKER COOLDOWN
# ═══════════════════════════════════════════════

async def check_lurker_cooldown(
    redis: Redis,
    channel_id: str,
    cooldown_seconds: int,
) -> bool:
    key = f"{LURKER_PREFIX}{channel_id}"
    try:
        exists = await redis.exists(key)
        return not exists
    except Exception as error:
        logger.error("lurker_cooldown_check_failed", error=str(error))
        return False


async def set_lurker_cooldown(
    redis: Redis,
    channel_id: str,
    cooldown_seconds: int,
) -> None:
    key = f"{LURKER_PREFIX}{channel_id}"
    try:
        await redis.setex(key, cooldown_seconds, "1")
    except Exception as error:
        logger.error("lurker_cooldown_set_failed", error=str(error))


# ═══════════════════════════════════════════════
# GENERIC CACHE
# ═══════════════════════════════════════════════

async def cache_get(redis: Redis, key: str) -> Any | None:
    try:
        data = await redis.get(key)
        if data is None:
            return None
        return orjson.loads(data)
    except Exception:
        return None


async def cache_set(redis: Redis, key: str, value: Any, ttl_seconds: int = 600) -> None:
    try:
        await redis.setex(key, ttl_seconds, orjson.dumps(value))
    except Exception:
        pass