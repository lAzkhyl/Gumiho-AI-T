from __future__ import annotations

import re

import asyncpg
import structlog

from database.connection import execute, fetch, fetchrow

logger = structlog.get_logger("twomoon.memory_store")


# ═══════════════════════════════════════════════
# TOPIC DETECTION
# ═══════════════════════════════════════════════

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "gaming": [
        "game", "play", "steam", "valorant", "minecraft", "roblox",
        "rank", "match", "gg", "noob", "grind", "clutch", "nerf",
        "buff", "meta", "carry", "genshin", "mobile legends", "ml",
    ],
    "personal": [
        "feel", "sad", "happy", "love", "hate", "friend", "family",
        "girlfriend", "boyfriend", "crush", "relationship", "life",
        "lonely", "stress", "anxious", "tired", "bored",
    ],
    "work": [
        "work", "job", "boss", "project", "deadline", "meeting",
        "office", "salary", "interview", "resign", "client",
    ],
    "hobby": [
        "music", "movie", "anime", "art", "draw", "cook", "gym",
        "sport", "book", "manga", "cosplay", "photography",
    ],
    "tech": [
        "code", "programming", "python", "javascript", "server",
        "api", "bug", "error", "deploy", "database", "linux",
    ],
    "food": [
        "food", "eat", "hungry", "lunch", "dinner", "breakfast",
        "pizza", "burger", "nasi", "makan", "lapar", "masak",
    ],
}

_IMPORTANCE_WORDS = re.compile(
    r"\b(always|never|hate|love|important|serious|favorite|worst|best)\b",
    re.IGNORECASE,
)


def detect_topics(content: str) -> list[str]:
    lower = content.lower()
    found = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            found.append(topic)
    return found


def calculate_importance(content: str, sentiment: float) -> int:
    score = 5
    if len(content) > 100:
        score += 2
    if abs(sentiment) > 0.5:
        score += 2
    if _IMPORTANCE_WORDS.search(content):
        score += 1
    return min(score, 10)


# ═══════════════════════════════════════════════
# BOT MEMORIES (Long-term, topic-based)
# ═══════════════════════════════════════════════

async def save_memory(
    pool: asyncpg.Pool,
    user_id: str,
    content: str,
    sentiment: float = 0.0,
) -> None:
    topics = detect_topics(content)
    if not topics or len(content) < 20:
        return

    importance = calculate_importance(content, sentiment)
    truncated = content[:500]

    for topic in topics:
        await execute(
            pool,
            """INSERT INTO bot_memories (user_id, topic, content, importance)
               VALUES ($1, $2, $3, $4)""",
            user_id, topic, truncated, importance,
        )


async def recall_memory(
    pool: asyncpg.Pool,
    user_id: str,
    current_content: str,
    limit: int = 3,
) -> list[str]:
    topics = detect_topics(current_content)

    if topics:
        rows = await fetch(
            pool,
            """SELECT content FROM bot_memories
               WHERE user_id = $1 AND topic = ANY($2)
               ORDER BY importance DESC, created_at DESC
               LIMIT $3""",
            user_id, topics, limit,
        )
    else:
        rows = await fetch(
            pool,
            """SELECT content FROM bot_memories
               WHERE user_id = $1
               ORDER BY importance DESC, created_at DESC
               LIMIT $2""",
            user_id, limit,
        )

    return [row["content"] for row in rows]


async def get_user_top_topics(
    pool: asyncpg.Pool,
    user_id: str,
    limit: int = 5,
) -> list[str]:
    rows = await fetch(
        pool,
        """SELECT topic, COUNT(*) as cnt FROM bot_memories
           WHERE user_id = $1
           GROUP BY topic
           ORDER BY cnt DESC
           LIMIT $2""",
        user_id, limit,
    )
    return [row["topic"] for row in rows]


# ═══════════════════════════════════════════════
# CONVERSATION LOG (with embedding for semantic retrieval)
# ═══════════════════════════════════════════════

async def save_conversation(
    pool: asyncpg.Pool,
    channel_id: str,
    message_id: str,
    user_id: str,
    content: str,
    embedding: list[float] | None = None,
    sentiment: float = 0.0,
    is_bot: bool = False,
) -> None:
    await execute(
        pool,
        """INSERT INTO conversation_log
               (channel_id, message_id, user_id, content, embedding, sentiment, is_bot)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (message_id) DO NOTHING""",
        channel_id, message_id, user_id, content[:2000],
        embedding, sentiment, is_bot,
    )


async def semantic_search(
    pool: asyncpg.Pool,
    channel_id: str,
    query_embedding: list[float],
    window_hours: int = 24,
    limit: int = 3,
) -> list[dict]:
    # CRITICAL: CockroachDB uses array-based cosine similarity
    # since native VECTOR type support varies by version.
    # We compute cosine similarity manually via SQL.
    rows = await fetch(
        pool,
        """
        WITH candidates AS (
            SELECT id, user_id, content, is_bot, created_at, embedding
            FROM conversation_log
            WHERE channel_id = $1
              AND embedding IS NOT NULL
              AND created_at > now() - make_interval(hours => $3)
        )
        SELECT
            user_id,
            content,
            is_bot,
            created_at,
            (
                SELECT SUM(a * b)
                FROM UNNEST(embedding) WITH ORDINALITY AS e(a, ord)
                JOIN UNNEST($2::FLOAT4[]) WITH ORDINALITY AS q(b, ord)
                ON e.ord = q.ord
            ) / (
                NULLIF(
                    SQRT((SELECT SUM(a * a) FROM UNNEST(embedding) AS a)) *
                    SQRT((SELECT SUM(b * b) FROM UNNEST($2::FLOAT4[]) AS b)),
                    0
                )
            ) AS similarity
        FROM candidates
        WHERE embedding IS NOT NULL
        ORDER BY similarity DESC NULLS LAST
        LIMIT $4
        """,
        channel_id, query_embedding, window_hours, limit,
    )

    return [
        {
            "user_id": row["user_id"],
            "content": row["content"],
            "is_bot": row["is_bot"],
            "created_at": row["created_at"],
            "similarity": row["similarity"] or 0.0,
        }
        for row in rows
        if row["similarity"] and row["similarity"] > 0.3
    ]


async def get_recent_messages(
    pool: asyncpg.Pool,
    channel_id: str,
    limit: int = 15,
) -> list[dict]:
    rows = await fetch(
        pool,
        """SELECT user_id, content, is_bot, created_at
           FROM conversation_log
           WHERE channel_id = $1
           ORDER BY created_at DESC
           LIMIT $2""",
        channel_id, limit,
    )

    return [
        {
            "user_id": row["user_id"],
            "content": row["content"],
            "is_bot": row["is_bot"],
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]