from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog
from groq import AsyncGroq, APIError, APITimeoutError, RateLimitError

from config import GroqConfig

logger = structlog.get_logger("twomoon.groq")


# ═══════════════════════════════════════════════
# RESPONSE MODEL
# ═══════════════════════════════════════════════

@dataclass(slots=True)
class LLMResponse:
    success: bool
    content: str = ""
    error: str = ""
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    is_rate_limited: bool = False
    is_timeout: bool = False


# ═══════════════════════════════════════════════
# GROQ CLIENT
# ═══════════════════════════════════════════════

class GroqClient:
    def __init__(self, config: GroqConfig) -> None:
        self._config = config
        self._client = AsyncGroq(api_key=config.api_key)

    async def generate(
        self,
        system_prompt: str,
        user_content: str,
        use_smart: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        model = self._config.model_smart if use_smart else self._config.model_fast
        tokens = max_tokens or self._config.max_tokens
        temp = temperature or self._config.temperature

        try:
            completion = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=tokens,
                    temperature=temp,
                    top_p=0.95,
                    frequency_penalty=0.4,
                    presence_penalty=0.3,
                ),
                timeout=self._config.timeout_seconds,
            )

            text = completion.choices[0].message.content if completion.choices else ""
            usage = completion.usage

            return LLMResponse(
                success=True,
                content=text or "",
                provider="groq",
                model=model,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            )

        except RateLimitError as error:
            logger.warning("groq_rate_limited", error=str(error))
            return LLMResponse(
                success=False,
                error="rate_limited",
                provider="groq",
                model=model,
                is_rate_limited=True,
            )

        except (APITimeoutError, asyncio.TimeoutError) as error:
            logger.warning("groq_timeout", timeout=self._config.timeout_seconds)
            return LLMResponse(
                success=False,
                error="timeout",
                provider="groq",
                model=model,
                is_timeout=True,
            )

        except APIError as error:
            logger.error("groq_api_error", status=error.status_code, error=str(error))
            return LLMResponse(
                success=False,
                error=f"api_error:{error.status_code}",
                provider="groq",
                model=model,
            )

        except Exception as error:
            logger.error("groq_unexpected_error", error=str(error))
            return LLMResponse(
                success=False,
                error=f"unexpected:{type(error).__name__}",
                provider="groq",
                model=model,
            )

    async def generate_raw_messages(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        target_model = model or self._config.model_fast
        tokens = max_tokens or self._config.max_tokens
        temp = temperature or self._config.temperature

        try:
            completion = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    max_tokens=tokens,
                    temperature=temp,
                    top_p=0.95,
                    frequency_penalty=0.4,
                    presence_penalty=0.3,
                ),
                timeout=self._config.timeout_seconds,
            )

            text = completion.choices[0].message.content if completion.choices else ""
            usage = completion.usage

            return LLMResponse(
                success=True,
                content=text or "",
                provider="groq",
                model=target_model,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
            )

        except (RateLimitError, APITimeoutError, asyncio.TimeoutError, APIError) as error:
            is_rl = isinstance(error, RateLimitError)
            is_to = isinstance(error, (APITimeoutError, asyncio.TimeoutError))
            logger.warning("groq_raw_error", error_type=type(error).__name__)
            return LLMResponse(
                success=False,
                error=type(error).__name__,
                provider="groq",
                model=target_model,
                is_rate_limited=is_rl,
                is_timeout=is_to,
            )

        except Exception as error:
            logger.error("groq_raw_unexpected", error=str(error))
            return LLMResponse(
                success=False,
                error=f"unexpected:{type(error).__name__}",
                provider="groq",
                model=target_model,
            )