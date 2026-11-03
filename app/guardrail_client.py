"""Клиент guardrail-сервиса: вердикт вероятностный, решения — за кодом.

Недоступный сервис — не исключение: Verdict(safe=True, available=False).
Чтение продолжается, а действия закрывает политика флагом guard_ok.
"""
from __future__ import annotations

from dataclasses import dataclass

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from core import config


@dataclass
class Verdict:
    safe: bool
    category: str = ""
    available: bool = True


async def check(text: str, mode: str = "input") -> Verdict:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            r = await client.post(f"{config.GUARDRAIL_URL}/check", json={"text": text, "mode": mode})
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return Verdict(safe=True, available=False)
    return Verdict(safe=body.get("verdict") == "safe", category=body.get("category") or "")
