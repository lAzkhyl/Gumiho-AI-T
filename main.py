from __future__ import annotations

import sys
import platform

if platform.system() != "Windows":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass

import asyncio
import logging
import pathlib

import discord
from discord.ext import commands
import structlog

from config import load_settings, Settings


# ═══════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════

def setup_logging(debug: bool = False) -> None:
    log_level = logging.DEBUG if debug else logging.INFO
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger("twomoon.main")


# ═══════════════════════════════════════════════
# CONNECTION POOLS
# ═══════════════════════════════════════════════

class ConnectionPools:
    def __init__(self) -> None:
        self.db = None
        self.redis = None

    async def initialize(self, settings: Settings) -> None:
        import asyncpg
        import redis.asyncio as aioredis

        try:
            ssl_context = "require" if settings.database.ssl else None
            self.db = await asyncpg.create_pool(
                dsn=settings.database.url,
                min_size=settings.database.pool_min_size,
                max_size=settings.database.pool_max_size,
                command_timeout=settings.database.command_timeout,
                ssl=ssl_context,
            )
            logger.info("database_pool_ready", pool_size=settings.database.pool_max_size)
        except Exception as error:
            logger.error("database_pool_failed", error=str(error))
            raise SystemExit(f"[FATAL] Database connection failed: {error}") from error

        try:
            self.redis = aioredis.from_url(
                settings.redis.url,
                max_connections=settings.redis.max_connections,
                decode_responses=True,
            )
            await self.redis.ping()
            logger.info("redis_pool_ready")
        except Exception as error:
            logger.error("redis_pool_failed", error=str(error))
            raise SystemExit(f"[FATAL] Redis connection failed: {error}") from error

    async def close(self) -> None:
        if self.db:
            await self.db.close()
            logger.info("database_pool_closed")
        if self.redis:
            await self.redis.aclose()
            logger.info("redis_pool_closed")


# ═══════════════════════════════════════════════
# BOT CLASS
# ═══════════════════════════════════════════════

class TwoMoonBot(commands.Bot):
    def __init__(self, settings: Settings, pools: ConnectionPools) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )

        self.settings = settings
        self.pools = pools
        self.local_router = None

    async def setup_hook(self) -> None:
        # ═══ Schema Bootstrap ═══
        from database.connection import bootstrap_schema
        await bootstrap_schema(self.pools.db)
        logger.info("schema_bootstrapped")

        # ═══ Local Router (The Gatekeeper) ═══
        from core.router import LocalRouter
        self.local_router = LocalRouter(self.settings.router)
        await self.local_router.initialize()
        logger.info("local_router_ready")

        # ═══ Load Cogs ═══
        cogs_dir = pathlib.Path(__file__).parent / "cogs"
        loaded = 0
        for cog_file in sorted(cogs_dir.glob("*.py")):
            if cog_file.name.startswith("_"):
                continue
            extension = f"cogs.{cog_file.stem}"
            try:
                await self.load_extension(extension)
                logger.info("cog_loaded", extension=extension)
                loaded += 1
            except commands.ExtensionError as error:
                logger.error("cog_load_failed", extension=extension, error=str(error))

        logger.info("cog_loading_complete", total=loaded)

        # ═══ Sync Slash Commands ═══
        guild = discord.Object(id=self.settings.discord.allowed_server_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("slash_commands_synced", guild_id=self.settings.discord.allowed_server_id)

    async def on_ready(self) -> None:
        logger.info(
            "bot_online",
            user=str(self.user),
            guild_target=self.settings.discord.allowed_server_id,
            latency_ms=round(self.latency * 1000, 1),
        )


# ═══════════════════════════════════════════════
# BOOT SEQUENCE
# ═══════════════════════════════════════════════

async def main() -> None:
    try:
        settings = load_settings()
    except Exception as error:
        print(f"[FATAL] Config validation failed:\n{error}")
        sys.exit(1)

    setup_logging(debug=settings.debug)
    logger.info("config_loaded", environment=settings.environment)

    pools = ConnectionPools()
    await pools.initialize(settings)

    bot = TwoMoonBot(settings=settings, pools=pools)

    try:
        async with bot:
            await bot.start(settings.discord.token)
    except KeyboardInterrupt:
        logger.info("shutdown_requested")
    finally:
        await pools.close()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())