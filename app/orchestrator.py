"""Оркестратор хода: план → гейт → доменные агенты → редактор → SSE.

Ход — один запрос абонента; оркестратор управляет запуском агентов и
собирает события хода для фронтенда.

TODO(контракты) — карточка действия:
  - в начале хода: cards.active(session) → cards.classify_answer(message):
    «да» → execute_pending, «нет» → settle("rejected") + «отменяю»,
    «оператор» → escalate, смена темы → обычный ход с живой карточкой;
  - после агентов: свежая карточка → событие {"type": "card"} и ход
    завершается (ждём подтверждение);
  - execute_pending(session, card): settle("confirmed"), ключ
    f"{action}:{pending_id}", idempotency.execute → вызов MCP execute_action
    → человекочитаемый ответ (executed / duplicate / daily_limit / отказ).

TODO(аудит) — аудит: audit.append на все исходы пути действия
  (executed с before/after, duplicate_suppressed, rejected_by_user,
  server_limit, escalated).

TODO(права) — права: «да» → PEP: pep.confirm → pep.execute; при Deny —
  человекочитаемый отказ по reason (session_limit, confirmation_expired,
  card_not_prepared, pdp_unavailable).

TODO(guardrail) — guardrail: input-проверка реплики до всего хода (unsafe →
  отказ, запись в аудит); output-проверка ответа и карточки до показа;
  недоступный сервис → session.guard_state["ok"] = False (fail-closed
  для действий), чтение продолжается.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from app import planner
from app.editor import build_editor, editor_task
from app.llm import run_agent
from app.workers import build_workers

Emit = Callable[[dict], Awaitable[None]]


def _sources(runs: list[dict]) -> list[str]:
    """Источники kb из результатов search_kb."""
    found = set()
    for run in runs:
        for call in run["tools"]:
            result = call["result"]
            if isinstance(result, dict):
                found.update(h["source"] for h in result.get("results", []) if isinstance(h, dict) and h.get("source"))
    return sorted(found)


async def run_turn(session, message: str, emit: Emit) -> None:
    session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"

    if session.planner is None:
        session.planner = planner.build_planner()
    raw, session.thread = await planner.next_plan(session.planner, session.thread, message)
    plan, dropped = planner.gate(raw)
    for reason in dropped:
        session.note(f"plan_dropped:{reason}")
    await emit({
        "type": "plan",
        "agents": [f"{s.agent}: {s.task}" for s in plan.steps] or ["без агентов"],
        "dropped": dropped,
    })

    if plan.question and not plan.steps:
        await emit({"type": "reply", "text": plan.question, "sources": []})
        return

    runs = []
    if plan.steps:
        workers = build_workers(session, tuple(dict.fromkeys(s.agent for s in plan.steps)))
        for step in plan.steps:
            run = await run_agent(workers[step.agent], step.task, trace_id=session.trace_id)
            runs.append({"agent": step.agent, "task": step.task, **run})

    editor = await run_agent(build_editor(session), editor_task(message, runs), trace_id=session.trace_id)
    await emit({"type": "reply", "text": editor["report"], "sources": _sources(runs)})
