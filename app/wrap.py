"""Обёртки MCP-инструментов для агентов: вызов с JWT + учёт трейса.

Готовая основа: MCP-инструмент превращается в FunctionTool агента;
субъект вызова в Bearer-токене соединения; кривой JSON аргументов —
машинно-читаемый ответ модели, сессия не роняется; ошибка MCP-сервера
(MCPError, код + data с подсказками) — JSON tool-результат для модели.

Крючок prepare_*: успешная подготовка создаёт карточку подтверждения,
модель получает «карточка готова, спроси да/нет» и исполнить сама не может.

Барьер 1: wrap_tools собирает только инструменты allowlist сессии.
Барьер 2: admit перед каждым вызовом; отказ — модели и в аудит.

Каждый tool-вызов модели пишется в tool_calls (исход, задержка, обрезанные
аргументы), включая вызовы с кривыми аргументами; тикет — ещё и в аудит.

TODO(права): подготовленную карточку проводить через pep.prepare — Deny →
  карточки нет, модель получает отказ с reason.
"""
from __future__ import annotations

import json
import time

from agents import FunctionTool
from agents.run_context import RunContextWrapper
from mcp.shared.exceptions import MCPError

from app import audit, cards, permissions
from core import db
from app.sessions import Session


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
        decision = permissions.admit(session.allowlist, name, args)
        if not decision.allowed:
            log(args, f"denied:{decision.reason}")
            audit.append(session.user.sub, name, "denied", trace_id=session.trace_id,
                         session_id=session.session_id, args=args, detail=f"barrier2 {decision.reason}")
            return json.dumps(
                {"error": "denied", "reason": decision.reason,
                 "detail": "операция недоступна этому пользователю; можно предложить оператора"},
                ensure_ascii=False,
            )
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
        params_json_schema=session.pool.tool_schema(name),
        on_invoke_tool=invoke,
    )


def wrap_tools(session: Session, names: list[str]) -> list[FunctionTool]:
    """Барьер 1: обёртки только для известных и разрешённых сессии инструментов."""
    visible = set(permissions.visible_tools(session.allowlist, session.pool.tool_names()))
    return [wrap_mcp_tool(session, n) for n in names if n in visible]
