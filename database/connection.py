from __future__ import annotations

import asyncpg
import structlog

logger = structlog.get_logger("twomoon.database")


# ═══════════════════════════════════════════════
# SCHEMA
# ═══════════════════════════════════════════════

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id           TEXT PRIMARY KEY,
        display_name      TEXT NOT NULL,
        preferred_lang    TEXT DEFAULT 'en',
        sentiment_avg     FLOAT DEFAULT 0.0,
        interaction_count INT DEFAULT 0,
        topics            TEXT[],
        last_interaction  TIMESTAMPTZ DEFAULT now(),
        created_at        TIMESTAMPTZ DEFAULT now()
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS bot_memories (
        id         SERIAL PRIMARY KEY,
        user_id    TEXT NOT NULL,
        topic      TEXT NOT NULL,
        content    TEXT NOT NULL,
        importance INT DEFAULT 5,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_memories_user ON bot_memories (user_id, importance DESC)",

    """
    CREATE TABLE IF NOT EXISTS conversation_log (
        id         SERIAL PRIMARY KEY,
        channel_id TEXT NOT NULL,
        message_id TEXT UNIQUE NOT NULL,
        user_id    TEXT NOT NULL,
        content    TEXT NOT NULL,
        embedding  FLOAT4[] DEFAULT NULL,
        sentiment  FLOAT DEFAULT 0.0,
        is_bot     BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,

    "CREATE INDEX IF NOT EXISTS idx_convlog_channel ON conversation_log (channel_id, created_at DESC)",

    """
    CREATE TABLE IF NOT EXISTS server_personas (
        server_id       TEXT PRIMARY KEY,
        preset          TEXT DEFAULT 'twomoon',
        quirk_intensity TEXT DEFAULT 'heavy',
        updated_at      TIMESTAMPTZ DEFAULT now()
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS user_personas (
        user_id         TEXT PRIMARY KEY,
        preset          TEXT,
        quirk_intensity TEXT,
        style_sample    TEXT,
        updated_at      TIMESTAMPTZ DEFAULT now()
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS lurker_state (
        channel_id   TEXT PRIMARY KEY,
        last_lurk_at TIMESTAMPTZ DEFAULT now()
    )
    """,
]

CLEANUP_SQL = {
    "conversation_log": "DELETE FROM conversation_log WHERE created_at < now() - INTERVAL '{days} days'",
    "bot_memories": "DELETE FROM bot_memories WHERE created_at < now() - INTERVAL '30 days' AND importance < 7",
}


# ═══════════════════════════════════════════════
# BOOTSTRAP
# ═══════════════════════════════════════════════

async def bootstrap_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for statement in SCHEMA_SQL:
            try:
                await conn.execute(statement)
            except Exception as error:
                logger.error("schema_exec_failed", statement=statement[:80], error=str(error))


async def cleanup_old_data(pool: asyncpg.Pool, days: int = 7) -> None:
    async with pool.acquire() as conn:
        for table, sql in CLEANUP_SQL.items():
            try:
                result = await conn.execute(sql.format(days=days))
                logger.info("cleanup_done", table=table, result=result)
            except Exception as error:
                logger.error("cleanup_failed", table=table, error=str(error))


# ═══════════════════════════════════════════════
# QUERY HELPERS
# ═══════════════════════════════════════════════

async def execute(pool: asyncpg.Pool, query: str, *args) -> str:
    try:
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)
    except Exception as error:
        logger.error("query_execute_failed", error=str(error))
        return ""


async def fetch(pool: asyncpg.Pool, query: str, *args) -> list[asyncpg.Record]:
    try:
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)
    except Exception as error:
        logger.error("query_fetch_failed", error=str(error))
        return []


async def fetchrow(pool: asyncpg.Pool, query: str, *args) -> asyncpg.Record | None:
    try:
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)
    except Exception as error:
        logger.error("query_fetchrow_failed", error=str(error))
        return None


async def fetchval(pool: asyncpg.Pool, query: str, *args):
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    except Exception as error:
        logger.error("query_fetchval_failed", error=str(error))
        return None