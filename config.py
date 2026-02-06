from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ═══════════════════════════════════════════════
# SUB-CONFIGS
# ═══════════════════════════════════════════════

class DiscordConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DISCORD_")

    token: str = Field(...)
    allowed_server_id: int = Field(default=1452886736874111009)
    lurker_channel_ids: list[int] = Field(default_factory=lambda: [1452886738497310824])
    ignored_channel_ids: list[int] = Field(default_factory=list)

    @field_validator("lurker_channel_ids", "ignored_channel_ids", mode="before")
    @classmethod
    def parse_comma_separated_ids(cls, value: str | list) -> list[int]:
        if isinstance(value, str):
            return [int(chunk.strip()) for chunk in value.split(",") if chunk.strip()]
        return value


class GroqConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GROQ_")

    api_key: str = Field(...)
    model_fast: str = Field(default="llama-3.1-8b-instant")
    model_smart: str = Field(default="llama-3.3-70b-versatile")
    max_tokens: int = Field(default=150)
    temperature: float = Field(default=0.85, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=10.0)


class OpenRouterConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENROUTER_")

    api_key: str = Field(default="")
    base_url: str = Field(default="https://openrouter.ai/api/v1")
    default_model: str = Field(default="meta-llama/llama-3.1-8b-instruct")
    vision_model: str = Field(default="meta-llama/llama-4-scout:free")
    vision_fallback_model: str = Field(default="google/gemini-2.0-flash-001")
    timeout_seconds: float = Field(default=15.0)


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_")

    url: str = Field(
        default="postgresql://twomoon_admin@localhost:26257/twomoon?sslmode=disable"
    )
    pool_min_size: int = Field(default=2)
    pool_max_size: int = Field(default=10)
    command_timeout: float = Field(default=30.0)
    ssl: bool = Field(default=False)


class RedisConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")
    url: str = Field(...) 
    
    max_connections: int = Field(default=20)


class CircuitBreakerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CB_")

    fail_max: int = Field(default=5)
    reset_timeout_seconds: int = Field(default=30)


class RateLimitConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RATELIMIT_")

    max_requests: int = Field(default=20)
    window_seconds: int = Field(default=60)


class LurkerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LURKER_")

    min_interest_score: int = Field(default=85)
    cooldown_seconds: int = Field(default=600)
    base_chance: float = Field(default=0.03)


class ContextConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CTX_")

    history_limit: int = Field(default=15)
    reply_chain_depth: int = Field(default=5)
    max_memory_tokens: int = Field(default=800)
    semantic_retrieval_limit: int = Field(default=3)
    semantic_retrieval_window_hours: int = Field(default=24)


class RouterConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROUTER_")

    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5")
    similarity_threshold: float = Field(default=0.5)
    chitchat_response_cache_size: int = Field(default=50)


# ═══════════════════════════════════════════════
# MASTER CONFIG
# ═══════════════════════════════════════════════

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    lurker: LurkerConfig = Field(default_factory=LurkerConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)

    debug: bool = Field(default=False)
    environment: str = Field(default="development")


def load_settings() -> Settings:
    return Settings()