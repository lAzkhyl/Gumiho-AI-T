from __future__ import annotations

from typing import Any

import asyncpg
import structlog

from database.connection import execute, fetch, fetchrow

logger = structlog.get_logger("twomoon.user_store")


# ═══════════════════════════════════════════════
# USER PROFILES
# ═══════════════════════════════════════════════

async def get_user(pool: asyncpg.Pool, user_id: str) -> dict | None:
    row = await fetchrow(
        pool,
        "SELECT * FROM user_profiles WHERE user_id = $1",
        user_id,
    )
    return dict(row) if row else None


async def upsert_user(
    pool: asyncpg.Pool,
    user_id: str,
    display_name: str,
    preferred_lang: str | None = None,
) -> dict | None:
    existing = await get_user(pool, user_id)

    if existing:
        await execute(
            pool,
            """UPDATE user_profiles SET
                display_name = $2,
                preferred_lang = COALESCE($3, preferred_lang),
                interaction_count = interaction_count + 1,
                last_interaction = now()
               WHERE user_id = $1""",
            user_id, display_name, preferred_lang,
        )
    else:
        await execute(
            pool,
            """INSERT INTO user_profiles (user_id, display_name, preferred_lang)
               VALUES ($1, $2, COALESCE($3, 'en'))""",
            user_id, display_name, preferred_lang,
        )

    return await get_user(pool, user_id)


# ═══════════════════════════════════════════════
# SENTIMENT
# ═══════════════════════════════════════════════

async def update_sentiment(
    pool: asyncpg.Pool,
    user_id: str,
    sentiment: float,
) -> None:
    await execute(
        pool,
        """UPDATE user_profiles SET
            sentiment_avg = (sentiment_avg * 0.8) + ($2 * 0.2)
           WHERE user_id = $1""",
        user_id, sentiment,
    )


async def get_sentiment(pool: asyncpg.Pool, user_id: str) -> float:
    row = await fetchrow(
        pool,
        "SELECT sentiment_avg FROM user_profiles WHERE user_id = $1",
        user_id,
    )
    return row["sentiment_avg"] if row else 0.0


# ═══════════════════════════════════════════════
# TOPICS
# ═══════════════════════════════════════════════

async def update_topics(
    pool: asyncpg.Pool,
    user_id: str,
    topics: list[str],
) -> None:
    if not topics:
        return
    await execute(
        pool,
        "UPDATE user_profiles SET topics = $2 WHERE user_id = $1",
        user_id, topics,
    )


# ═══════════════════════════════════════════════
# LANGUAGE
# ═══════════════════════════════════════════════

async def get_language(pool: asyncpg.Pool, user_id: str) -> str:
    row = await fetchrow(
        pool,
        "SELECT preferred_lang FROM user_profiles WHERE user_id = $1",
        user_id,
    )
    return row["preferred_lang"] if row else "en"


# ═══════════════════════════════════════════════
# STATS (for /stats command)
# ═══════════════════════════════════════════════

async def get_top_users(
    pool: asyncpg.Pool,
    limit: int = 10,
) -> list[dict]:
    rows = await fetch(
        pool,
        """SELECT user_id, display_name, interaction_count, sentiment_avg
           FROM user_profiles
           ORDER BY interaction_count DESC
           LIMIT $1""",
        limit,
    )
    return [dict(row) for row in rows]


async def get_total_users(pool: asyncpg.Pool) -> int:
    row = await fetchrow(pool, "SELECT COUNT(*) as total FROM user_profiles")
    return row["total"] if row else 0