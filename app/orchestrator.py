"""Оркестратор хода: карточка → план → гейт → доменные агенты → редактор → SSE.

Ход — один запрос абонента. Пока жива карточка подтверждения, ответ на
неё разбирает код без LLM: «да» исполняет, «нет» отменяет, «оператор»
эскалирует, смена темы идёт обычным ходом, и карточка остаётся живой.

«Да» проходит PDP дважды: ConfirmCard той же карточки, затем Execute
с лимитом. Решает политика, оркестратор только переводит reason на
человеческий язык.

Guardrail проверяет текст дважды: реплику до LLM и ответ с карточкой до
показа. Недоступный guardrail ставит guard_state["ok"] = False: чтение
продолжается, а действия закрывает политика (guard_not_ok).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable

from app import audit, cards, guardrail_client, idempotency, planner
from app.editor import build_editor, editor_task
from app.llm import run_agent
from app.sessions import pep_of
from app.workers import build_workers

Emit = Callable[[dict], Awaitable[None]]

CANCELLED = "Хорошо, отменяю. Ничего не изменено."
UNSAFE = "Не могу выполнить эту просьбу. Если нужен человек — скажите, и я позову оператора."

DENIED = {
    "confirmation_expired": "Подтверждение истекло — попросите операцию заново, я подготовлю новую карточку.",
    "card_not_prepared": "Карточка не найдена — попросите операцию заново.",
    "session_limit": "Лимит исчерпан: в одной сессии можно сменить тариф не больше двух раз. Могу позвать оператора.",
    "pdp_unavailable": "Действия сейчас недоступны: не работает проверка прав. "
                       "На вопросы отвечу, могу позвать оператора.",
    "guard_not_ok": "Действия сейчас недоступны: не работает проверка безопасности. "
                    "На вопросы отвечу, могу позвать оператора.",
}


def _sources(runs: list[dict]) -> list[str]:
    """Источники kb из результатов search_kb."""
    found = set()
    for run in runs:
        for call in run["tools"]:
            result = call["result"]
            if isinstance(result, dict):
                found.update(h["source"] for h in result.get("results", []) if isinstance(h, dict) and h.get("source"))
    return sorted(found)


def _guard_audit(session, mode: str, outcome: str, detail: str = "") -> None:
    audit.append(session.user.sub, f"guardrail:{mode}", outcome, trace_id=session.trace_id,
                 session_id=session.session_id, detail=detail or None)


async def run_turn(session, message: str, emit: Emit) -> None:
    session.trace_id = f"tr_{uuid.uuid4().hex[:12]}"

    verdict = await guardrail_client.check(message, mode="input")
    session.guard_state["ok"] = verdict.available and verdict.safe  # тот же dict читает PEP
    if not verdict.available:
        _guard_audit(session, "input", "guardrail_degraded", "чтение продолжено, действия закрыты")
    elif not verdict.safe:
        _guard_audit(session, "input", "guardrail_unsafe", verdict.category)
        await emit({"type": "reply", "text": UNSAFE, "sources": []})
        return

    card = cards.active(session)
    if card is not None and await _answer_card(session, card, message, emit):
        return

    if session.planner is None:
        session.planner = planner.build_planner()
    raw, session.thread = await planner.next_plan(
        session.planner, session.thread, message, trace_id=session.trace_id
    )
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

    runs, fresh = [], None
    if plan.steps:
        workers = build_workers(session, tuple(dict.fromkeys(s.agent for s in plan.steps)))
        for step in plan.steps:
            run = await run_agent(workers[step.agent], step.task, trace_id=session.trace_id)
            runs.append({"agent": step.agent, "task": step.task, **run})
            now = cards.active(session)
            if now is not None and now is not card:
                fresh = now
                break  # карточка создана — дальше ждём подтверждения

    editor = await run_agent(build_editor(session), editor_task(message, runs), trace_id=session.trace_id)

    shown = editor["report"] + (f"\n{json.dumps(fresh.view, ensure_ascii=False)}" if fresh else "")
    out = await guardrail_client.check(shown, mode="output")
    if out.available and not out.safe:
        if fresh is not None:
            cards.settle(session, fresh, "denied")
        _guard_audit(session, "output", "guardrail_unsafe", out.category)
        await emit({"type": "reply", "text": UNSAFE, "sources": []})
        return
    if not out.available:
        _guard_audit(session, "output", "guardrail_degraded", "ответ показан без проверки")
    if fresh is not None:
        await emit({"type": "card", "pending_id": fresh.pending_id, "card": fresh.public_view(ttl_sec=120)})
    await emit({"type": "reply", "text": editor["report"], "sources": _sources(runs)})


async def _answer_card(session, card, message: str, emit: Emit) -> bool:
    """Ответ на живую карточку разбирает код; True — ход обслужен без LLM."""
    answer = cards.classify_answer(message)
    if answer == "topic":
        return False
    await emit({"type": "plan", "agents": [f"ответ на карточку: {answer}"], "dropped": []})
    if answer == "yes":
        text = await execute_pending(session, card)
    elif answer == "no":
        reject(session, card)
        text = CANCELLED
    else:
        cards.settle(session, card, "rejected")
        text = await escalate(session, card, "абонент попросил оператора")
    await emit({"type": "reply", "text": text, "sources": []})
    return True


def _audit(session, card, outcome: str, **kw) -> None:
    audit.append(card.sub, card.action, outcome, trace_id=session.trace_id,
                 session_id=session.session_id, **kw)


def reject(session, card) -> None:
    cards.settle(session, card, "rejected")
    _audit(session, card, "rejected_by_user", args=card.details)


async def execute_pending(session, card) -> str:
    """«Да» → ConfirmCard → Execute → исполнение под ключом идемпотентности."""
    pep = pep_of(session)
    for stage, ask in (("ConfirmCard", pep.confirm), ("Execute", pep.execute)):
        decision = await ask(card)
        if not decision.allowed:
            expired = decision.reason in ("confirmation_expired", "card_not_prepared")
            cards.settle(session, card, "expired" if expired else "denied")
            _audit(session, card, "denied", args=card.details, detail=f"pdp {stage} {decision.reason}")
            return DENIED.get(decision.reason, f"Действие не прошло проверку прав: {decision.reason}.")
    cards.settle(session, card, "confirmed")
    key = f"{card.action}:{card.pending_id}"
    stored = await idempotency.execute(
        key, card.sub, card.action,
        lambda: session.pool.call("actions", "execute_action", {
            "action": card.action,
            "details_json": json.dumps(card.details, ensure_ascii=False),
            "pending_id": card.pending_id,
            "idempotency_key": key,
        }),
    )
    result = stored.result
    outcome = result.get("outcome") or result.get("error") or "error"
    args = {**card.details, "pending_id": card.pending_id}
    if stored.duplicate or outcome == "duplicate":
        _audit(session, card, "duplicate_suppressed", args=args, idempotency_key=key)
        return "Эта операция уже исполнена — повтор не выполнялся."
    if outcome in idempotency.SUCCESS:
        _audit(session, card, "executed", args=args, idempotency_key=key,
               before=result.get("before"), after=result.get("after"))
        return f"Готово, операция «{card.view.get('что', card.action)}» выполнена."
    if outcome == "daily_limit":
        _audit(session, card, "server_limit", args=args, detail="billing daily_limit")
        return "Биллинг отклонил операцию: дневной лимит смен тарифа исчерпан. Могу позвать оператора."
    _audit(session, card, "rejected_by_contract", args=args, detail=outcome)
    return f"Операция не выполнена: {outcome}."


async def escalate(session, card, reason: str) -> str:
    result = await session.pool.call("actions", "create_ticket", {
        "subject": f"эскалация: {card.view.get('что', card.action)}",
        "note": f"{reason}; карточка: {json.dumps(card.view, ensure_ascii=False)}",
    })
    ticket = result.get("ticket_id", "?")
    _audit(session, card, "escalated", args={"reason": reason}, detail=ticket)
    return f"Передаю диалог оператору, тикет {ticket}."
