"""Пул из двух LLM-провайдеров с переключением, встроен на уровне клиента SDK.

429, 5xx, таймаут и сетевая ошибка — к следующему провайдеру, упавший выпадает
на LLM_FAILOVER_COOLDOWN_SEC. 400/401/403 не повторяются.
"""
from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass
from types import SimpleNamespace

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from core import config


@dataclass
class Provider:
    name: str
    model: str
    client: AsyncOpenAI
    down_until: float = 0.0

    def live(self) -> bool:
        return time.monotonic() >= self.down_until


PROVIDERS: list[Provider] = []
# dict, а не значение: дочерние задачи SDK копируют контекст, но пишут в тот же dict
_used: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_provider", default=None)


def _retryable(e: Exception) -> bool:
    if isinstance(e, (APITimeoutError, APIConnectionError)):
        return True
    return isinstance(e, APIStatusError) and (e.status_code == 429 or e.status_code >= 500)


async def call(fn):
    """fn(provider) → корутина запроса; переключение по политике выше."""
    candidates = [p for p in PROVIDERS if p.live()] or PROVIDERS[:1]
    last: Exception | None = None
    for provider in candidates:
        try:
            result = await fn(provider)
        except Exception as e:
            if not _retryable(e):
                raise
            provider.down_until = time.monotonic() + config.LLM_FAILOVER_COOLDOWN_SEC
            last = e
            continue
        if (box := _used.get()) is not None:
            box["provider"] = provider
        return result
    raise last


def tracking() -> dict:
    """Перед запуском агента: в box["provider"] окажется фактический провайдер."""
    box: dict = {}
    _used.set(box)
    return box


def enabled() -> bool:
    return len(PROVIDERS) > 1


def _client(base_url: str, api_key: str) -> AsyncOpenAI:
    # max_retries=0: повторы решает пул, а не встроенный ретрай SDK
    return AsyncOpenAI(base_url=base_url, api_key=api_key or "none", max_retries=0,
                       http_client=httpx.AsyncClient(trust_env=False, timeout=180))


def setup_pool() -> str:
    """Два провайдера из .env и клиент-переключатель по умолчанию для SDK."""
    from agents import (
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )

    PROVIDERS[:] = [
        Provider("primary", config.LLM_MODEL, _client(config.LLM_BASE_URL, config.LLM_API_KEY)),
        Provider("secondary", config.LLM_SECONDARY_MODEL,
                 _client(config.LLM_SECONDARY_BASE_URL, config.LLM_SECONDARY_API_KEY)),
    ]

    async def create(**kwargs):
        return await call(lambda p: p.client.chat.completions.create(**{**kwargs, "model": p.model}))

    front = _client(config.LLM_BASE_URL, config.LLM_API_KEY)
    front.__dict__["chat"] = SimpleNamespace(completions=SimpleNamespace(create=create))
    set_default_openai_client(front, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)
    config.MODEL_NAME = config.LLM_MODEL
    return config.MODEL_NAME
