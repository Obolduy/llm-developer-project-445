"""Аудит: только append и чтение, текст реплик не пишется."""
from __future__ import annotations

import json

from core import db

OUTCOMES = frozenset({
    "executed", "duplicate_suppressed", "rejected_by_user", "rejected_by_contract",
    "expired", "escalated", "denied", "guardrail_unsafe", "guardrail_degraded",
    "token_refreshed", "server_limit", "budget_exhausted", "error",
})


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def append(who: str, what: str, outcome: str, *, trace_id=None, session_id=None, args=None,
           before=None, after=None, idempotency_key=None, detail=None) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"неизвестный исход аудита: {outcome}")
    db.execute(
        "insert into audit_log(trace_id, session_id, who, what, outcome, args, before, after,"
        " idempotency_key, detail) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (trace_id, session_id, who, what, outcome, _json(args), _json(before), _json(after),
         idempotency_key, detail),
    )


def tail(n: int = 20, who: str | None = None) -> list[dict]:
    if who:
        return db.fetchall("select * from audit_log where who = %s order by id desc limit %s", (who, n))
    return db.fetchall("select * from audit_log order by id desc limit %s", (n,))


def trace(trace_id: str) -> list[dict]:
    """Хронология хода: аудит, запуски агентов и вызовы инструментов вперемешку по времени."""
    return db.fetchall(
        "select ts, 'audit' as kind, what as name, outcome, detail from audit_log where trace_id = %(t)s"
        " union all select ts, 'llm', agent, model, concat(input_tokens, '/', output_tokens, ' tok, ',"
        "  latency_ms, ' ms') from llm_calls where trace_id = %(t)s"
        " union all select ts, 'tool', tool, outcome, concat(latency_ms, ' ms') from tool_calls"
        "  where trace_id = %(t)s"
        " order by ts",
        {"t": trace_id},
    )
