from __future__ import annotations

from typing import TypeVar

import instructor
import structlog
from groq import AsyncGroq
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from config import Settings
from services.groq_client import GroqClient, LLMResponse
from services.openrouter_client import OpenRouterClient
from services import redis_client as rc

logger = structlog.get_logger("twomoon.gateway")

T = TypeVar("T", bound=BaseModel)


# ═══════════════════════════════════════════════
# STRUCTURED RESPONSE MODELS
# ═══════════════════════════════════════════════

class ChatResponse(BaseModel):
    should_respond: bool = Field(
        description="false if the message doesn't warrant a response (like 'ok', 'lol', emoji-only)"
    )
    response_text: str = Field(
        default="",
        description="the actual reply text, empty if should_respond is false"
    )
    mood: str = Field(
        default="neutral",
        description="detected mood: positive, negative, neutral, playful, aggressive"
    )


# ═══════════════════════════════════════════════
# GATEWAY
# ═══════════════════════════════════════════════

class LLMGateway:
    def __init__(self, settings: Settings, redis: Redis) -> None:
        self._settings = settings
        self._redis = redis
        self._cb_fail_max = settings.circuit_breaker.fail_max
        self._cb_reset_timeout = settings.circuit_breaker.reset_timeout_seconds

        self._groq = GroqClient(settings.groq)
        self._openrouter = OpenRouterClient(settings.openrouter)

        self._groq_instructor = instructor.from_groq(
            AsyncGroq(api_key=settings.groq.api_key),
            mode=instructor.Mode.JSON,
        )
        self._openrouter_instructor = instructor.from_openai(
            AsyncOpenAI(
                api_key=settings.openrouter.api_key,
                base_url=settings.openrouter.base_url,
            ),
            mode=instructor.Mode.JSON,
        )

    # ═══════════════════════════════════════════
    # MAIN CHAT — Structured Output (1 call = think + generate)
    # ═══════════════════════════════════════════

    async def generate_chat(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 150,
        temperature: float = 0.85,
    ) -> ChatResponse:

        # ─── Try Groq ───
        if not await self._is_circuit_open("groq"):
            result = await self._try_instructor(
                provider="groq",
                client=self._groq_instructor,
                model=self._settings.groq.model_fast,
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=ChatResponse,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self._settings.groq.timeout_seconds,
            )
            if result is not None:
                await self._record_success("groq")
                return result
            await self._record_failure("groq")

        # ─── Fallback: OpenRouter ───
        if not await self._is_circuit_open("openrouter"):
            result = await self._try_instructor(
                provider="openrouter",
                client=self._openrouter_instructor,
                model=self._settings.openrouter.default_model,
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=ChatResponse,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=self._settings.openrouter.timeout_seconds,
            )
            if result is not None:
                await self._record_success("openrouter")
                return result
            await self._record_failure("openrouter")

        # ─── Both providers down ───
        logger.error("all_providers_down")
        return ChatResponse(should_respond=False, response_text="", mood="neutral")

    # ═══════════════════════════════════════════
    # VISION — Direct to OpenRouter
    # ═══════════════════════════════════════════

    async def generate_vision(
        self,
        system_prompt: str,
        user_text: str,
        image_url: str,
        max_tokens: int = 150,
    ) -> LLMResponse:
        result = await self._openrouter.generate_vision(
            system_prompt=system_prompt,
            user_text=user_text,
            image_url=image_url,
            max_tokens=max_tokens,
        )
        if result.success:
            return result

        logger.warning("vision_failed", error=result.error)
        return LLMResponse(
            success=False,
            content="",
            error="vision_unavailable",
            provider="openrouter",
        )

    # ═══════════════════════════════════════════
    # FREE-FORM TEXT — For lurker, simple responses
    # ═══════════════════════════════════════════

    async def generate_text(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 80,
        temperature: float = 0.9,
    ) -> LLMResponse:

        # ─── Try Groq ───
        if not await self._is_circuit_open("groq"):
            result = await self._groq.generate(
                system_prompt=system_prompt,
                user_content=user_content,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if result.success:
                await self._record_success("groq")
                return result
            await self._record_failure("groq")

        # ─── Fallback: OpenRouter ───
        if not await self._is_circuit_open("openrouter"):
            result = await self._openrouter.generate(
                system_prompt=system_prompt,
                user_content=user_content,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if result.success:
                await self._record_success("openrouter")
                return result
            await self._record_failure("openrouter")

        logger.error("all_providers_down_text")
        return LLMResponse(success=False, error="all_down", provider="none")

    # ═══════════════════════════════════════════
    # PROVIDER STATUS (for /status command)
    # ═══════════════════════════════════════════

    async def get_provider_status(self) -> dict:
        groq_state = await rc.get_circuit_state(self._redis, "groq")
        openrouter_state = await rc.get_circuit_state(self._redis, "openrouter")
        return {
            "groq": {"circuit": groq_state},
            "openrouter": {"circuit": openrouter_state},
        }

    # ═══════════════════════════════════════════
    # INSTRUCTOR WRAPPER
    # ═══════════════════════════════════════════

    async def _try_instructor(
        self,
        provider: str,
        client,
        model: str,
        system_prompt: str,
        user_content: str,
        response_model: type[T],
        max_tokens: int,
        temperature: float,
        timeout: float,
    ) -> T | None:
        import asyncio

        try:
            result = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    max_retries=1,
                ),
                timeout=timeout,
            )
            logger.debug(
                "instructor_success",
                provider=provider,
                model=model,
            )
            return result

        except asyncio.TimeoutError:
            logger.warning("instructor_timeout", provider=provider, timeout=timeout)
            return None
        except Exception as error:
            logger.warning(
                "instructor_failed",
                provider=provider,
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return None

    # ═══════════════════════════════════════════
    # CIRCUIT BREAKER (Async, Redis-backed)
    # ═══════════════════════════════════════════

    async def _is_circuit_open(self, provider: str) -> bool:
        state = await rc.get_circuit_state(self._redis, provider)
        if state == "open":
            logger.debug("circuit_open_skipping", provider=provider)
            return True
        return False

    async def _record_success(self, provider: str) -> None:
        await rc.reset_circuit_failures(self._redis, provider)
        current_state = await rc.get_circuit_state(self._redis, provider)
        if current_state != "closed":
            await rc.set_circuit_state(self._redis, provider, "closed")
            logger.info("circuit_closed", provider=provider)

    async def _record_failure(self, provider: str) -> None:
        count = await rc.increment_circuit_failures(self._redis, provider)
        if count >= self._cb_fail_max:
            await rc.set_circuit_state(
                self._redis, provider, "open", ttl_seconds=self._cb_reset_timeout,
            )
            logger.warning(
                "circuit_opened",
                provider=provider,
                failures=count,
                reset_in=self._cb_reset_timeout,
            )