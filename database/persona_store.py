from __future__ import annotations

import asyncpg
import structlog

from database.connection import execute, fetchrow
from services import redis_client as rc

logger = structlog.get_logger("twomoon.persona_store")

CACHE_TTL = 600

VALID_PRESETS = {"twomoon", "homie", "mentor", "chaos", "professional", "matchuser"}
VALID_QUIRKS = {"light", "medium", "heavy"}


# ═══════════════════════════════════════════════
# SERVER PERSONA
# ═══════════════════════════════════════════════

async def get_server_persona(
    pool: asyncpg.Pool,
    redis,
    server_id: str,
) -> dict:
    cached = await rc.cache_get(redis, f"sp:{server_id}")
    if cached:
        return cached

    row = await fetchrow(
        pool,
        "SELECT preset, quirk_intensity FROM server_personas WHERE server_id = $1",
        server_id,
    )

    data = {
        "preset": row["preset"] if row else "twomoon",
        "quirk_intensity": row["quirk_intensity"] if row else "heavy",
    }
    await rc.cache_set(redis, f"sp:{server_id}", data, CACHE_TTL)
    return data


async def set_server_persona(
    pool: asyncpg.Pool,
    redis,
    server_id: str,
    preset: str,
    quirk_intensity: str = "heavy",
) -> None:
    await execute(
        pool,
        """INSERT INTO server_personas (server_id, preset, quirk_intensity, updated_at)
           VALUES ($1, $2, $3, now())
           ON CONFLICT (server_id) DO UPDATE SET
             preset = $2,
             quirk_intensity = $3,
             updated_at = now()""",
        server_id, preset, quirk_intensity,
    )
    await rc.cache_set(redis, f"sp:{server_id}", None, 1)


# ═══════════════════════════════════════════════
# USER PERSONA
# ═══════════════════════════════════════════════

async def get_user_persona(
    pool: asyncpg.Pool,
    redis,
    user_id: str,
) -> dict | None:
    cached = await rc.cache_get(redis, f"up:{user_id}")
    if cached:
        return cached

    row = await fetchrow(
        pool,
        "SELECT preset, quirk_intensity, style_sample FROM user_personas WHERE user_id = $1",
        user_id,
    )

    if not row:
        return None

    data = {
        "preset": row["preset"],
        "quirk_intensity": row["quirk_intensity"],
        "style_sample": row["style_sample"],
    }
    await rc.cache_set(redis, f"up:{user_id}", data, CACHE_TTL)
    return data


async def set_user_persona(
    pool: asyncpg.Pool,
    redis,
    user_id: str,
    preset: str | None = None,
    quirk_intensity: str | None = None,
    style_sample: str | None = None,
) -> None:
    await execute(
        pool,
        """INSERT INTO user_personas (user_id, preset, quirk_intensity, style_sample, updated_at)
           VALUES ($1, $2, $3, $4, now())
           ON CONFLICT (user_id) DO UPDATE SET
             preset = COALESCE($2, user_personas.preset),
             quirk_intensity = COALESCE($3, user_personas.quirk_intensity),
             style_sample = COALESCE($4, user_personas.style_sample),
             updated_at = now()""",
        user_id, preset, quirk_intensity, style_sample,
    )
    await rc.cache_set(redis, f"up:{user_id}", None, 1)


async def reset_user_persona(
    pool: asyncpg.Pool,
    redis,
    user_id: str,
) -> None:
    await execute(pool, "DELETE FROM user_personas WHERE user_id = $1", user_id)
    await rc.cache_set(redis, f"up:{user_id}", None, 1)


# ═══════════════════════════════════════════════
# EFFECTIVE PERSONA (Resolution Chain)
# ═══════════════════════════════════════════════

async def get_effective_persona(
    pool: asyncpg.Pool,
    redis,
    user_id: str,
    server_id: str,
) -> dict:
    user_p = await get_user_persona(pool, redis, user_id)
    server_p = await get_server_persona(pool, redis, server_id)

    if user_p and user_p.get("preset"):
        return {
            "source": "user",
            "preset": user_p["preset"],
            "quirk_intensity": user_p.get("quirk_intensity") or server_p.get("quirk_intensity", "heavy"),
            "style_sample": user_p.get("style_sample"),
        }

    return {
        "source": "server",
        "preset": server_p.get("preset", "twomoon"),
        "quirk_intensity": server_p.get("quirk_intensity", "heavy"),
        "style_sample": None,
    }