from __future__ import annotations

import asyncio
import random

import discord
import structlog
from discord.ext import commands

from core.llm_gateway import LLMGateway
from services import redis_client as rc

logger = structlog.get_logger("twomoon.cog.lurker")


# ═══════════════════════════════════════════════
# INTEREST SCORING
# ═══════════════════════════════════════════════

INTEREST_KEYWORDS: dict[str, dict] = {
    "gaming": {"words": ["valorant", "minecraft", "roblox", "game", "rank", "grind", "noob", "gg", "clutch", "apex", "ml"], "weight": 15},
    "drama": {"words": ["drama", "beef", "toxic", "cancel", "expose", "caught", "drama"], "weight": 20},
    "food": {"words": ["food", "eat", "hungry", "lunch", "dinner", "pizza", "makan", "lapar"], "weight": 10},
    "tech": {"words": ["code", "bug", "server", "api", "error", "python", "javascript", "deploy"], "weight": 12},
    "meme": {"words": ["meme", "funny", "lmao", "bruh moment", "based", "chad", "shitpost"], "weight": 8},
    "personal": {"words": ["girlfriend", "boyfriend", "crush", "love", "relationship", "dating", "pacar", "gebetan"], "weight": 18},
}


def calculate_interest(content: str, recent_message_count: int) -> tuple[int, str]:
    lower = content.lower()
    score = 0
    top_topic = "general"
    top_weight = 0

    for topic, data in INTEREST_KEYWORDS.items():
        matches = sum(1 for w in data["words"] if w in lower)
        topic_score = matches * data["weight"]
        score += topic_score
        if topic_score > top_weight:
            top_weight = topic_score
            top_topic = topic

    if len(content) > 50:
        score += 5
    if len(content) > 100:
        score += 5

    if any(c * 2 in content for c in "!?"):
        score += 8

    if recent_message_count > 5:
        score += 10

    return min(score, 100), top_topic


# ═══════════════════════════════════════════════
# LURKER COG
# ═══════════════════════════════════════════════

class LurkerCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._gateway: LLMGateway | None = None
        self._recent_messages: dict[int, list[float]] = {}

    def _ensure_gateway(self) -> None:
        if self._gateway is None:
            self._gateway = LLMGateway(
                settings=self.bot.settings,
                redis=self.bot.pools.redis,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not message.guild or str(message.guild.id) != str(self.bot.settings.discord.allowed_server_id):
            return
        if message.channel.id not in self.bot.settings.discord.lurker_channel_ids:
            return
        if self.bot.user in message.mentions:
            return

        self._track_activity(message.channel.id)
        recent_count = self._get_recent_count(message.channel.id)
        interest, topic = calculate_interest(message.content, recent_count)

        if interest < self.bot.settings.lurker.min_interest_score:
            return

        can_lurk = await rc.check_lurker_cooldown(
            self.bot.pools.redis,
            str(message.channel.id),
            self.bot.settings.lurker.cooldown_seconds,
        )
        if not can_lurk:
            return

        chance = self.bot.settings.lurker.base_chance + (interest - self.bot.settings.lurker.min_interest_score) * 0.01
        if random.random() > chance:
            return

        self._ensure_gateway()

        try:
            lurk_prompt = (
                "You're Gumiho (2M_Gumiho), lurking in Discord. "
                "You just read something interesting and want to chime in.\n"
                f"Topic: {topic}\n"
                "RULES:\n"
                "- Jump in naturally, like a friend suddenly commenting\n"
                "- VERY SHORT (max 12 words)\n"
                "- Don't pretend to know stuff you don't\n"
                "- Can be random, funny, or absurd\n"
                "- Don't ask questions, just comment or react\n"
                "- Lowercase, casual"
            )

            response = await self._gateway.generate_text(
                system_prompt=lurk_prompt,
                user_content=f'Someone said: "{message.content[:200]}"',
                max_tokens=40,
                temperature=0.95,
            )

            if not response.success or not response.content.strip():
                return

            reply_text = response.content.strip().lower()

            delay = random.uniform(1.0, 3.0)
            await asyncio.sleep(delay)
            await message.channel.send(reply_text)

            await rc.set_lurker_cooldown(
                self.bot.pools.redis,
                str(message.channel.id),
                self.bot.settings.lurker.cooldown_seconds,
            )

            logger.info(
                "lurk_triggered",
                channel=message.channel.id,
                interest=interest,
                topic=topic,
            )

        except Exception as error:
            logger.error("lurk_failed", error=str(error))

    # ═══════════════════════════════════════════
    # ACTIVITY TRACKING (in-memory, lightweight)
    # ═══════════════════════════════════════════

    def _track_activity(self, channel_id: int) -> None:
        import time
        now = time.time()
        if channel_id not in self._recent_messages:
            self._recent_messages[channel_id] = []
        timestamps = self._recent_messages[channel_id]
        timestamps.append(now)
        self._recent_messages[channel_id] = [t for t in timestamps if now - t < 60]

    def _get_recent_count(self, channel_id: int) -> int:
        return len(self._recent_messages.get(channel_id, []))


# ═══════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LurkerCog(bot))