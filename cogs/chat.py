from __future__ import annotations

import asyncio

import discord
import structlog
from discord.ext import commands

from core.context_manager import ContextManager
from core.llm_gateway import LLMGateway
from core.persona_engine import build_system_prompt
from core.router import RouteType
from database import memory_store, user_store
from services import redis_client as rc
from utils.text import (
    clean_bot_mentions,
    detect_language,
    extract_image_url,
    has_any_image,
    sanitize,
)

logger = structlog.get_logger("twomoon.cog.chat")


# ═══════════════════════════════════════════════
# CHAT COG
# ═══════════════════════════════════════════════

class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._gateway: LLMGateway | None = None
        self._context_mgr: ContextManager | None = None

    def _ensure_services(self) -> bool:
        if self._gateway is None:
            self._gateway = LLMGateway(
                settings=self.bot.settings,
                redis=self.bot.pools.redis,
            )
        if self._context_mgr is None:
            self._context_mgr = ContextManager(
                config=self.bot.settings.context,
                db_pool=self.bot.pools.db,
                redis=self.bot.pools.redis,
                router=self.bot.local_router,
            )
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        guild_id = message.guild.id if message.guild else 0
        allowed_id = self.bot.settings.discord.allowed_server_id

        if message.guild and str(guild_id) != str(allowed_id):
            return
            
        if message.channel.id in self.bot.settings.discord.ignored_channel_ids:
            return

        is_mentioned = self.bot.user in message.mentions
        is_reply_to_bot = await self._is_reply_to_bot(message)

        if not is_mentioned and not is_reply_to_bot:
            return

        self._ensure_services()

        content = clean_bot_mentions(message.content, self.bot.user.id)
        content = sanitize(content)
        image_present = has_any_image(message)

        # ═══ GATEKEEPER ═══
        route_result = self.bot.local_router.classify(content, has_image=image_present)
        logger.debug("route_classified", route=route_result.route, confidence=route_result.confidence)

        if route_result.route == RouteType.IGNORE:
            if _should_quick_react():
                await _quick_react(message)
            return

        if route_result.route == RouteType.CHITCHAT and route_result.chitchat_response:
            delay = _typing_delay(route_result.chitchat_response)
            await asyncio.sleep(delay)
            await message.reply(route_result.chitchat_response, mention_author=False)
            return

        # ═══ RATE LIMIT ═══
        rate_check = await rc.check_rate_limit(
            self.bot.pools.redis,
            str(message.author.id),
            self.bot.settings.rate_limit.max_requests,
            self.bot.settings.rate_limit.window_seconds,
        )
        if not rate_check["allowed"]:
            await message.reply(f"sabar {rate_check['reset_in']}s", mention_author=False)
            return

        await message.channel.typing()

        try:
            # ═══ CONTEXT ═══
            context = await self._context_mgr.build(message)
            
            member = getattr(message, "member", None)
            display_name = member.display_name if member else message.author.display_name
            
            language = detect_language(content) or context.language
            nick = _extract_short_name(display_name)

            await user_store.upsert_user(
                self.bot.pools.db,
                str(message.author.id),
                display_name,
                language,
            )

            mention_map = self._context_mgr.build_mention_map(context)

            user_messages_for_style = None
            persona = await self._get_persona(str(message.author.id), str(guild_id))
            if persona["preset"] == "matchuser":
                user_messages_for_style = [
                    m.content for m in context.channel_history
                    if m.author_id == str(message.author.id) and m.content
                ]

            # ═══ SYSTEM PROMPT ═══
            system_prompt = await build_system_prompt(
                pool=self.bot.pools.db,
                redis=self.bot.pools.redis,
                user_id=str(message.author.id),
                server_id=str(guild_id),
                user_messages=user_messages_for_style,
                language=language,
                mention_map=mention_map,
                talking_to=nick,
            )

            user_context = self._context_mgr.format(context, message)

            # ═══ LLM GATEWAY ═══
            if route_result.is_vision:
                image_url = extract_image_url(message)
                if image_url:
                    response = await self._gateway.generate_vision(
                        system_prompt=system_prompt,
                        user_text=content or "react to this image naturally",
                        image_url=image_url,
                        max_tokens=100,
                    )
                    if response.success:
                        reply_text = response.content.strip()
                    else:
                        reply_text = ""
                    should_respond = bool(reply_text)
                else:
                    should_respond = False
                    reply_text = ""
            else:
                max_tokens = _decide_max_tokens(content)
                chat_response = await self._gateway.generate_chat(
                    system_prompt=system_prompt,
                    user_content=user_context,
                    max_tokens=max_tokens,
                )
                should_respond = chat_response.should_respond
                reply_text = chat_response.response_text

            if not should_respond or not reply_text:
                return

            # ═══ SEND ═══
            delay = _typing_delay(reply_text)
            await asyncio.sleep(delay)
            await message.reply(reply_text, mention_author=False)

            asyncio.create_task(self._post_process(message, content, reply_text))

        except Exception as error:
            logger.error("chat_pipeline_failed", error=str(error))
            try:
                await message.reply("my brain glitched sry", mention_author=False)
            except Exception:
                pass

    # ═══════════════════════════════════════════
    # POST-PROCESSING
    # ═══════════════════════════════════════════

    async def _post_process(
        self,
        message: discord.Message,
        content: str,
        reply_text: str,
    ) -> None:
        try:
            embedding = None
            if self.bot.local_router and len(content) >= 10:
                embedding = self.bot.local_router.get_embedding(content)

            await memory_store.save_conversation(
                pool=self.bot.pools.db,
                channel_id=str(message.channel.id),
                message_id=str(message.id),
                user_id=str(message.author.id),
                content=content,
                embedding=embedding,
                is_bot=False,
            )

            bot_embedding = None
            if self.bot.local_router and len(reply_text) >= 10:
                bot_embedding = self.bot.local_router.get_embedding(reply_text)

            await memory_store.save_conversation(
                pool=self.bot.pools.db,
                channel_id=str(message.channel.id),
                message_id=f"{message.id}_bot",
                user_id=str(self.bot.user.id),
                content=reply_text,
                embedding=bot_embedding,
                is_bot=True,
            )

            await memory_store.save_memory(
                self.bot.pools.db,
                str(message.author.id),
                content,
            )

        except Exception as error:
            logger.error("post_process_failed", error=str(error))

    # ═══════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════

    async def _is_reply_to_bot(self, message: discord.Message) -> bool:
        if not message.reference or not message.reference.message_id:
            return False
        try:
            ref = await message.channel.fetch_message(message.reference.message_id)
            return ref.author.id == self.bot.user.id
        except Exception:
            return False

    async def _get_persona(self, user_id: str, server_id: str) -> dict:
        from database import persona_store
        return await persona_store.get_effective_persona(
            self.bot.pools.db, self.bot.pools.redis, user_id, server_id,
        )


# ═══════════════════════════════════════════
# MODULE-LEVEL HELPERS
# ═══════════════════════════════════════════

import random


def _should_quick_react() -> bool:
    return random.random() < 0.10


async def _quick_react(message: discord.Message) -> None:
    reactions = ["👍", "💀", "😂", "🔥"]
    try:
        await message.add_reaction(random.choice(reactions))
    except Exception:
        pass


def _typing_delay(text: str) -> float:
    base = 0.3
    per_char = min(len(text) * 0.015, 1.5)
    jitter = random.uniform(0.1, 0.6)
    return min(base + per_char + jitter, 2.5)


def _decide_max_tokens(content: str) -> int:
    length = len(content)
    if length < 15:
        return 60
    if length < 60:
        return 100
    return 150


def _extract_short_name(display_name: str) -> str:
    prefixes = ["2M_", "TM_", "2m_", "tm_", "GD_", "gd_", "DD_", "dd_"]
    clean = display_name
    for prefix in prefixes:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    clean = clean.replace("_", " ").replace("-", " ").replace(".", " ").strip()
    if len(clean) <= 4:
        return clean
    parts = clean.split()
    if len(parts) > 1:
        return parts[0]
    return clean[:7] if len(clean) > 7 else clean


# ═══════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))
