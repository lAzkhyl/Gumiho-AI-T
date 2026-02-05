from __future__ import annotations

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from core.llm_gateway import LLMGateway
from database.connection import cleanup_old_data
from database import user_store

logger = structlog.get_logger("twomoon.cog.admin")


# ═══════════════════════════════════════════════
# ADMIN COG
# ═══════════════════════════════════════════════

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    admin_group = app_commands.Group(
        name="admin",
        description="Bot administration (Admin only)",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ═══ /admin status ═══

    @admin_group.command(name="status", description="Bot health and provider status")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        gateway = LLMGateway(settings=self.bot.settings, redis=self.bot.pools.redis)
        provider_status = await gateway.get_provider_status()

        db_ok = False
        try:
            async with self.bot.pools.db.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            pass

        redis_ok = False
        try:
            await self.bot.pools.redis.ping()
            redis_ok = True
        except Exception:
            pass

        router_ok = self.bot.local_router is not None and self.bot.local_router._ready

        groq_circuit = provider_status["groq"]["circuit"]
        openrouter_circuit = provider_status["openrouter"]["circuit"]

        status_icon = lambda ok: "🟢" if ok else "🔴"
        circuit_icon = lambda state: "🟢" if state == "closed" else "🟡" if state == "half-open" else "🔴"

        lines = [
            "**2M_Gumiho Status**",
            "",
            f"**Infrastructure**",
            f"{status_icon(db_ok)} Database (CockroachDB)",
            f"{status_icon(redis_ok)} Redis",
            f"{status_icon(router_ok)} Local Router (Gatekeeper)",
            "",
            f"**LLM Providers**",
            f"{circuit_icon(groq_circuit)} Groq — circuit: `{groq_circuit}`",
            f"{circuit_icon(openrouter_circuit)} OpenRouter — circuit: `{openrouter_circuit}`",
            "",
            f"**Gateway**",
            f"Latency: `{round(self.bot.latency * 1000, 1)}ms`",
            f"Guilds: `{len(self.bot.guilds)}`",
        ]

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ═══ /admin stats ═══

    @admin_group.command(name="stats", description="Usage statistics")
    async def stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        total_users = await user_store.get_total_users(self.bot.pools.db)
        top_users = await user_store.get_top_users(self.bot.pools.db, limit=5)

        lines = [
            "**2M_Gumiho Stats**",
            "",
            f"Total users: `{total_users}`",
            "",
            "**Top 5 Users**",
        ]

        for i, user in enumerate(top_users, 1):
            sentiment_emoji = "😊" if user["sentiment_avg"] > 0.1 else "😐" if user["sentiment_avg"] > -0.1 else "😤"
            lines.append(
                f"`{i}.` **{user['display_name']}** — "
                f"{user['interaction_count']} msgs {sentiment_emoji}"
            )

        if not top_users:
            lines.append("No data yet")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ═══ /admin cleanup ═══

    @admin_group.command(name="cleanup", description="Manual database cleanup")
    @app_commands.describe(days="Delete conversation logs older than N days")
    async def cleanup(self, interaction: discord.Interaction, days: int = 7) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            await cleanup_old_data(self.bot.pools.db, days=days)
            await interaction.followup.send(
                f"✓ Cleanup complete — removed data older than {days} days",
                ephemeral=True,
            )
        except Exception as error:
            logger.error("manual_cleanup_failed", error=str(error))
            await interaction.followup.send(
                f"Cleanup failed: {str(error)[:100]}",
                ephemeral=True,
            )


# ═══════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))