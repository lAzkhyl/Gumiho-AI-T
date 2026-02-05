from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tiktoken
import structlog

from config import ContextConfig
from database import memory_store, user_store
from services import redis_client as rc

if TYPE_CHECKING:
    import asyncpg
    import discord
    from redis.asyncio import Redis
    from core.router import LocalRouter

logger = structlog.get_logger("twomoon.context")

_ENCODER: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_tokens(text: str) -> int:
    return len(_get_encoder().encode(text, disallowed_special=()))


# ═══════════════════════════════════════════════
# CONTEXT DATA
# ═══════════════════════════════════════════════

@dataclass
class MessageFragment:
    msg_id: str
    author: str
    author_id: str
    content: str
    is_bot: bool
    timestamp: float = 0.0


@dataclass
class AssembledContext:
    reply_chain: list[MessageFragment] = field(default_factory=list)
    semantic_results: list[dict] = field(default_factory=list)
    channel_history: list[MessageFragment] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    user_profile: dict | None = None
    language: str = "en"
    active_users: dict[str, str] = field(default_factory=dict)


# ═══════════════════════════════════════════════
# CONTEXT MANAGER
# ═══════════════════════════════════════════════

class ContextManager:
    def __init__(
        self,
        config: ContextConfig,
        db_pool: asyncpg.Pool,
        redis: Redis,
        router: LocalRouter | None = None,
    ) -> None:
        self._config = config
        self._db = db_pool
        self._redis = redis
        self._router = router

    async def build(self, message: discord.Message) -> AssembledContext:
        start_time = time.perf_counter()
        ctx = AssembledContext()

        # 1. Trace Reply Chain (Highest Priority Context)
        ctx.reply_chain = await self._trace_reply_chain(message)

        # 2. Get Recent Channel History (Immediate Context)
        ctx.channel_history = await self._get_channel_history(message.channel, str(message.id))

        # 3. Recall Explicit Memories (Facts about user)
        ctx.memories = await memory_store.recall_memory(
            self._db, message.author.id, message.content, limit=3,
        )

        # 4. Semantic Retrieval (Long-term Context)
        raw_semantic = await self._semantic_retrieval(
            message.channel.id, message.content,
        )

        seen_ids = {msg.msg_id for msg in ctx.channel_history}
        seen_ids.update(msg.msg_id for msg in ctx.reply_chain)
        seen_ids.add(str(message.id))

        unique_semantic = []
        for res in raw_semantic:
            res_id = str(res.get("message_id") or res.get("id"))

            if res_id not in seen_ids:
                unique_semantic.append(res)

        unique_semantic.sort(key=lambda x: x.get("timestamp", 0))

        ctx.semantic_results = unique_semantic

        logger.info(
            "context_built",
            history=len(ctx.channel_history),
            semantic_raw=len(raw_semantic),
            semantic_deduped=len(ctx.semantic_results),
            time_ms=round((time.perf_counter() - start_time) * 1000, 2)
        )

        # 5. Build User Profile & Active Users
        ctx.user_profile = await user_store.get_user(self._db, str(message.author.id))
        ctx.language = ctx.user_profile["preferred_lang"] if ctx.user_profile else "en"

        for msg in ctx.channel_history:
            if not msg.is_bot:
                ctx.active_users[msg.author_id] = msg.author

        return ctx

    def format(
        self,
        context: AssembledContext,
        message: discord.Message,
        token_budget: int = 2500,
    ) -> str:
        sections: list[tuple[str, str, int]] = []
        # (label, content, priority) — lower priority number = trimmed first

        # ─── Memories (priority 2 — trim second) ───
        if context.memories:
            mem_text = "\n".join(f"- {m[:100]}" for m in context.memories)
            sections.append(("[MEMORIES]", mem_text, 2))

        # ─── Semantic Recall (priority 3 — trim third) ───
        if context.semantic_results:
            sem_lines = []
            for r in context.semantic_results:
                role = "Bot" if r["is_bot"] else f"user_{str(r['user_id'])[:6]}"
                sem_lines.append(f"{role}: {r['content'][:150]}")
            sem_text = "\n".join(sem_lines)
            sections.append(("[SEMANTIC RECALL]", sem_text, 3))

        # ─── Reply Chain (priority 5 — NEVER trim) ───
        if context.reply_chain:
            chain_lines = []
            for msg in reversed(context.reply_chain):
                role = "Bot" if msg.is_bot else msg.author
                chain_lines.append(f"{role}: {msg.content[:200]}")
            chain_text = "\n".join(chain_lines)
            sections.append(("[REPLY CONTEXT]", chain_text, 5))

        # ─── Channel History (priority 1 — trim first) ───
        if context.channel_history:
            recent = context.channel_history[-10:]
            hist_lines = []
            for msg in recent:
                role = "Bot" if msg.is_bot else msg.author
                hist_lines.append(f"{role}: {msg.content[:150]}")
            hist_text = "\n".join(hist_lines)
            sections.append(("[RECENT CHAT]", hist_text, 1))

        # ─── Current Message (priority 5 — NEVER trim) ───
        current_text = f"{message.author.display_name}: {message.content}"
        sections.append(("[CURRENT]", current_text, 5))

        # ─── Token Budget Enforcement ───
        sections.sort(key=lambda s: s[2], reverse=True)

        final_parts = []
        used_tokens = 0

        for label, content, priority in sections:
            block = f"{label}\n{content}"
            block_tokens = count_tokens(block)

            if used_tokens + block_tokens > token_budget and priority < 5:
                remaining = token_budget - used_tokens
                if remaining > 50:
                    trimmed = self._trim_to_tokens(block, remaining)
                    final_parts.append(trimmed)
                    used_tokens += count_tokens(trimmed)
                continue

            final_parts.append(block)
            used_tokens += block_tokens

        return "\n\n".join(final_parts)

    def build_mention_map(self, context: AssembledContext) -> str:
        if not context.active_users:
            return ""
        lines = [f"{name} = <@{uid}>" for uid, name in context.active_users.items()]
        return "[USER LIST]\n" + "\n".join(lines)

    # ═══════════════════════════════════════════
    # REPLY CHAIN
    # ═══════════════════════════════════════════

    async def _trace_reply_chain(
        self, message: discord.Message,
    ) -> list[MessageFragment]:
        chain = []
        current = message
        depth = 0
        max_depth = self._config.reply_chain_depth

        while current.reference and current.reference.message_id and depth < max_depth:
            try:
                if current.reference.cached_message:
                    parent = current.reference.cached_message
                else:
                    parent = await current.channel.fetch_message(current.reference.message_id)
                
                chain.append(MessageFragment(
                    msg_id=str(parent.id),
                    author=parent.author.display_name,
                    author_id=str(parent.author.id),
                    content=parent.content,
                    is_bot=parent.author.bot,
                    timestamp=parent.created_at.timestamp(),
                ))
                current = parent
                depth += 1
            except Exception:
                break

        return chain

    # ═══════════════════════════════════════════
    # CHANNEL HISTORY (Redis-cached)
    # ═══════════════════════════════════════════

    async def _get_channel_history(
        self,
        channel: discord.TextChannel,
        exclude_id: str,
    ) -> list[MessageFragment]:
        cached = await rc.get_context(self._redis, str(channel.id))
        if cached:
            return [
                MessageFragment(
                    msg_id=m.get("id", ""),
                    author=m["author"],
                    author_id=m["author_id"],
                    content=m["content"],
                    is_bot=m["is_bot"],
                    timestamp=m.get("timestamp", 0),
                )
                for m in cached
                if m.get("id") != exclude_id
            ]

        try:
            messages = [message async for message in channel.history(limit=self._config.history_limit)]
        except Exception as error:
            logger.error("history_fetch_failed", error=str(error))
            return []

        history = []
        cache_data = []

        for m in reversed(messages):
            if str(m.id) == exclude_id:
                continue
            
            if not m.content.strip():
                continue

            frag = MessageFragment(
                msg_id=str(m.id),
                author=m.author.display_name,
                author_id=str(m.author.id),
                content=m.content,
                is_bot=m.author.bot,
                timestamp=m.created_at.timestamp(),
            )
            history.append(frag)
            cache_data.append({
                "id": str(m.id),
                "author": m.author.display_name,
                "author_id": str(m.author.id),
                "content": m.content,
                "is_bot": m.author.bot,
                "timestamp": m.created_at.timestamp(),
            })

        await rc.set_context(self._redis, str(channel.id), cache_data, ttl_seconds=120)
        return history

    # ═══════════════════════════════════════════
    # SEMANTIC RETRIEVAL (Vector Search)
    # ═══════════════════════════════════════════

    async def _semantic_retrieval(
        self,
        channel_id: str | int,
        content: str,
    ) -> list[dict]:
        if not self._router or not content or len(content) < 10:
            return []

        try:
            embedding = self._router.get_embedding(content)
            if embedding is None:
                return []

            results = await memory_store.semantic_search(
                pool=self._db,
                channel_id=str(channel_id),
                query_embedding=embedding,
                window_hours=self._config.semantic_retrieval_window_hours,
                limit=self._config.semantic_retrieval_limit,
            )
            return results
        except Exception as error:
            logger.error("semantic_retrieval_failed", error=str(error))
            return []

    # ═══════════════════════════════════════════
    # TOKEN TRIMMING
    # ═══════════════════════════════════════════

    @staticmethod
    def _trim_to_tokens(text: str, max_tokens: int) -> str:
        encoder = _get_encoder()
        tokens = encoder.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text
        trimmed_tokens = tokens[:max_tokens]
        return encoder.decode(trimmed_tokens)