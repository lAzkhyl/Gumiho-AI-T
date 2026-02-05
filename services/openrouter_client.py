from __future__ import annotations

import asyncio

import structlog
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from config import OpenRouterConfig
from services.groq_client import LLMResponse

logger = structlog.get_logger("twomoon.openrouter")


# ═══════════════════════════════════════════════
# OPENROUTER CLIENT
# ═══════════════════════════════════════════════

class OpenRouterClient:
    def __init__(self, config: OpenRouterConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def generate(
        self,
        system_prompt: str,
        user_content: str,
        model: str | None = None,
        max_tokens: int = 150,
        temperature: float = 0.85,
    ) -> LLMResponse:
        target_model = model or self._config.default_model

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        return await self._call(messages, target_model, max_tokens, temperature)

    async def generate_vision(
        self,
        system_prompt: str,
        user_text: str,
        image_url: str,
        model: str | None = None,
        max_tokens: int = 150,
        temperature: float = 0.85,
    ) -> LLMResponse:
        target_model = model or self._config.vision_model

        user_content = [
            {"type": "text", "text": user_text or "describe this image briefly, react naturally"},
        ]
        user_content.append({
            "type": "image_url",
            "image_url": {"url": image_url},
        })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        result = await self._call(messages, target_model, max_tokens, temperature)

        if not result.success and target_model == self._config.vision_model:
            logger.warning("vision_primary_failed", fallback=self._config.vision_fallback_model)
            return await self._call(
                messages, self._config.vision_fallback_model, max_tokens, temperature,
            )

        return result

    async def generate_raw_messages(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 150,
        temperature: float = 0.85,
    ) -> LLMResponse:
        target_model = model or self._config.default_model
        return await self._call(messages, target_model, max_tokens, temperature)

    # ═══════════════════════════════════════════
    # INTERNAL
    # ═══════════════════════════════════════════

    async def _call(
        self,
        messages: list[dict],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        try:
            completion = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=0.95,
                ),
                timeout=self._config.timeout_seconds,
            )

            text = completion.choices[0].message.content if completion.choices else ""
            usage = completion.usage

            return LLMResponse(
                success=True,
                content=text or "",
                provider="openrouter",
                model=model,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            )

        except RateLimitError as error:
            logger.warning("openrouter_rate_limited", model=model)
            return LLMResponse(
                success=False,
                error="rate_limited",
                provider="openrouter",
                model=model,
                is_rate_limited=True,
            )

        except (APITimeoutError, asyncio.TimeoutError):
            logger.warning("openrouter_timeout", model=model, timeout=self._config.timeout_seconds)
            return LLMResponse(
                success=False,
                error="timeout",
                provider="openrouter",
                model=model,
                is_timeout=True,
            )

        except APIError as error:
            status = getattr(error, "status_code", 0)
            logger.error("openrouter_api_error", model=model, status=status)
            return LLMResponse(
                success=False,
                error=f"api_error:{status}",
                provider="openrouter",
                model=model,
            )

        except Exception as error:
            logger.error("openrouter_unexpected", model=model, error=str(error))
            return LLMResponse(
                success=False,
                error=f"unexpected:{type(error).__name__}",
                provider="openrouter",
                model=model,
            )