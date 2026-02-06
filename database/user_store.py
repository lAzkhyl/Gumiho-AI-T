from __future__ import annotations

from typing import Any

import asyncpg
import structlog

from database.connection import execute, fetch, fetchrow

logger = structlog.get_logger("twomoon.user_store")


# ═══════════════════════════════════════════════
# USER PROFILES
# ═══════════════════════════════════════════════

async def get_user(pool: asyncpg.Pool, user_id: str | int) -> dict | None:
    uid_str = str(user_id)
    
    row = await fetchrow(
        pool,
        "SELECT * FROM user_profiles WHERE user_id = $1",
        uid_str,
    )
    return dict(row) if row else None


async def upsert_user(
    pool: asyncpg.Pool,
    user_id: str | int,
    display_name: str,
    preferred_lang: str | None = None,
) -> dict | None:
    uid_str = str(user_id)
    
    existing = await get_user(pool, uid_str)

    if existing:
        await execute(
            pool,
            """UPDATE user_profiles SET
                display_name = $2,
                preferred_lang = COALESCE($3, preferred_lang),
                interaction_count = interaction_count + 1,
                last_interaction = now()
               WHERE user_id = $1""",
            uid_str, display_name, preferred_lang,
        )
    else:
        await execute(
            pool,
            """INSERT INTO user_profiles (user_id, display_name, preferred_lang)
               VALUES ($1, $2, COALESCE($3, 'en'))""",
            uid_str, display_name, preferred_lang,
        )

    return await get_user(pool, uid_str)


# ═══════════════════════════════════════════════
# SENTIMENT
# ═══════════════════════════════════════════════

async def update_sentiment(
    pool: asyncpg.Pool,
    user_id: str | int,
    sentiment: float,
) -> None:
    uid_str = str(user_id)
    
    await execute(
        pool,
        """UPDATE user_profiles SET
            sentiment_avg = (sentiment_avg * 0.8) + ($2 * 0.2)
           WHERE user_id = $1""",
        uid_str, sentiment,
    )


async def get_sentiment(pool: asyncpg.Pool, user_id: str | int) -> float:
    uid_str = str(user_id)
    
    row = await fetchrow(
        pool,
        "SELECT sentiment_avg FROM user_profiles WHERE user_id = $1",
        uid_str,
    )
    return row["sentiment_avg"] if row else 0.0


# ═══════════════════════════════════════════════
# TOPICS
# ═══════════════════════════════════════════════

async def update_topics(
    pool: asyncpg.Pool,
    user_id: str | int,
    topics: list[str],
) -> None:
    if not topics:
        return
        
    uid_str = str(user_id)
    
    await execute(
        pool,
        "UPDATE user_profiles SET topics = $2 WHERE user_id = $1",
        uid_str, topics,
    )


# ═══════════════════════════════════════════════
# LANGUAGE
# ═══════════════════════════════════════════════

async def get_language(pool: asyncpg.Pool, user_id: str | int) -> str:
    uid_str = str(user_id) # FIX
    
    row = await fetchrow(
        pool,
        "SELECT preferred_lang FROM user_profiles WHERE user_id = $1",
        uid_str,
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
