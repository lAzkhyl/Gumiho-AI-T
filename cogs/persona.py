from __future__ import annotations

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from core.persona_engine import get_emoji, get_description
from database import persona_store

logger = structlog.get_logger("twomoon.cog.persona")


# ═══════════════════════════════════════════════
# CHOICES
# ═══════════════════════════════════════════════

PRESET_CHOICES = [
    app_commands.Choice(name="🌙 Two Moon — Calm & balanced", value="twomoon"),
    app_commands.Choice(name="😎 Homie — Chill & friendly", value="homie"),
    app_commands.Choice(name="🧙 Mentor — Wise & thoughtful", value="mentor"),
    app_commands.Choice(name="🔥 Chaos — Savage & unhinged", value="chaos"),
    app_commands.Choice(name="💼 Professional — Formal", value="professional"),
    app_commands.Choice(name="🪞 Match Me — Mirrors your style", value="matchuser"),
]

QUIRK_CHOICES = [
    app_commands.Choice(name="Light — Minimal quirks", value="light"),
    app_commands.Choice(name="Medium — Balanced", value="medium"),
    app_commands.Choice(name="Heavy — Very human-like", value="heavy"),
]

SERVER_PRESET_CHOICES = [c for c in PRESET_CHOICES if c.value != "matchuser"]


# ═══════════════════════════════════════════════
# PERSONA COG
# ═══════════════════════════════════════════════

class PersonaCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ═══ USER: /persona ═══

    persona_group = app_commands.Group(name="persona", description="Set how Gumiho talks to you")

    @persona_group.command(name="set", description="Choose a personality preset")
    @app_commands.describe(preset="Pick a personality")
    @app_commands.choices(preset=PRESET_CHOICES)
    async def persona_set(self, interaction: discord.Interaction, preset: str) -> None:
        await persona_store.set_user_persona(
            self.bot.pools.db,
            self.bot.pools.redis,
            str(interaction.user.id),
            preset=preset,
        )
        emoji = get_emoji(preset)
        desc = get_description(preset)
        await interaction.response.send_message(
            f"{emoji} **Persona set: {preset}**\n{desc}",
            ephemeral=True,
        )

    @persona_group.command(name="view", description="View your current persona")
    async def persona_view(self, interaction: discord.Interaction) -> None:
        effective = await persona_store.get_effective_persona(
            self.bot.pools.db,
            self.bot.pools.redis,
            str(interaction.user.id),
            str(interaction.guild_id),
        )
        emoji = get_emoji(effective["preset"])
        source = effective["source"]
        quirks = effective["quirk_intensity"]
        await interaction.response.send_message(
            f"{emoji} **Your persona: {effective['preset']}**\n"
            f"Source: {source} | Quirks: {quirks}",
            ephemeral=True,
        )

    @persona_group.command(name="reset", description="Reset to server default")
    async def persona_reset(self, interaction: discord.Interaction) -> None:
        await persona_store.reset_user_persona(
            self.bot.pools.db,
            self.bot.pools.redis,
            str(interaction.user.id),
        )
        await interaction.response.send_message("✓ Reset to server default", ephemeral=True)

    # ═══ ADMIN: /server-persona ═══

    server_persona_group = app_commands.Group(
        name="server-persona",
        description="Server-wide persona settings (Admin only)",
        default_permissions=discord.Permissions(administrator=True),
    )

    @server_persona_group.command(name="set", description="Set server default preset")
    @app_commands.describe(preset="Server default personality", quirks="How human-like")
    @app_commands.choices(preset=SERVER_PRESET_CHOICES, quirks=QUIRK_CHOICES)
    async def server_set(
        self,
        interaction: discord.Interaction,
        preset: str,
        quirks: str = "heavy",
    ) -> None:
        await persona_store.set_server_persona(
            self.bot.pools.db,
            self.bot.pools.redis,
            str(interaction.guild_id),
            preset,
            quirks,
        )
        emoji = get_emoji(preset)
        await interaction.response.send_message(
            f"{emoji} **Server default: {preset}**\nQuirk intensity: {quirks}",
        )

    @server_persona_group.command(name="view", description="View server persona settings")
    async def server_view(self, interaction: discord.Interaction) -> None:
        data = await persona_store.get_server_persona(
            self.bot.pools.db,
            self.bot.pools.redis,
            str(interaction.guild_id),
        )
        emoji = get_emoji(data["preset"])
        await interaction.response.send_message(
            f"{emoji} **Server default: {data['preset']}**\n"
            f"Quirks: {data['quirk_intensity']}",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonaCog(bot))