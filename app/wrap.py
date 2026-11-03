"""Обёртки MCP-инструментов для агентов: вызов с JWT + учёт трейса.

Готовая основа: MCP-инструмент превращается в FunctionTool агента;
субъект вызова в Bearer-токене соединения; кривой JSON аргументов —
машинно-читаемый ответ модели, сессия не роняется; ошибка MCP-сервера
(MCPError, код + data с подсказками) — JSON tool-результат для модели.

Крючок prepare_*: успешная подготовка создаёт карточку подтверждения,
модель получает «карточка готова, спроси да/нет» и исполнить сама не может.
Пока в контрактах есть sub, обёртка прячет его из схемы и подставляет сама.

Каждый tool-вызов модели пишется в tool_calls (исход, задержка, обрезанные
аргументы), включая вызовы с кривыми аргументами; тикет — ещё и в аудит.

TODO(права): два барьера —
  wrap_tools: фильтровать имена через permissions.visible_tools
  (барьер 1: модель не видит запрещённого);
  invoke: permissions.admit(session.allowlist, name, args) перед вызовом
  (барьер 2: отказ → {"error": "denied", "reason"} + запись в аудит);
  подготовленную карточку проводить через pep.prepare — Deny → карточки
  нет, модель получает отказ с reason.
"""
from __future__ import annotations

import json
import time

from agents import FunctionTool
from agents.run_context import RunContextWrapper
from mcp.shared.exceptions import MCPError

from app import audit, cards
from core import db
from app.sessions import Session


def _schema_without_sub(schema: dict) -> dict:
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k != "sub"}
    required = [k for k in schema.get("required", []) if k != "sub"]
    return {**schema, "properties": props, "required": required}


def _card_reply(session: Session, name: str, result) -> dict:
    """Успешный prepare_* → карточка; модели — только просьба спросить «да/нет»."""
    if not (name.startswith("prepare_") and isinstance(result, dict) and result.get("ok")):
        return result
    card = cards.create(session, result["action"], result["details"], result["view"])
    return {
        "card_created": True,
        "card": card.public_view(ttl_sec=120),
        "next": "перескажи карточку и спроси подтверждение «да/нет»; исполнить сам ты не можешь",
    }


def wrap_mcp_tool(session: Session, name: str) -> FunctionTool:
    """Обёртка одного MCP-инструмента для агента этой сессии."""
    schema = session.pool.tool_schema(name)
    needs_sub = "sub" in (schema.get("properties") or {})

    async def invoke(ctx: RunContextWrapper, input_json: str) -> str:
        started = time.perf_counter()

        def log(args, outcome: str) -> None:
            db.execute(
                "insert into tool_calls(trace_id, tool, args, outcome, latency_ms) values (%s, %s, %s, %s, %s)",
                (session.trace_id or None, name, json.dumps(args, ensure_ascii=False)[:400], outcome[:60],
                 int((time.perf_counter() - started) * 1000)),
            )

        try:
            args = json.loads(input_json) if input_json and input_json.strip() else {}
        except (TypeError, ValueError):
            log({"raw": str(input_json)[:200]}, "bad_arguments")
            return json.dumps(
                {"error": "bad_arguments", "detail": "аргументы — не JSON; повтори вызов по схеме"},
                ensure_ascii=False,
            )
        if needs_sub:
            args["sub"] = session.user.sub
        session.note(name)
        try:
            result = await session.pool.call(session.pool.resource_of(name), name, args)
        except MCPError as e:
            data = e.data if isinstance(e.data, dict) else {}
            log(args, str(getattr(e, "message", "tool_error")))
            return json.dumps(
                {"error": getattr(e, "message", "tool_failed")[:60], **data},
                ensure_ascii=False,
            )
        except Exception as e:  # noqa: BLE001 — сбой сервера становится данными для модели
            log(args, "tool_failed")
            return json.dumps({"error": "tool_failed", "detail": str(e)[:160]}, ensure_ascii=False)
        log(args, "ok")
        if name == "create_ticket":
            audit.append(session.user.sub, name, "escalated", trace_id=session.trace_id,
                         session_id=session.session_id, detail=result.get("ticket_id"))
        return json.dumps(_card_reply(session, name, result), ensure_ascii=False)

    return FunctionTool(
        name=name,
        description=session.pool.tool_description(name),
        params_json_schema=_schema_without_sub(schema) if needs_sub else schema,
        on_invoke_tool=invoke,
    )


def wrap_tools(session: Session, names: list[str]) -> list[FunctionTool]:
    """Собрать обёртки для перечня имён (какие имена давать — решает код)."""
    known = set(session.pool.tool_names())
    return [wrap_mcp_tool(session, n) for n in names if n in known]
