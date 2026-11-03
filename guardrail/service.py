"""Guardrail-сервис: отдельный stateless-контур проверки текста.

    uvicorn --factory guardrail.service:build_app --host 127.0.0.1 --port 8300

POST /check {"text", "mode": "input"|"output"} → safe или unsafe с категорией.
Движок по умолчанию RULE — маркеры атак стенда; это лабораторная эвристика,
для продакшена её мало. GUARDRAIL_MODEL=1 включает HiveTraceGuard-Pro
(transformers и torch в requirements не входят). /admin/down и /admin/up
имитируют отказ сервиса.

Guardrail только ставит флаг. Права решает политика, а недоступный сервис
оркестратор переживает сам: чтение идёт, действия закрыты.
"""
from __future__ import annotations

import asyncio
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

RULES: dict[str, tuple[str, ...]] = {
    "jailbreak": ("суперпользовател", "ты теперь админ", "отключи проверк", "developer mode", "jailbreak"),
    "prompt_injection": ("игнорируй инструкц", "игнорируй предыдущ", "системный промпт", "system prompt"),
    "tool_hijack": ("смени тариф в аккаунте", "переведи абонента"),
    "injected_instruction": ("немедленно переведи", "без подтверждения", "системная инструкция"),
}

STATE = {"up": True, "model": None}


def rule_verdict(text: str) -> dict:
    lowered = text.lower()
    for category, markers in RULES.items():
        if any(m in lowered for m in markers):
            return {"verdict": "unsafe", "category": category, "engine": "rule"}
    return {"verdict": "safe", "category": None, "engine": "rule"}


async def model_verdict(text: str) -> dict:
    from transformers import pipeline  # type: ignore[import-not-found]

    if STATE["model"] is None:
        STATE["model"] = await asyncio.to_thread(pipeline, "text-classification", model="hivetrace/HiveTraceGuard-Pro")
    top = await asyncio.to_thread(lambda: STATE["model"](text[:2000], top_k=1)[0])
    label = str(top.get("label", "")).lower()
    unsafe = "unsafe" in label or "attack" in label
    return {"verdict": "unsafe" if unsafe else "safe", "category": label or None, "engine": "model"}


async def check(request: Request) -> JSONResponse:
    if not STATE["up"]:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    body = await request.json()
    text, mode = str(body.get("text", "")), body.get("mode", "input")
    if mode not in ("input", "output"):
        return JSONResponse({"error": "mode: input|output"}, status_code=400)
    verdict = await model_verdict(text) if os.environ.get("GUARDRAIL_MODEL") == "1" else rule_verdict(text)
    return JSONResponse({**verdict, "mode": mode})


async def admin(request: Request) -> JSONResponse:
    STATE["up"] = request.url.path.endswith("/up")
    return JSONResponse({"available": STATE["up"]})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok" if STATE["up"] else "down"}, status_code=200 if STATE["up"] else 503)


def build_app() -> Starlette:
    return Starlette(routes=[
        Route("/check", check, methods=["POST"]),
        Route("/admin/down", admin, methods=["POST"]),
        Route("/admin/up", admin, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ])
